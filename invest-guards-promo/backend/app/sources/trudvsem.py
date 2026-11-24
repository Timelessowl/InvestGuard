"""«Работа России» open data API (https://trudvsem.ru/opendata/api): vacancies of one region.

Checked against the documentation and live answers (24.11.2026):
- GET {base}/vacancies/region/{regionCode}?offset=<page number>&limit=<≤100>&modifiedFrom=&modifiedTo= (ISO 8601, both inclusive);
- one query returns at most 10 000 records: a page past them answers HTTP 500, so large ranges are split by modification time;
- answer: {status, meta: {total, limit}, results: {vacancies: [{vacancy: {...}}]}}, results is {} when nothing matches.
"""
import copy
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..vacancies import store  # noqa: F401 - registers the 'vacancy' details writer
from ..vacancies.model import Vacancy, municipality_from_address, scrub_contacts
from . import base, register
from .base import Batch, SourceAdapter, SourceDataError, SourceError, keep_fields

log = logging.getLogger(__name__)
DEFAULT_BASE = 'https://opendata.trudvsem.ru/api/v1'
DEFAULT_REGION = '6600000000000'  # Свердловская область, region.region_code in the API answer
FLOOR = datetime(2000, 1, 1, tzinfo=timezone.utc)
FLOOR_MARGIN = timedelta(days=30)
RECORD_CAP = 10_000  # documented per-query limit
WINDOW_LIMIT = 9_000  # split earlier: the open newest window grows while it is paged
SYNDICATED = 'Вакансия интернет ресурса'
# Whitelist for raw: no contact_person, contact_list or company e-mail.
RAW_FIELDS = ('id', 'source', 'region.region_code', 'region.name', 'creation-date', 'date_modify', 'job-name', 'vac_url', 'salary',
              'salary_min', 'salary_max', 'currency', 'schedule', 'employment', 'work_places', 'code_profession', 'typicalPosition',
              'category.specialisation', 'qualification', 'requirement.education', 'requirement.experience', 'conditions', 'term',
              'company.inn', 'company.ogrn', 'company.kpp', 'company.companycode', 'company.name', 'company.hr-agency',
              'company.url', 'company.site')
TEXT_LIMIT = 2000
CHECK_TIMEOUT = 25
clock = lambda: datetime.now(timezone.utc)  # noqa: E731 - tests replace it


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def moment(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def env_number(name, default, cast=int):
    try:
        return cast(os.environ.get(name) or default)
    except ValueError:
        return default


def number(value, cast=float):
    try:
        return cast(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def salary(value):
    amount = number(value)
    return Decimal(str(value)) if amount and amount > 0 and math.isfinite(amount) else None  # 0 means not stated


def source_moment(value):
    """date_modify comes as 2026-11-24T13:29:46+0300."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S%z')
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def source_day(value):
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def newest_modified(items, limit):
    """Latest date_modify of the page not after `limit`: a future one is a source error, not the data front."""
    found = [source_moment(entry['vacancy'].get('date_modify')) for entry in items
             if isinstance(entry, dict) and isinstance(entry.get('vacancy'), dict)]
    return max((m for m in found if m and m.tzinfo and m <= limit), default=None)


def windows_from(lower, now):
    """Windows oldest first, open above: wide where old vacancies are sparse, one, two, four… days near now where most are.
    Bounds are inclusive, so a boundary second is read twice (upsert keeps one)."""
    edges, days = [], 1
    while now - timedelta(days=days) > lower:
        edges.append(now - timedelta(days=days))
        days *= 2
    edges = [lower] + edges[::-1]
    return [[stamp(a), stamp(b)] for a, b in zip(edges, edges[1:])] + [[stamp(edges[-1]), None]]


def split(window, now):
    """Two halves by modification time; both bounds are inclusive, so the middle second is read twice (upsert keeps one)."""
    lower, upper = moment(window[0]), moment(window[1]) if window[1] else now
    if upper - lower < timedelta(seconds=2):
        raise SourceDataError(f'Больше {WINDOW_LIMIT} вакансий изменены за одну секунду: выгрузку нельзя разбить по времени.')
    middle = stamp(lower + (upper - lower) / 2)
    return [[window[0], middle], [middle, window[1]]]


@register('trudvsem')
class TrudvsemAdapter(SourceAdapter):
    produces = 'events'

    def __init__(self, source, http):
        super().__init__(source, http)
        self.base_url = (os.environ.get('TRUDVSEM_BASE_URL') or self.options.get('baseUrl') or DEFAULT_BASE).rstrip('/')
        self.region = (os.environ.get('TRUDVSEM_REGION_CODE') or self.options.get('regionCode') or DEFAULT_REGION).strip()
        self.page_size = max(1, min(100, int(self.options.get('pageSize') or 100)))
        self.delay = env_number('TRUDVSEM_PAGE_DELAY_SECONDS', 0.5, float)
        self.max_pages = max(1, env_number('TRUDVSEM_MAX_PAGES', 300))
        self.full_days = env_number('TRUDVSEM_FULL_SYNC_DAYS', 7, float)
        self.overlap = timedelta(minutes=env_number('TRUDVSEM_OVERLAP_MINUTES', 60))
        # The API answers in 12-15 s whatever the page size (measured 25.11.2026).
        self.timeout = max(5.0, env_number('TRUDVSEM_TIMEOUT_SECONDS', 60, float))

    @property
    def url(self):
        return f'{self.base_url}/vacancies/region/{self.region}'

    def availability(self):
        ok = self.base_url.startswith('http') and self.region.isdigit() and len(self.region) == 13
        return 'ready' if ok else 'not_configured'

    def not_ready_message(self):
        return 'Требуется настройка: TRUDVSEM_BASE_URL должен быть http(s)-адресом, TRUDVSEM_REGION_CODE — 13 цифр.'

    def check(self):
        if self.availability() != 'ready':
            return False, self.not_ready_message()
        # Below the 30 s frontend timeout of the check button.
        data = self.request_json(self.url, {'offset': 0, 'limit': 1}, attempts=1, timeout=min(self.timeout, CHECK_TIMEOUT))
        total, _ = self.parse_meta(data)
        return True, f'API доступен, вакансий в регионе: {total}.'

    def parse_meta(self, data):
        if not isinstance(data, dict) or not isinstance(data.get('meta'), dict):
            raise SourceDataError('Ответ API без meta: схема изменилась.')
        if str(data.get('status', '200')) != '200':
            raise SourceError('API сообщил об ошибке: ' + str(data['meta'].get('error') or data.get('status'))[:300])
        total = number(data['meta'].get('total'), int)
        results = data.get('results')
        if total is None or not isinstance(results, dict):
            raise SourceDataError('Ответ API без meta.total или results: схема изменилась.')
        items = results.get('vacancies', [])
        if not isinstance(items, list):
            raise SourceDataError('results.vacancies не список: схема изменилась.')
        return total, items

    def full_floor(self, cursor):
        """Modification time only grows: the next full export starts a margin below the oldest one met last time."""
        oldest = cursor.get('oldest_modified')
        return max(FLOOR, moment(oldest) - FLOOR_MARGIN) if oldest else FLOOR

    def full_due(self, cursor, now):
        done = cursor.get('full_sync_at')
        return not cursor.get('modified_from') or not done or now - moment(done) >= timedelta(days=self.full_days)

    def next_from(self, state, current):
        """Next increment starts at the latest date_modify received minus the overlap, not at the pass start: the API
        publishes changes in batches about 12 hours late (checked 25.11.2026). A pass that received nothing keeps it."""
        previous = state.get('modified_from')
        if not current.get('newest'):
            return previous or current.get('from') or stamp(FLOOR)
        bound = moment(current['newest']) - self.overlap
        if current['mode'] == 'incremental' and previous:
            bound = max(bound, moment(previous))
        return stamp(bound)

    def fetch(self, cursor):
        """One pass: full (weekly, from below the oldest modification) or incremental (from the latest received
        modification minus the overlap). Windows go oldest first, the open newest one last, so a vacancy edited during
        the pass is still met. Position is in the cursor: a failed or capped run continues the same pass."""
        state = copy.deepcopy(cursor or {})
        now = clock()
        current = state.get('pass')
        if not current:
            full = self.full_due(state, now)
            lower = self.full_floor(state) if full else moment(state['modified_from'])
            current = {'mode': 'full' if full else 'incremental', 'startedAt': stamp(now), 'from': stamp(lower),
                       'windows': windows_from(lower, now), 'page': 0}
        requests = 0
        while current['windows']:
            if requests >= self.max_pages:
                raise SourceError(f'Достигнут предел TRUDVSEM_MAX_PAGES={self.max_pages} страниц за запуск: '
                                  'выгрузка продолжится со следующей страницы.')
            if requests:
                base.pause(self.delay)
            window, page = current['windows'][0], current['page']
            params = {'offset': page, 'limit': self.page_size, 'modifiedFrom': window[0]}
            if window[1]:
                params['modifiedTo'] = window[1]
            data = self.request_json(self.url, params, timeout=self.timeout)
            requests += 1
            total, items = self.parse_meta(data)
            if page == 0 and total > WINDOW_LIMIT:
                current['windows'][0:1] = split(window, now)
                continue
            events, rejected = self.parse_items(items)
            oldest = min((e.details['modified_at'] for e in events if e.details.get('modified_at')), default=None)
            if oldest and (not current.get('oldest') or oldest < moment(current['oldest'])):
                current['oldest'] = stamp(oldest)
            newest = newest_modified(items, moment(current['startedAt']))
            if newest and (not current.get('newest') or newest > moment(current['newest'])):
                current['newest'] = stamp(newest)
            if not items or (page + 1) * self.page_size >= min(total, RECORD_CAP):
                current['windows'].pop(0)
                current['page'] = 0
            else:
                current['page'] = page + 1
            done = not current['windows']
            if done:
                state.pop('pass', None)
                state['modified_from'] = self.next_from(state, current)
                if current['mode'] == 'full':
                    state['full_sync_at'] = current['startedAt']
                    if current.get('oldest'):
                        state['oldest_modified'] = current['oldest']
            else:
                state['pass'] = current
            yield Batch(events=events, rejected=rejected, cursor=copy.deepcopy(state), snapshot_complete=done and current['mode'] == 'full')

    def parse_items(self, items):
        events, rejected = [], 0
        for entry in items:
            data = entry.get('vacancy') if isinstance(entry, dict) else None
            vacancy = self.normalize(data) if isinstance(data, dict) else None
            if vacancy is None:
                rejected += 1
                continue
            events.append(vacancy.event(self.raw(data)))
        return events, rejected

    def normalize(self, data):
        """Vacancy of the configured region with an employer identifier, otherwise None (counted as rejected)."""
        company = data.get('company') if isinstance(data.get('company'), dict) else {}
        region = (data.get('region') or {}).get('region_code') if isinstance(data.get('region'), dict) else None
        if str(region or '') != self.region:
            log.info('trudvsem: vacancy %s from region %s skipped', data.get('id'), region)
            return None
        addresses = (data.get('addresses') or {}).get('address') if isinstance(data.get('addresses'), dict) else None
        place = addresses[0] if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict) else {}
        location = scrub_contacts(place.get('location'), 500)
        try:
            vacancy = Vacancy(
                source_id=self.source['id'], external_id=str(data.get('id') or ''), title=str(data.get('job-name') or '').strip(),
                employer_name=str(company.get('name') or '').strip(), inn=company.get('inn'), ogrn=company.get('ogrn'),
                kpp=company.get('kpp'), source_employer_id=str(company.get('companycode') or '') or None,
                employer_url=company.get('url') if str(company.get('url') or '').startswith('http') else None,
                hr_agency=company.get('hr-agency') is True, region_code=str(region), address=location,
                municipality=municipality_from_address(location), latitude=number(place.get('lat')), longitude=number(place.get('lng')),
                published_at=source_day(data.get('creation-date')), modified_at=source_moment(data.get('date_modify')),
                work_places=number(data.get('work_places'), int), salary_min=salary(data.get('salary_min')),
                salary_max=salary(data.get('salary_max')), currency=data.get('currency'),
                employment=data.get('employment'), schedule=data.get('schedule'),
                specialisation=(data.get('category') or {}).get('specialisation') if isinstance(data.get('category'), dict) else None,
                profession_code=str(data['code_profession']) if data.get('code_profession') else None,
                url=data.get('vac_url'), syndicated=data.get('source') == SYNDICATED)
        except ValueError as error:
            log.warning('trudvsem: vacancy %s does not validate: %s', data.get('id'), error)
            return None
        if not vacancy.external_id or not vacancy.title or not vacancy.employer_key:
            return None
        if not vacancy.employer_name:
            vacancy = vacancy.model_copy(update={'employer_name': vacancy.employer_key})
        return vacancy

    def raw(self, data):
        raw = keep_fields(data, RAW_FIELDS)
        for name in ('duty', 'requirements'):
            text = scrub_contacts(data.get(name), TEXT_LIMIT)
            if text:
                raw[name] = text
        addresses = (data.get('addresses') or {}).get('address') if isinstance(data.get('addresses'), dict) else None
        if isinstance(addresses, list):
            raw['addresses'] = [{'location': scrub_contacts(a.get('location'), 500), 'lat': a.get('lat'), 'lng': a.get('lng')}
                                for a in addresses[:5] if isinstance(a, dict)]
        return raw

    def snapshot_finished(self, db, started_at):
        closed, reopened, marked = store.close_missing(db, self.source['id'], started_at)
        log.info('trudvsem full export: %s vacancies closed, %s reopened, %s missing for the first time', closed, reopened, marked)
