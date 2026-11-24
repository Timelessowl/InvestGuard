"""Rule-based linking of events to cards, and old cards surviving the migration."""
import json
from datetime import date, timedelta
from sqlalchemy import create_engine, text

from app.database import ROOT, initialize
from app.linking import link_pending_events
from app.llm import LlmSettings
from app.main import create_app
from app.schemas import Candidate
from app.sources.base import EventItem
from app.sources.runner import push_events
from conftest import api_client
from fakes import add_source, make_inn, set_card

INN = '6661005707'
SOON = date.today() - timedelta(days=5)


def event(kind, title, inn=INN, place='Ревда', key=None, day=SOON, **fields):
    return EventItem(event_type=kind, title=title, inn=inn, municipality=place, external_id=key or title, event_date=day,
                     company_name=fields.pop('company', 'АО «Ревдинские приводы»'), url=fields.pop('url', None), **fields)


def setup(database_url):
    api = api_client(create_app(database_url, worker=False, llm_settings=LlmSettings()))
    api.__enter__()
    add_source(api.app.state.sessions, 'rosseti', kind='import', name='Россети Урал')
    return api, api.app.state.sessions


def push(sessions, *items):
    with sessions() as db:
        return push_events(db, 'rosseti', list(items))


def link(sessions):
    return link_pending_events(sessions)


def events_by_title(api):
    return {e['title']: e for e in api.get('/api/events?limit=200').json()['items']}


def test_linking_rules_for_existing_cards(database_url):
    api, sessions = setup(database_url)
    try:
        set_card(sessions, 'INV-0254', inn=INN)
        other = make_inn('667000001')
        set_card(sessions, 'INV-0261', inn=other)
        set_card(sessions, 'INV-0258', inn=other)
        push(sessions,
             event('engineering_connection', 'Заявка на 2 МВт', value=2000, value_unit='кВт', url='https://rosseti.test/1'),
             event('engineering_refusal', 'Отказ: нет свободной мощности', place=None),
             event('construction_permit', 'Склад в Нижнем Тагиле', place='Нижний Тагил'),
             event('land_lot_published', 'Лот: участок в Ревде', inn=None),
             event('vacancy_opened', 'Сварщик'),
             event('other', 'Без ИНН', inn=None),
             event('other', 'Только ОГРН', inn=None, ogrn='1026600000350'),
             event('construction_permit', 'Склад в Екатеринбурге', inn=other, place='Екатеринбург'),
             event('construction_permit', 'Склад в Ирбите', inn=other, place='Ирбит'))
        result = link(sessions)
        assert result['processed'] == 9 and result['linked'] == 3 and result['review'] == 4
        found = events_by_title(api)
        linked = found['Заявка на 2 МВт']
        assert linked['processingStatus'] == 'linked' and linked['candidates'] == [{'id': 'INV-0254', 'confidence': 'confirmed'}]
        assert found['Отказ: нет свободной мощности']['candidates'][0]['confidence'] == 'confirmed', 'no municipality: company level'
        moved = found['Склад в Нижнем Тагиле']
        assert moved['processingStatus'] == 'review' and 'другой проект' in moved['processingReason']
        assert moved['candidates'] == [{'id': 'INV-0254', 'confidence': 'manual_review'}]
        assert found['Лот: участок в Ревде']['processingStatus'] == 'context' and not found['Лот: участок в Ревде']['candidates']
        assert found['Сварщик']['processingStatus'] == 'aggregated'
        assert found['Без ИНН']['processingReason'] == 'Нет идентификатора юрлица (ИНН или ОГРН)'
        assert found['Только ОГРН']['processingStatus'] == 'review'
        assert found['Склад в Екатеринбурге']['candidates'] == [{'id': 'INV-0261', 'confidence': 'probable'}]
        assert {c['confidence'] for c in found['Склад в Ирбите']['candidates']} == {'manual_review'}
        card = api.get('/api/candidates/INV-0254').json()
        assert Candidate.model_validate(card).origin == 'demo'
        evidence = [e for e in card['evidence'] if e['kind'] == 'event']
        assert len(evidence) == 2 and len(card['evidence']) == 4
        assert evidence[0]['eventId'] == linked['id'] and evidence[0]['eventType'] == 'engineering_connection'
        assert evidence[0]['occurredAt'] == SOON.isoformat() and evidence[0]['url'] == 'https://rosseti.test/1'
        assert evidence[0]['source'] == 'Россети Урал · Заявка на техприсоединение' and '2000 кВт' in evidence[0]['excerpt']
        assert card['factors'] == next(c for c in json.loads((ROOT / 'seed/candidates.json').read_text()) if c['id'] == 'INV-0254')['factors']
        timeline = api.get('/api/candidates/INV-0254/events').json()
        assert [e['title'] for e in timeline] == ['Заявка на 2 МВт', 'Отказ: нет свободной мощности', 'Склад в Нижнем Тагиле']
        assert api.get('/api/events?candidate=INV-0254').json()['total'] == 3
        detail = api.get(f'/api/events/{moved["id"]}').json()
        assert detail['links'][0]['company'] == 'Ревдинские приводы' and detail['links'][0]['linkedBy'] == 'rule'

        # A changed event is linked again and its evidence is refreshed, not duplicated.
        push(sessions, event('engineering_connection', 'Заявка на 2 МВт', key='Заявка на 2 МВт', value=3000, value_unit='кВт'))
        assert link(sessions)['linked'] == 1
        card = api.get('/api/candidates/INV-0254').json()
        refreshed = [e for e in card['evidence'] if e.get('eventId') == linked['id']]
        assert len(refreshed) == 1 and '3000 кВт' in refreshed[0]['excerpt']
        assert link(sessions)['processed'] == 0
    finally:
        api.__exit__(None, None, None)


def test_threshold_creates_card_from_events(database_url):
    api, sessions = setup(database_url)
    inn = make_inn('667100002')
    try:
        push(sessions, event('construction_permit', 'Разрешение на цех', inn=inn, company='ООО «Новый завод»'))
        push(sessions, event('engineering_refusal', 'Отказ в подключении', inn=inn, place=None))
        assert link(sessions)['waiting'] == 2
        push(sessions, event('project_expertise', 'Экспертиза проекта', inn=inn), event('construction_permit', 'Разрешение (повтор)', inn=inn))
        assert link(sessions)['waiting'] == 2, 'one category is not enough'
        push(sessions, event('engineering_connection', 'Заявка на мощность', inn=inn, day=SOON - timedelta(days=30)))
        result = link(sessions)
        assert result['linked'] == 1
        found = events_by_title(api)
        cards = [c for c in api.get('/api/candidates').json() if c['origin'] == 'events']
        assert len(cards) == 1
        card = Candidate.model_validate(cards[0])
        assert card.inn == inn and card.company == 'ООО «Новый завод»' and card.city == 'Ревда' and card.location.precision == 'municipality'
        assert card.stage == 'Строительство' and card.status == 'new' and card.amount == 'Не указано' and card.sector == 'Не определена'
        assert len(card.evidence) == 4 and all(e.kind == 'event' for e in card.evidence)
        for title in ('Разрешение на цех', 'Экспертиза проекта', 'Разрешение (повтор)', 'Заявка на мощность'):
            assert found[title]['candidates'] == [{'id': card.id, 'confidence': 'confirmed'}]
        assert found['Отказ в подключении']['processingStatus'] == 'waiting'
        assert link(sessions)['linked'] == 1, 'a waiting obstacle joins the card once it exists'
        assert events_by_title(api)['Отказ в подключении']['candidates'] == [{'id': card.id, 'confidence': 'confirmed'}]
        assert 'risk' not in cards[0] or cards[0]['risk'] is None, 'no automatic Focus checks for event cards'
        assert api.get('/api/state').status_code == 200
    finally:
        api.__exit__(None, None, None)


def test_threshold_needs_one_municipality_and_window(database_url):
    api, sessions = setup(database_url)
    inn = make_inn('667100003')
    try:
        push(sessions, event('construction_permit', 'Цех в Ревде', inn=inn), event('engineering_connection', 'Сети в Серове', inn=inn, place='Серов'))
        link(sessions)
        found = events_by_title(api)
        assert {found['Цех в Ревде']['processingStatus'], found['Сети в Серове']['processingStatus']} == {'waiting', 'review'}
        old = make_inn('667100004')
        push(sessions, event('construction_permit', 'Давнее разрешение', inn=old, day=date.today() - timedelta(days=400)),
             event('engineering_connection', 'Свежая заявка', inn=old))
        link(sessions)
        assert events_by_title(api)['Свежая заявка']['processingStatus'] == 'waiting'
        assert not [c for c in api.get('/api/candidates').json() if c['origin'] == 'events']
    finally:
        api.__exit__(None, None, None)


def test_old_cards_survive_migration(database_url):
    """A database at migration 003 with an AI card and demo cards: after 004 everything validates, inn_key is filled."""
    engine = create_engine(database_url)
    old_card = {**json.loads((ROOT / 'seed/candidates.json').read_text())[0], 'id': 'INV-1000', 'origin': 'ai', 'inn': INN,
                'evidence': [{'source': 'E1.ru · Завод', 'date': '24 сен, 09:42', 'excerpt': 'Текст', 'highlights': [], 'url': 'https://e1.ru/1'}]}
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE schema_versions (version TEXT PRIMARY KEY)'))
        for migration in sorted((ROOT / 'migrations').glob('00[1-3]_*.sql')):
            for statement in migration.read_text().split(';'):
                if statement.strip():
                    connection.execute(text(statement))
            connection.execute(text('INSERT INTO schema_versions VALUES (:v)'), {'v': migration.name})
        connection.execute(text('INSERT INTO candidates (number, payload) VALUES (1000, CAST(:p AS jsonb))'), {'p': json.dumps(old_card)})
    initialize(engine)
    with engine.connect() as connection:
        rows = {row.number: row for row in connection.execute(text('SELECT number, payload, inn_key FROM candidates'))}
    engine.dispose()
    assert len(rows) == 7 and rows[1000].inn_key == INN and rows[254].inn_key is None
    assert rows[1000].payload == old_card, 'migration does not rewrite stored cards'
    with api_client(create_app(database_url, worker=False, llm_settings=LlmSettings())) as api:
        cards = {c['id']: c for c in api.get('/api/state').json()['candidates']}
        assert len(cards) == 7 and cards['INV-1000']['origin'] == 'ai'
        assert cards['INV-1000']['evidence'][0] == {**old_card['evidence'][0], 'kind': None, 'eventId': None, 'eventType': None, 'occurredAt': None}
        demo = json.loads((ROOT / 'seed/candidates.json').read_text())[2]
        assert cards[demo['id']]['company'] == demo['company'] and cards[demo['id']]['evidence'][0]['excerpt'] == demo['evidence'][0]['excerpt']
        assert api.get('/api/events').json()['total'] == 0
