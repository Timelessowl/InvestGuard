"""Structured events: storage, raw JSON, repeat loads, versions and the events API."""
import httpx
import pytest
from sqlalchemy import func, select

from app.database import EventVersionRecord
from app.events import dedup_key
from app.llm import LlmSettings
from app.main import create_app
from app.sources.base import EventItem
from app.sources.runner import sync_source
from app.worker import tick
from conftest import api_client
from fakes import RECENT, Api, add_source, make_inn

INN = '6661005707'
OTHER_INN = make_inn('666100100')
PERMIT = {'id': 'p-1', 'type': 'construction_permit', 'title': 'Разрешение на строительство цеха литья', 'company': 'АО «ЕЗ ОЦМ»',
          'inn': INN, 'municipality': 'г. Екатеринбург', 'address': 'ул. Монтажников, 3', 'date': RECENT, 'status': 'Выдано',
          'value': 1500.50, 'unit': 'м²', 'metrics': [{'name': 'work_places', 'value': 120, 'unit': 'чел.'}],
          'url': 'https://events.test/p-1', 'employer': {'name': 'ЕЗ ОЦМ', 'phone': '+7 900 000-00-00'}, 'contact': 'Иванов И. И.'}
LOT = {'id': 'l-7', 'type': 'land_lot_published', 'title': 'Лот: участок 2 га под производство', 'municipality': 'Ревда',
       'date': '2026-01-15', 'url': 'https://events.test/l-7'}
VACANCY = {'id': 'v-3', 'type': 'vacancy_opened', 'title': 'Сварщик', 'company': 'ООО «Тест»', 'inn': OTHER_INN, 'municipality': 'Ревда',
           'date': RECENT}


def make_api(database_url, handler):
    return create_app(database_url, worker=False, llm_settings=LlmSettings(), fetch_transport=httpx.MockTransport(handler))


def test_event_created_with_fields_metrics_and_raw(database_url, monkeypatch):
    handler = Api((200, {'items': [PERMIT, LOT]}))
    with api_client(make_api(database_url, handler)) as api:
        add_source(api.app.state.sessions, 'fake-src')
        queued = api.post('/api/sources/fake-src/collect').json()
        assert queued['queued'] is True and queued['added'] == 0 and not handler.requests, 'API source is collected by the worker'
        monkeypatch.setenv('SOURCE_KINDS', 'fake')
        tick(api.app.state.sessions, api.app.state.llm, api.app.state.http, 5)
        page = api.get('/api/events').json()
        assert page['total'] == 2
        event = next(e for e in page['items'] if e['externalId'] == 'p-1')
        assert event['type'] == 'construction_permit' and event['typeTitle'] == 'Разрешение на строительство'
        assert event['inn'] == INN and event['municipality'] == 'Екатеринбург' and event['eventDate'] == RECENT
        assert event['value'] == 1500.5 and event['valueUnit'] == 'м²' and event['status'] == 'Выдано'
        assert event['metrics'] == [{'name': 'work_places', 'value': 120, 'unit': 'чел.'}]
        detail = api.get(f'/api/events/{event["id"]}').json()
        assert detail['raw'] == {'id': 'p-1', 'type': 'construction_permit', 'title': PERMIT['title'], 'company': 'АО «ЕЗ ОЦМ»',
                                 'inn': INN, 'date': RECENT, 'status': 'Выдано', 'value': 1500.5, 'employer': {'name': 'ЕЗ ОЦМ'}}
        assert 'contact' not in str(detail['raw']) and detail['versions'] == []
        source = next(s for s in api.get('/api/state').json()['sources'] if s['id'] == 'fake-src')
        assert source['events'] == 2 and source['produces'] == 'events' and source['connection'] == 'ready'
        assert source['status'] == 'active' and source['lastRunStats']['inserted'] == 2
        runs = api.get('/api/sources/fake-src/runs').json()
        assert runs[0]['trigger'] == 'manual' and runs[0]['status'] == 'ok' and runs[0]['fetched'] == 2


def test_repeat_load_unchanged_and_change_creates_version(database_url):
    changed = {**PERMIT, 'status': 'Продлено', 'value': 1700}
    handler = Api((200, {'items': [PERMIT, LOT]}), (200, {'items': [PERMIT, LOT]}), (200, {'items': [changed, LOT, LOT]}))
    with api_client(make_api(database_url, handler)) as api:
        sessions = api.app.state.sessions
        add_source(sessions, 'fake-src')
        with sessions() as db:
            assert sync_source(db, 'fake-src', api.app.state.http)['inserted'] == 2
            again = sync_source(db, 'fake-src', api.app.state.http)
            assert (again['inserted'], again['updated'], again['unchanged']) == (0, 0, 2)
            third = sync_source(db, 'fake-src', api.app.state.http)
            assert (third['inserted'], third['updated'], third['unchanged']) == (0, 1, 1), 'a duplicate in one batch is stored once'
            assert db.scalar(select(func.count()).select_from(EventVersionRecord)) == 1
        event = next(e for e in api.get('/api/events?source=fake-src').json()['items'] if e['externalId'] == 'p-1')
        assert api.get('/api/events').json()['total'] == 2
        detail = api.get(f'/api/events/{event["id"]}').json()
        assert detail['status'] == 'Продлено' and detail['value'] == 1700 and detail['processingStatus'] == 'new'
        assert detail['versions'][0]['status'] == 'Выдано' and detail['versions'][0]['raw']['value'] == 1500.5
        assert detail['versions'][0]['fields']['value'] == '1500.5'


def test_dedup_key_by_external_id_or_stable_hash():
    item = EventItem(event_type='other', title='Событие  X', inn=INN)
    assert dedup_key(EventItem(event_type='other', title='a', external_id=' 42 ')) == 'id:42'
    assert dedup_key(item).startswith('h:') and dedup_key(item) == dedup_key(EventItem(event_type='other', title='событие x', inn=INN))
    assert dedup_key(item) != dedup_key(EventItem(event_type='other', title='Событие X', inn=OTHER_INN))


def test_events_api_filters_pagination_and_errors(database_url):
    other = {**PERMIT, 'id': 'p-2', 'inn': OTHER_INN, 'company': 'ООО «Уралпласт»', 'municipality': 'Ревда', 'date': '2026-02-01'}
    with api_client(make_api(database_url, Api((200, {'items': [PERMIT, LOT, VACANCY, other]})))) as api:
        add_source(api.app.state.sessions, 'fake-src')
        with api.app.state.sessions() as db:
            sync_source(db, 'fake-src', api.app.state.http)

        def ids(query):
            response = api.get('/api/events' + query)
            assert response.status_code == 200, response.text
            return sorted(e['externalId'] for e in response.json()['items'])
        assert ids('?type=construction_permit') == ['p-1', 'p-2']
        assert ids('?type=land_lot_published&type=vacancy_opened') == ['l-7', 'v-3']
        assert ids(f'?inn={INN}') == ['p-1']
        assert ids('?company=уралпласт') == ['p-2']
        assert ids('?company=%25') == [], 'LIKE wildcards are escaped'
        assert ids('?municipality=г.%20Ревда') == ['l-7', 'p-2', 'v-3']
        assert ids('?source=fake-src&processing=new') == ['l-7', 'p-1', 'p-2', 'v-3']
        assert ids('?source=other') == []
        assert ids('?date_from=2026-01-20&date_to=2026-03-01') == ['p-2']
        page = api.get('/api/events?limit=2&offset=1').json()
        assert page['total'] == 4 and len(page['items']) == 2 and page['limit'] == 2 and page['offset'] == 1
        assert api.get('/api/events?limit=500').status_code == 422
        assert api.get('/api/events?inn=12345').status_code == 422
        assert api.get('/api/events?processing=unknown').status_code == 422
        assert api.get('/api/events/999999').status_code == 404
        assert api.get('/api/candidates/INV-0254/events').json() == []
        assert api.get('/api/candidates/INV-9999/events').status_code == 404
        types = {t['code']: t for t in api.get('/api/event-types').json()}
        assert {'vacancy_opened', 'vacancy_growth', 'land_lot_published', 'land_lot_awarded', 'project_expertise', 'construction_permit',
                'engineering_connection', 'engineering_capacity_increase', 'engineering_refusal', 'infrastructure_barrier',
                'investment_equipment', 'corporate_expansion', 'other'} <= set(types)
        assert types['land_lot_published']['linkPolicy'] == 'context' and types['vacancy_opened']['linkPolicy'] == 'aggregate'
    with api_client(make_api(database_url, Api((200, {'items': []}))), auth=None) as anonymous:
        for path in ('/api/events', '/api/events/1', '/api/event-types', '/api/candidates/INV-0254/events', '/api/sources/fake-src/runs'):
            assert anonymous.get(path).status_code == 401
        assert anonymous.post('/api/sources/oblgazeta/check').status_code == 401


@pytest.mark.parametrize('kind', ['bogus_type'])
def test_unknown_event_type_is_rejected_not_fatal(database_url, kind):
    rows = [{**PERMIT, 'id': 'x', 'type': kind}, PERMIT]
    with api_client(make_api(database_url, Api((200, {'items': rows})))) as api:
        add_source(api.app.state.sessions, 'fake-src')
        with api.app.state.sessions() as db:
            run = sync_source(db, 'fake-src', api.app.state.http)
        assert run['status'] == 'ok' and run['inserted'] == 1 and run['rejected'] == 1
