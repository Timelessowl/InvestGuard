"""«Работа России» adapter through MockTransport: request shape, normalisation, pages, time windows, cursor,
full reconciliation, errors. The fixture is synthetic and carries contact fields only to prove they are dropped."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select, text

from app.database import EventRecord, EventVersionRecord, SyncStateRecord, VacancyDetailRecord, VacancyEmployerRecord
from app.sources import base, trudvsem
from app.sources.runner import sync_source
from conftest import api_client
from fakes import Api
from test_source_runner import make_api

PAGE = json.loads((Path(__file__).parent / 'fixtures/trudvsem_page.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc)
PLANT = 'inn:6609000015'


@pytest.fixture
def pauses(monkeypatch):
    waits = []
    monkeypatch.setattr(base, 'pause', waits.append)
    return waits


@pytest.fixture
def clock(monkeypatch):
    moments = [NOW]
    monkeypatch.setattr(trudvsem, 'clock', lambda: moments[0])
    return moments


def params(request):
    return {key: values[0] for key, values in parse_qs(urlparse(str(request.url)).query).items()}


def run(api, source_id='trudvsem'):
    with api.app.state.sessions() as db:
        return sync_source(db, source_id, api.app.state.http)


def cursor(api):
    with api.app.state.sessions() as db:
        return db.get(SyncStateRecord, 'trudvsem').sync_cursor


def vacancy(number, modified, title='Наладчик оборудования', inn='6609000015', ogrn='1026600000019'):
    """A minimal vacancy in the API shape, modified at `modified` (UTC)."""
    item = copy.deepcopy(PAGE['results']['vacancies'][0])
    data = item['vacancy']
    data['id'] = f'00000000-0000-4000-8000-{number:012d}'
    data['job-name'] = title
    data['date_modify'] = modified.astimezone(timezone(timedelta(hours=3))).strftime('%Y-%m-%dT%H:%M:%S%z')
    data['company'].update(inn=inn, ogrn=ogrn)
    return item


class FakeApi:
    """Region endpoint: inclusive modifiedFrom/modifiedTo, newest first, offset is a page number, HTTP 500 past the cap."""

    def __init__(self, vacancies, cap=10_000, fail=None):
        self.vacancies, self.cap, self.fail, self.requests = vacancies, cap, fail or {}, []

    def __call__(self, request):
        self.requests.append(request)
        if len(self.requests) in self.fail:
            return httpx.Response(self.fail[len(self.requests)], json={'status': '500', 'meta': {'error': 'сбой'}})
        query = params(request)
        offset, limit = int(query.get('offset', 0)), int(query.get('limit', 100))
        if (offset + 1) * limit > self.cap:
            return httpx.Response(500, json={'status': '500', 'request': {'api': 'v1'}, 'meta': {'error': 'Проверьте синтаксис запроса'}})
        low = datetime.fromisoformat(query['modifiedFrom'].replace('Z', '+00:00')) if 'modifiedFrom' in query else None
        high = datetime.fromisoformat(query['modifiedTo'].replace('Z', '+00:00')) if 'modifiedTo' in query else None
        found = [v for v in self.vacancies
                 if (low is None or self.modified(v) >= low) and (high is None or self.modified(v) <= high)]
        found.sort(key=self.modified, reverse=True)
        chunk = found[offset * limit:(offset + 1) * limit]
        results = {'vacancies': chunk} if chunk else {}
        return httpx.Response(200, json={'status': '200', 'request': {'api': 'v1'}, 'meta': {'total': len(found), 'limit': limit},
                                         'results': results})

    @staticmethod
    def modified(item):
        return datetime.strptime(item['vacancy']['date_modify'], '%Y-%m-%dT%H:%M:%S%z')


def test_first_sync_requests_region_and_normalises(database_url, pauses, clock):
    handler = Api((200, PAGE))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'ok', result
        assert (result['fetched'], result['inserted'], result['rejected']) == (5, 4, 1), 'Chelyabinsk vacancy is dropped'
        request = handler.requests[0]
        assert request.url.path == '/api/v1/vacancies/region/6600000000000'
        assert params(request) == {'offset': '0', 'limit': '100', 'modifiedFrom': '2000-01-01T00:00:00Z'}, 'first run is a full export'
        with api.app.state.sessions() as db:
            events = {e.external_id[-4:]: e for e in db.scalars(select(EventRecord).where(EventRecord.source_id == 'trudvsem'))}
            details = {d.event_id: d for d in db.scalars(select(VacancyDetailRecord))}
            employers = {e.employer_key: e for e in db.scalars(select(VacancyEmployerRecord))}
        plant = events['a001']
        assert plant.event_type == 'vacancy_opened' and plant.title == 'Наладчик оборудования (м/ж)'
        assert (plant.inn, plant.ogrn, plant.source_company_id) == ('6609000015', '1026600000019', '1026600000019')
        assert plant.municipality == 'Первоуральск' and plant.location_precision == 'address' and plant.latitude == 56.83
        assert plant.event_date.isoformat() == '2026-11-20' and plant.url.endswith('/00000000-0000-4000-8000-00000000a001')
        assert {m['name']: m['value'] for m in plant.metrics} == {'work_places': 2, 'salary_min': 60000.0, 'salary_max': 80000.0}
        dumped = json.dumps(plant.raw, ensure_ascii=False)
        assert 'contact_person' not in plant.raw and 'contact_list' not in plant.raw and 'email' not in plant.raw['company']
        assert 'Тестова' not in dumped and 'example.org' not in dumped.replace('plant.example.org', '') and '000-00-00' not in dumped
        assert plant.raw['company']['inn'] == '6609000015' and plant.raw['duty'].startswith('Наладка линии розлива')
        row = details[plant.id]
        assert (row.employer_key, row.title_norm, row.category, row.profile) == (PLANT, 'наладчик оборудования', 'setter', 'production')
        assert (row.work_places, row.published_at.isoformat(), row.modified_at.isoformat()) == (2, '2026-11-20', '2026-11-24T10:15:00+03:00')
        assert row.employment == 'Полная занятость' and row.specialisation == 'Производство' and row.closed_at is None
        # INN with a broken checksum is not kept or fixed: the employer is keyed by OGRN.
        broken = events['a002']
        assert broken.inn is None and details[broken.id].employer_key == 'ogrn:1026600000020'
        assert broken.municipality == 'Реж' and '[телефон]' in broken.address
        # No INN and no OGRN: the source employer id is the key, nothing is invented.
        nameless = events['a003']
        assert (nameless.inn, nameless.ogrn) == (None, None) and nameless.latitude is None and nameless.municipality is None
        assert details[nameless.id].employer_key == 'trudvsem:0b1f0c7e-0000-4000-8000-000000000001'
        assert employers[PLANT].source_ids == ['trudvsem'] and employers[PLANT].url.startswith('https://trudvsem.ru/company/')
        # A republication from another job site with the same fingerprint is a duplicate of the employer's own vacancy.
        assert details[events['a005'].id].syndicated and details[events['a005'].id].duplicate_of == plant.id
        assert cursor(api) == {'modified_from': '2026-11-25T05:00:00Z', 'full_sync_at': '2026-11-25T06:00:00Z'}


def test_resync_keeps_one_row_per_vacancy(database_url, pauses, clock):
    changed = copy.deepcopy(PAGE)
    changed['results']['vacancies'][0]['vacancy']['work_places'] = 5
    handler = Api((200, PAGE), (200, PAGE), (200, changed))
    with api_client(make_api(database_url, handler)) as api:
        assert run(api)['inserted'] == 4
        clock[0] = NOW + timedelta(hours=6)
        again = run(api)
        assert params(handler.requests[1])['modifiedFrom'] == '2026-11-25T05:00:00Z', 'incremental from last start minus overlap'
        assert (again['inserted'], again['updated'], again['unchanged']) == (0, 0, 4)
        with api.app.state.sessions() as db:
            assert db.get(SyncStateRecord, 'trudvsem').snapshot_started_at is None, 'an incremental run does not start a full export'
        third = run(api)
        assert (third['inserted'], third['updated'], third['unchanged']) == (0, 1, 3)
        with api.app.state.sessions() as db:
            assert db.scalar(text("SELECT count(*) FROM source_events WHERE source_id = 'trudvsem'")) == 4
            assert db.scalar(text('SELECT count(*) FROM vacancy_details')) == 4
            assert db.scalar(text('SELECT work_places FROM vacancy_details d JOIN source_events e ON e.id = d.event_id '
                                  "WHERE e.external_id LIKE '%a001'")) == 5
            assert len(list(db.scalars(select(EventVersionRecord)))) == 1


def test_duplicate_ids_in_one_page_are_stored_once(database_url, pauses, clock):
    page = copy.deepcopy(PAGE)
    page['results']['vacancies'].append(copy.deepcopy(page['results']['vacancies'][1]))
    with api_client(make_api(database_url, Api((200, page)))) as api:
        result = run(api)
        assert result['fetched'] == 6 and result['inserted'] == 4
        with api.app.state.sessions() as db:
            assert db.scalar(text("SELECT count(*) FROM source_events WHERE external_id LIKE '%a002'")) == 1


def test_pages_follow_meta_total(database_url, pauses, clock):
    stock = [vacancy(n, NOW - timedelta(minutes=n)) for n in range(1, 6)]
    handler = FakeApi(stock)
    with api_client(make_api(database_url, handler)) as api:
        with api.app.state.sessions() as db:
            db.execute(text("UPDATE sources SET payload = jsonb_set(payload, '{options,pageSize}', '2') WHERE id = 'trudvsem'"))
            db.commit()
        result = run(api)
        assert result['status'] == 'ok' and result['inserted'] == 5
        assert [params(r)['offset'] for r in handler.requests] == ['0', '1', '2'] and all(params(r)['limit'] == '2' for r in handler.requests)
        assert pauses == [0.5, 0.5], 'polite pause between pages'


def test_empty_page_ends_the_window(database_url, pauses, clock):
    empty = {'status': '200', 'request': {'api': 'v1'}, 'meta': {'total': 0, 'limit': 100}, 'results': {}}
    handler = Api((200, empty))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'ok' and result['fetched'] == 0 and len(handler.requests) == 1


def test_large_range_is_split_by_modification_time(database_url, pauses, clock, monkeypatch):
    # Scaled down: at most 5 records per query and windows above 4 are split, like 10 000 / 9 000 in the real API.
    monkeypatch.setattr(trudvsem, 'RECORD_CAP', 5)
    monkeypatch.setattr(trudvsem, 'WINDOW_LIMIT', 4)
    stock = [vacancy(n, NOW - timedelta(days=n * 3)) for n in range(1, 12)]
    handler = FakeApi(stock, cap=5)
    with api_client(make_api(database_url, handler)) as api:
        with api.app.state.sessions() as db:
            db.execute(text("UPDATE sources SET payload = jsonb_set(payload, '{options,pageSize}', '2') WHERE id = 'trudvsem'"))
            db.commit()
        result = run(api)
        assert result['status'] == 'ok', result
        assert result['inserted'] == 11, 'every vacancy is reached although one query returns at most 5'
        assert all((int(params(r)['offset']) + 1) * 2 <= 5 for r in handler.requests), 'never asks past the cap'
        windows = [(params(r)['modifiedFrom'], params(r).get('modifiedTo')) for r in handler.requests]
        assert any(upper for _, upper in windows) and len(set(windows)) > 2, 'the range was split'
        assert windows[-1][1] is None, 'the newest window stays open and goes last'
        assert cursor(api)['full_sync_at'] == '2026-11-25T06:00:00Z'


def test_full_export_closes_missing_and_partial_does_not(database_url, pauses, clock):
    stock = [vacancy(n, NOW - timedelta(hours=n)) for n in range(1, 6)]
    handler = FakeApi(stock)
    with api_client(make_api(database_url, handler)) as api:
        with api.app.state.sessions() as db:
            db.execute(text("UPDATE sources SET payload = jsonb_set(payload, '{options,pageSize}', '2') WHERE id = 'trudvsem'"))
            db.commit()
        assert run(api)['inserted'] == 5
        gone = stock.pop()  # removed from the portal
        clock[0] = NOW + timedelta(days=8)
        second_page = len(handler.requests) + 2
        handler.fail = {second_page: 503, second_page + 1: 503, second_page + 2: 503}
        failed = run(api)
        assert failed['status'] == 'partial' and failed['inserted'] + failed['unchanged'] == 2
        with api.app.state.sessions() as db:
            assert db.scalar(text('SELECT count(*) FROM vacancy_details WHERE closed_at IS NOT NULL')) == 0, 'partial export closes nothing'
        handler.fail = {}
        done = run(api)
        assert done['status'] == 'ok' and params(handler.requests[-1])['offset'] == '1', 'continues the same full export'
        with api.app.state.sessions() as db:
            closed = db.scalars(text('SELECT e.external_id FROM vacancy_details d JOIN source_events e ON e.id = d.event_id '
                                     'WHERE d.closed_at IS NOT NULL')).all()
        assert closed == [gone['vacancy']['id']], 'records seen in the interrupted run stay open'
        stock.append(gone)  # back on the portal unchanged: reopened by the next full export
        clock[0] = NOW + timedelta(days=16)
        run(api)
        with api.app.state.sessions() as db:
            assert db.scalar(text('SELECT count(*) FROM vacancy_details WHERE closed_at IS NOT NULL')) == 0


def test_page_cap_gives_partial_and_next_run_continues(database_url, pauses, clock, monkeypatch):
    monkeypatch.setenv('TRUDVSEM_MAX_PAGES', '1')
    stock = [vacancy(n, NOW - timedelta(minutes=n)) for n in range(1, 4)]
    handler = FakeApi(stock)
    with api_client(make_api(database_url, handler)) as api:
        with api.app.state.sessions() as db:
            db.execute(text("UPDATE sources SET payload = jsonb_set(payload, '{options,pageSize}', '2') WHERE id = 'trudvsem'"))
            db.commit()
        first = run(api)
        assert first['status'] == 'partial' and 'TRUDVSEM_MAX_PAGES' in first['error'] and first['inserted'] == 2
        assert cursor(api)['pass']['page'] == 1 and 'full_sync_at' not in cursor(api)
        second = run(api)
        assert second['status'] == 'ok' and second['inserted'] == 1 and params(handler.requests[-1])['offset'] == '1'
        assert cursor(api)['full_sync_at'] == '2026-11-25T06:00:00Z' and 'pass' not in cursor(api)


def test_server_error_is_retried(database_url, pauses, clock):
    handler = Api((500, {'status': '500', 'meta': {'error': 'сбой'}}), (200, PAGE))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'ok' and result['retries'] == 1 and result['inserted'] == 4 and pauses == [1.0]


def test_rate_limit_waits_retry_after(database_url, pauses, clock):
    handler = Api((429, 'slow down', {'Retry-After': '20'}), (200, PAGE))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'ok' and pauses == [20]


def test_unavailable_api_is_an_error_with_backoff(database_url, pauses, clock):
    handler = Api((503, 'maintenance'))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'error' and result['error'] == 'HTTP 503' and len(handler.requests) == 3
        source = next(s for s in api.get('/api/sources').json() if s['id'] == 'trudvsem')
        assert source['status'] == 'retry' and source['lastError'] == 'HTTP 503' and cursor(api) is None


@pytest.mark.parametrize('body, message', [
    ('<html>Bad gateway</html>', 'некорректный JSON'),
    ({'status': '200', 'results': {'vacancies': []}}, 'без meta'),
    ({'status': '200', 'meta': {'total': 3}, 'results': {'vacancies': {'x': 1}}}, 'не список'),
])
def test_broken_answers_are_data_errors(database_url, pauses, clock, body, message):
    with api_client(make_api(database_url, Api((200, body)))) as api:
        result = run(api)
        assert result['status'] == 'error' and message in result['error'] and result['retries'] == 0


def test_check_and_manual_collect(database_url, pauses, clock):
    handler = Api((200, {'status': '200', 'request': {'api': 'v1'}, 'meta': {'total': 16431, 'limit': 1}, 'results': {'vacancies': []}}))
    with api_client(make_api(database_url, handler)) as api:
        check = api.post('/api/sources/trudvsem/check').json()
        assert check == {**check, 'ok': True, 'connection': 'ready'} and '16431' in check['message']
        assert params(handler.requests[0]) == {'offset': '0', 'limit': '1'}
        queued = api.post('/api/sources/trudvsem/collect').json()
        assert queued['queued'] is True and len(handler.requests) == 1, 'the HTTP request never runs the export'
        signals = next(s for s in api.get('/api/sources').json() if s['id'] == 'hiring-signals')
        assert signals['kind'] == 'derived' and signals['connection'] == 'ready' and signals['group'] == 'hiring'


def test_wrong_region_setting_is_not_configured(database_url, pauses, clock, monkeypatch):
    monkeypatch.setenv('TRUDVSEM_REGION_CODE', '66')
    handler = Api((200, PAGE))
    with api_client(make_api(database_url, handler)) as api:
        result = run(api)
        assert result['status'] == 'not_configured' and 'TRUDVSEM_REGION_CODE' in result['error'] and not handler.requests
