import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from .net import http_client
from .risk import evaluate

log = logging.getLogger(__name__)
# httpx logs full request URLs at INFO, and Focus takes the key as a query parameter.
logging.getLogger('httpx').setLevel(logging.WARNING)

# Each method counts unique companies against its own limit (1000 per period on the pilot key).
METHODS = ['req', 'legalAnalytics', 'bankruptcyAnalytics', 'courtAnalytics', 'financeAnalytics', 'fsspAnalytics',
           'linkAnalytics', 'fnsBlockedBankAccounts', 'beneficialOwners', 'scoring']


class FocusError(Exception):
    pass


class FocusInputError(FocusError):
    pass


class FocusLimitExceeded(FocusError):
    pass


def valid_inn(inn):
    if not inn or not inn.isdigit() or len(inn) not in (10, 12):
        return False
    digits = [int(c) for c in inn]

    def check(weights):
        return sum(w * d for w, d in zip(weights, digits)) % 11 % 10
    if len(inn) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[9]
    return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[10]
            and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == digits[11])


@dataclass
class FocusSettings:
    base_url: str = 'https://focus-api.kontur.ru/api3'
    api_key: str = ''
    inn_limit: int = 950
    ttl_days: int = 30
    auto_check: bool = True

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(base_url=env('FOCUS_BASE_URL', cls.base_url).rstrip('/'), api_key=env('FOCUS_API_KEY', ''),
                   inn_limit=int(env('FOCUS_INN_LIMIT', cls.inn_limit)), ttl_days=int(env('FOCUS_TTL_DAYS', cls.ttl_days)),
                   auto_check=env('FOCUS_AUTO_CHECK', '1') == '1')

    @property
    def configured(self):
        return bool(self.api_key)


class FocusClient:
    def __init__(self, engine, settings=None, transport=None):
        self.engine = engine
        self.settings = settings or FocusSettings.from_env()
        self.http = http_client(transport, timeout=30.0)

    def usage(self):
        with self.engine.connect() as connection:
            used = connection.execute(text('SELECT count(*) FROM focus_checks')).scalar()
        return {'configured': self.settings.configured, 'used': used, 'limit': self.settings.inn_limit}

    def cached(self, inn):
        with self.engine.connect() as connection:
            row = connection.execute(text('SELECT result, checked_at FROM focus_checks WHERE inn = :inn'), {'inn': inn}).first()
        return row

    def check(self, inn, force=False):
        inn = (inn or '').strip()
        if not self.settings.configured:
            raise FocusError('Контур.Фокус не настроен: задайте FOCUS_API_KEY.')
        if not valid_inn(inn):
            raise FocusInputError('Некорректный ИНН: не сходится контрольная сумма.')
        row = self.cached(inn)
        fresh = row and datetime.now(timezone.utc) - row.checked_at < timedelta(days=self.settings.ttl_days)
        if row and fresh and not force:
            return row.result
        if not row and self.usage()['used'] >= self.settings.inn_limit:
            raise FocusLimitExceeded(f'Исчерпан лимит проверок Контур.Фокуса ({self.settings.inn_limit} компаний).')
        raw = {method: self._get(method, inn) for method in METHODS}
        if not raw['req']:
            raise FocusInputError(f'Компания с ИНН {inn} не найдена в Контур.Фокусе.')
        result = evaluate(inn, raw)
        with self.engine.begin() as connection:
            connection.execute(text(
                'INSERT INTO focus_checks (inn, company, raw, result, checked_at) VALUES (:inn, :company, CAST(:raw AS jsonb), CAST(:result AS jsonb), now()) '
                'ON CONFLICT (inn) DO UPDATE SET company = EXCLUDED.company, raw = EXCLUDED.raw, result = EXCLUDED.result, checked_at = now()'),
                {'inn': inn, 'company': result['company'], 'raw': json.dumps(raw, ensure_ascii=False), 'result': json.dumps(result, ensure_ascii=False)})
        return result

    def _get(self, method, inn):
        try:
            response = self.http.get(f'{self.settings.base_url}/{method}', params={'inn': inn, 'key': self.settings.api_key})
        except httpx.HTTPError as error:
            raise FocusError(f'Нет связи с Контур.Фокусом ({method}): {type(error).__name__}') from error
        if response.status_code == 403:
            raise FocusError(f'Контур.Фокус отклонил запрос {method}: ключ не действует или метод не входит в тариф.')
        if response.status_code >= 400:
            log.warning('Focus %s failed: %s', method, response.status_code)
            raise FocusError(f'Контур.Фокус ответил ошибкой {response.status_code} на {method}.')
        data = response.json()
        return data[0] if isinstance(data, list) and data else {}
