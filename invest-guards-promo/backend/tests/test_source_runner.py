"""Source runner: retries, errors, backoff, locks, schedule and manual runs. External APIs go through MockTransport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.database import SourceRunRecord, SyncStateRecord
from app.llm import LlmSettings
from app.main import create_app
from app.sources import base
from app.sources.imports import event_from_row
from app.sources.runner import LOCK_NS, due_sources, mark_stale_runs, push_events, sync_source
from app.worker import tick
from conftest import api_client
from fakes import Api, add_source
from test_events import LOT, PERMIT
from test_pipeline import rss

OK = (200, {'items': [PERMIT]})


@pytest.fixture
def pauses(monkeypatch):
    waits = []
    monkeypatch.setattr(base, 'pause', waits.append)
    return waits


def make_api(database_url, handler):
    return create_app(database_url, worker=False, llm_settings=LlmSettings(), fetch_transport=httpx.MockTransport(handler))


def run(api, source_id='fake-src'):
    with api.app.state.sessions() as db:
        return sync_source(db, source_id, api.app.state.http)


def state(api, source_id='fake-src'):
    with api.app.state.sessions() as db:
        return db.get(SyncStateRecord, source_id)


def source(api, source_id='fake-src'):
    return next(s for s in api.get('/api/sources').json() if s['id'] == source_id)


def test_server_error_is_retried_then_recorded(database_url, pauses):
    handler = Api((500, 'oops'))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert len(handler.requests) == 3 and result['retries'] == 2 and pauses == [0, 0]
        assert result['status'] == 'error' and result['error'] == 'HTTP 500'
        saved = source(api)
        assert saved['status'] == 'retry' and saved['lastError'] == 'HTTP 500' and saved['errors7d'] == 1
        after = state(api)
        assert after.consecutive_failures == 1 and after.last_error_kind == 'network'
        assert timedelta(minutes=4) < after.next_run_at - datetime.now(timezone.utc) <= timedelta(minutes=5), 'backoff starts at 5 minutes'
        run(api)
        assert timedelta(minutes=9) < state(api).next_run_at - datetime.now(timezone.utc) <= timedelta(minutes=10)
        runs = api.get('/api/sources/fake-src/runs').json()
        assert [r['status'] for r in runs] == ['error', 'error'] and runs[0]['retries'] == 2


def test_rate_limit_waits_for_retry_after(database_url, pauses):
    handler = Api((429, 'slow down', {'Retry-After': '7'}), OK)
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert result['status'] == 'ok' and result['inserted'] == 1 and result['retries'] == 1 and pauses == [7]


def test_long_retry_after_stops_and_postpones_run(database_url, pauses):
    handler = Api((429, 'later', {'Retry-After': '7200'}))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert result['status'] == 'error' and 'HTTP 429' in result['error'] and len(handler.requests) == 1 and not pauses
        assert state(api).next_run_at - datetime.now(timezone.utc) > timedelta(minutes=119)


def test_access_denied_is_not_retried(database_url, pauses):
    handler = Api((403, 'forbidden'))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert len(handler.requests) == 1 and not pauses
        assert result['status'] == 'error' and 'HTTP 403' in result['error']
        saved = source(api)
        assert saved['connection'] == 'no_access' and saved['status'] == 'not_configured'
        assert timedelta(minutes=59) < state(api).next_run_at - datetime.now(timezone.utc), 'no fast retry for a dead key'


def test_broken_json_is_data_error(database_url, pauses):
    handler = Api((200, '{"items": [', {'Content-Type': 'application/json'}))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert len(handler.requests) == 1 and result['status'] == 'error' and result['error'] == 'Источник вернул некорректный JSON.'
        assert state(api).last_error_kind == 'data'


def test_network_failure_is_retried(database_url, pauses):
    def down(request):
        raise httpx.ConnectError('refused', request=request)
    handler = Api(down)
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        result = run(api)
        assert len(handler.requests) == 3 and result['error'] == 'Нет связи с источником: ConnectError'


def test_partial_run_keeps_committed_batches_and_cursor(database_url, pauses):
    handler = Api((200, {'items': [PERMIT], 'more': True}), (200, {'items': [LOT]}))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src', options={'explode': True})
        result = run(api)
        assert result['status'] == 'partial' and result['inserted'] == 1 and result['error'] == 'Внутренняя ошибка: RuntimeError'
        assert state(api).sync_cursor == {'page': 1}
        assert api.get('/api/events?source=fake-src').json()['total'] == 1


def test_unexpected_error_does_not_break_tick(database_url, monkeypatch, pauses):
    def feeds(request):
        if request.url.host == 'events.test':
            return httpx.Response(200, json={'items': [PERMIT], 'more': request.url.params['page'] == '0'})
        return httpx.Response(200, text=rss(('Новость', 'https://feed.test/1', 'Текст')))
    with api_client(make_api(database_url, feeds)) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'a-broken', options={'explode': True})
        add_source(sessions, 'b-bad-url', kind='rss', url='http://[::1')
        add_source(sessions, 'c-good')
        monkeypatch.setenv('SOURCE_KINDS', 'fake,rss')
        with sessions() as db:
            db.execute(text("UPDATE sources SET payload = payload || '{\"enabled\": false}' WHERE payload->>'kind' = 'rss' AND id <> 'b-bad-url'"))
            db.commit()
        tick(sessions, api.app.state.llm, api.app.state.http, 5)
        statuses = {s['id']: s for s in api.get('/api/sources').json()}
        assert statuses['a-broken']['lastRunStats']['status'] == 'partial'
        assert statuses['b-bad-url']['status'] == 'retry' and statuses['b-bad-url']['lastError']
        assert statuses['c-good']['status'] == 'active' and statuses['c-good']['events'] == 1
        assert statuses['oblgazeta']['status'] == 'disabled'
        with sessions() as db:
            assert due_sources(db, ('fake', 'rss')) == [], 'failed sources wait for their backoff'


def test_not_configured_source_makes_no_requests(database_url, monkeypatch, pauses):
    monkeypatch.delenv('FAKE_SOURCE_KEY', raising=False)
    handler = Api(OK)
    with api_client(make_api(database_url, handler)) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'keyed', kind='fake-keyed')
        add_source(sessions, 'trudvsem-planned', kind='trudvsem')
        for source_id in ('keyed', 'trudvsem-planned'):
            response = api.post(f'/api/sources/{source_id}/collect').json()
            assert response['queued'] is False and response['source']['status'] == 'not_configured'
            assert response['source']['connection'] == 'not_configured' and response['source']['lastError']
            check = api.post(f'/api/sources/{source_id}/check').json()
            assert check['ok'] is False and check['connection'] == 'not_configured'
        assert not handler.requests
        assert 'FAKE_SOURCE_KEY' in source(api, 'keyed')['lastError']
        assert api.get('/api/sources/keyed/runs').json()[0]['status'] == 'not_configured'
        state = api.get('/api/state').json()
        assert not any(s['status'] == 'active' for s in state['sources'] if s['id'] in ('keyed', 'trudvsem-planned'))
        monkeypatch.setenv('FAKE_SOURCE_KEY', 'secret')
        assert source(api, 'keyed')['connection'] == 'ready' and source(api, 'keyed')['status'] == 'active'
        assert api.post('/api/sources/keyed/collect').json()['queued'] is True


def test_parallel_run_is_skipped(database_url, pauses):
    handler = Api(OK)
    with api_client(make_api(database_url, handler)) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'fake-src')
        with sessions() as holder:
            holder.execute(text('SELECT pg_advisory_lock(:ns, hashtext(:id))'), {'ns': LOCK_NS, 'id': 'fake-src'})
            result = run(api)
            assert result['status'] == 'skipped' and not handler.requests
            holder.execute(text('SELECT pg_advisory_unlock(:ns, hashtext(:id))'), {'ns': LOCK_NS, 'id': 'fake-src'})
        assert run(api)['status'] == 'ok'


def test_due_sources_follow_schedule_and_requests(database_url, pauses):
    with api_client(make_api(database_url, Api(OK))) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'fake-src')
        with sessions() as db:
            assert due_sources(db, ('fake',)) == ['fake-src'], 'never collected: due now'
        run(api)
        with sessions() as db:
            assert due_sources(db, ('fake',)) == []
            assert due_sources(db, ('rss',))[:2] == ['66ru', 'agroinvestor'], 'RSS still due by lastRunAt'
        api.post('/api/sources/fake-src/collect')
        with sessions() as db:
            assert due_sources(db, ('fake',)) == ['fake-src'], 'manual request is picked up by the worker'
        assert run(api)['trigger'] == 'manual'
        with sessions() as db:
            assert due_sources(db, ('fake',)) == []
            db.execute(text("UPDATE source_sync_state SET next_run_at = now() - interval '1 minute'"))
            db.commit()
            assert due_sources(db, ('fake',)) == ['fake-src']


def test_rss_check_and_runs(database_url):
    handler = Api((200, rss(('Заголовок', 'https://oblgazeta.ru/1', 'Текст'))))
    with api_client(make_api(database_url, handler)) as api:
        check = api.post('/api/sources/oblgazeta/check').json()
        assert check == {'ok': True, 'message': 'Лента доступна, записей: 1.', 'checkedAt': check['checkedAt'], 'connection': 'ready'}
        assert api.get('/api/state').json()['pipeline']['stages'] == {}, 'check writes no documents'
        assert source(api, 'oblgazeta')['lastCheck']['ok'] is True
        result = api.post('/api/sources/oblgazeta/collect').json()
        assert result['added'] == 1 and result['queued'] is False
        assert api.get('/api/sources/oblgazeta/runs').json()[0]['inserted'] == 1
        assert api.get('/api/sources/missing/runs').status_code == 404
        assert api.post('/api/sources/missing/check').status_code == 404


def test_stale_runs_are_closed(database_url):
    with api_client(make_api(database_url, Api(OK))) as api:
        sessions = api.app.state.sessions
        with sessions() as db:
            db.add(SourceRunRecord(source_id='rbc', run_trigger='schedule', started_at=datetime.now(timezone.utc) - timedelta(hours=3)))
            db.add(SourceRunRecord(source_id='rbc', run_trigger='schedule'))
            db.commit()
        assert mark_stale_runs(sessions) == 1
        assert [r['status'] for r in api.get('/api/sources/rbc/runs').json()] == ['running', 'error']


def test_import_rows_are_validated_and_stored(database_url):
    types = {'construction_permit', 'engineering_connection'}
    good = event_from_row({'event_type': 'engineering_connection', 'title': 'Заявка на 2 МВт', 'inn': '6661005707',
                           'ogrn': '1026600000350', 'municipality': 'Ревда', 'event_date': '01.10.2026', 'value': '2 000,5',
                           'value_unit': 'кВт', 'cadastral_number': '66:21:0101001:15', 'url': 'https://rosseti.test/1'}, types)
    assert good.municipality == 'Ревда' and str(good.value) == '2000.5' and good.event_date.isoformat() == '2026-10-01'
    assert good.raw['value'] == '2 000,5' and good.location_precision == 'municipality'
    with pytest.raises(base.SourceDataError) as error:
        event_from_row({'event_type': 'bogus', 'title': '', 'inn': '6661005708', 'municipality': 'Нью-Йорк', 'event_date': '2026/10/01',
                        'value': 'много', 'cadastral_number': '66-21', 'url': 'javascript:alert(1)', 'phone': '+7'}, types)
    for part in ('незнакомые колонки: phone', 'неизвестный тип', 'нет названия', 'ИНН', 'муниципалитет', 'дата', 'число',
                 'кадастровый', 'ссылка'):
        assert part in str(error.value)
    with api_client(make_api(database_url, Api(OK))) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'rosseti', kind='import')
        with sessions() as db:
            first = push_events(db, 'rosseti', [good])
            second = push_events(db, 'rosseti', [good])
        assert (first['inserted'], second['unchanged']) == (1, 1)
        saved = source(api, 'rosseti')
        assert saved['connection'] == 'manual_only' and saved['events'] == 1
        assert api.post('/api/sources/rosseti/collect').json()['source']['lastRunStats']['status'] == 'skipped'
        with sessions() as db:
            assert due_sources(db, ('import',)) == []


def test_assistant_collects_only_news_without_id(database_url, pauses, monkeypatch):
    from app import dzen
    from app.agent import Agent
    monkeypatch.setattr(dzen, 'search', lambda queries: [])

    def feeds(request):
        if request.url.host == 'events.test':
            return httpx.Response(200, json={'items': [PERMIT]})
        return httpx.Response(200, text=rss(('Новость', f'https://{request.url.host}/1', 'Текст')))
    with api_client(make_api(database_url, feeds)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        agent = Agent(api.app.state.sessions, api.app.state.llm, api.app.state.http)
        result = agent.collect_sources()
        assert 'fake-src' not in result and result['oblgazeta']['added'] == 1 and result['dzen'] == {'added': 0, 'status': 'active', 'error': None}
        assert agent.collect_sources('fake-src') == {'fake-src': {'added': 0, 'queued': True, 'status': 'queued'}}
        assert api.get('/api/events').json()['total'] == 0
        assert api.get('/api/sources/oblgazeta/runs').json()[0]['trigger'] == 'assistant'
