"""Common source adapter interface: availability, check, batched fetch, retries and error types."""
import email.utils
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import ClassVar, Literal

import httpx

log = logging.getLogger(__name__)

Availability = Literal['ready', 'not_configured', 'manual_only', 'no_access']
pause = time.sleep  # tests replace it


class SourceError(Exception):
    """Source failed; network errors and 5xx are worth retrying later."""
    retryable = True
    kind = 'network'


class SourceRateLimited(SourceError):
    kind = 'rate_limited'

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class SourceAccessDenied(SourceError):
    """401/403: the key does not work, retrying is useless."""
    retryable = False
    kind = 'access_denied'


class SourceNotConfigured(SourceError):
    retryable = False
    kind = 'not_configured'


class SourceDataError(SourceError):
    """Response does not match the expected schema."""
    retryable = False
    kind = 'data'


@dataclass(frozen=True)
class EventItem:
    event_type: str
    title: str
    external_id: str | None = None
    company_name: str | None = None
    inn: str | None = None
    ogrn: str | None = None
    source_company_id: str | None = None
    municipality: str | None = None
    address: str | None = None
    cadastral_number: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_precision: str | None = None  # exact | address | municipality
    event_date: date | None = None
    status: str | None = None
    value: Decimal | None = None
    value_unit: str | None = None
    metrics: tuple[dict, ...] = ()  # ({'name': 'work_places', 'value': 3, 'unit': 'чел.'},)
    url: str | None = None
    raw: dict = field(default_factory=dict)  # whitelisted source fields only, no personal data
    details_kind: str | None = None  # 'vacancy', 'land_lot', ...: extension record
    details: dict | None = None


@dataclass
class Batch:
    documents: list[dict] = field(default_factory=list)  # parse_feed format
    events: list[EventItem] = field(default_factory=list)
    cursor: dict | None = None  # saved after the batch is written
    snapshot_complete: bool = False  # last batch of a full export


def keep_fields(data, fields):
    """Whitelist of source fields for `raw`; dotted paths reach into nested objects."""
    result = {}
    for path in fields:
        value, target, parts = data, result, path.split('.')
        for part in parts:
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            continue
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result


def env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def retry_after_seconds(value):
    if not value:
        return None
    if value.strip().isdigit():
        return int(value.strip())
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0, int((moment - datetime.now(timezone.utc)).total_seconds()))


class SourceAdapter(ABC):
    kind: ClassVar[str]
    produces: ClassVar[Literal['documents', 'events']] = 'events'
    scheduled: ClassVar[bool] = True  # imports: False
    required_env: ClassVar[tuple[str, ...]] = ()
    retry_delay: ClassVar[float] = 1.0

    def __init__(self, source: dict, http: httpx.Client | None):
        self.source = source
        self.http = http
        self.options = source.get('options') or {}
        self.retries = 0

    def availability(self) -> Availability:
        """Without network: by env and config."""
        return 'not_configured' if self.missing_env() else 'ready'

    def missing_env(self):
        return [name for name in self.required_env if not os.environ.get(name, '').strip()]

    def not_ready_message(self):
        state = self.availability()
        if state == 'manual_only':
            return 'Источник пополняется только загрузкой файла.'
        if state == 'no_access':
            return 'Нет доступа к источнику: ' + (self.source.get('access') or 'нужно согласовать доступ.')
        missing = self.missing_env()
        return 'Требуется настройка: ' + (', '.join(missing) if missing else self.source.get('access') or 'не заданы параметры источника.')

    def check(self) -> tuple[bool, str]:
        """Cheap availability request (limit=1); writes nothing to the database."""
        if self.availability() != 'ready':
            return False, self.not_ready_message()
        return True, 'Адаптер готов к сбору.'

    @abstractmethod
    def fetch(self, cursor: dict | None) -> Iterator[Batch]:
        ...

    def snapshot_finished(self, db, started_at):
        """Called after the last batch of a full export: adapters may close records that disappeared."""

    def request_json(self, url, params=None, *, attempts=None, headers=None, timeout=None):
        attempts = attempts or env_int('SOURCE_RETRY_ATTEMPTS', 3)
        max_wait = env_int('SOURCE_MAX_RETRY_AFTER', 300)
        error = None
        for attempt in range(attempts):
            if attempt:
                self.retries += 1
                wait = error.retry_after if isinstance(error, SourceRateLimited) and error.retry_after is not None \
                    else self.retry_delay * 2 ** (attempt - 1)
                pause(min(wait, max_wait))
            try:
                kwargs = {'params': params, 'headers': headers}
                if timeout:
                    kwargs['timeout'] = timeout
                response = self.http.get(url, **kwargs)
            except httpx.TransportError as failure:
                error = SourceError(f'Нет связи с источником: {type(failure).__name__}')
                continue
            except httpx.HTTPError as failure:
                raise SourceError(f'Некорректный запрос к источнику: {type(failure).__name__}') from failure
            code = response.status_code
            if code in (401, 403):
                raise SourceAccessDenied(f'Источник отклонил запрос: HTTP {code}. Проверьте ключ или доступ.')
            if code == 429:
                wait = retry_after_seconds(response.headers.get('Retry-After'))
                error = SourceRateLimited(f'Превышен лимит запросов: HTTP 429' + (f', повтор через {wait} с' if wait else ''), wait)
                if wait is not None and wait > max_wait:
                    raise error
                continue
            if code >= 500:
                error = SourceError(f'HTTP {code}')
                continue
            if code >= 400:
                raise SourceDataError(f'HTTP {code}')
            try:
                return response.json()
            except ValueError as failure:
                raise SourceDataError('Источник вернул некорректный JSON.') from failure
        raise error
