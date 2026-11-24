"""Shared vacancy module: normalisation, categories, employer indicators on fixed dates, hiring signals and their storage."""
import itertools
import json
from datetime import date, datetime, time, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.common import TZ
from app.database import CandidateRecord, EventRecord, EventVersionRecord, VacancySnapshotRecord, initialize, make_engine, session_factory
from app.events import upsert_events
from app.linking import link_pending_events
from app.service import hiring_stats
from app.vacancies import SIGNAL_SOURCE, aggregates, jobs, signals, store  # noqa: F401 - store registers the writer
from app.vacancies.categories import CategoryBook, load_book
from app.vacancies.model import Vacancy, employer_key, fingerprint, municipality_from_address, scrub_contacts, title_norm
from conftest import api_client
from fakes import make_inn

AS_OF = date(2026, 11, 25)
PLANT, SMALL, FRESH, NOINN = make_inn('660900011'), make_inn('660900012'), make_inn('660900013'), None
numbers = itertools.count(1)


@pytest.fixture
def sessions(database_url):
    engine = make_engine(database_url)
    initialize(engine)
    yield session_factory(engine)
    engine.dispose()


def ago(days):
    return AS_OF - timedelta(days=days)


def noon(day):
    return datetime.combine(day, time(12), TZ)


def add(sessions, inn, title, published, *, seen=None, closed=None, place='Первоуральск', places=None, spec='Производство',
        name='ООО "ЗАВОД"', code=None, agency=False, syndicated=False, count=1):
    """Stores `count` vacancies through the real writer; first seen on `seen` (default: publication day)."""
    ids = []
    with sessions() as db:
        for _ in range(count):
            number = next(numbers)
            vacancy = Vacancy(source_id='trudvsem', external_id=f'v{number}', title=title, employer_name=name, inn=inn,
                              source_employer_id=code or f'code-{inn}', hr_agency=agency, municipality=place, published_at=published,
                              work_places=places, specialisation=spec, syndicated=syndicated, salary_min=50000,
                              url=f'https://trudvsem.ru/vacancy/card/x/v{number}')
            upsert_events(db, 'trudvsem', [vacancy.event({'id': f'v{number}'})])
            event_id = db.scalar(select(EventRecord.id).where(EventRecord.external_id == f'v{number}'))
            db.execute(text('UPDATE vacancy_details SET first_seen_at = :seen, closed_at = :closed WHERE event_id = :id'),
                       {'seen': noon(seen or published), 'closed': noon(closed) if closed else None, 'id': event_id})
            ids.append(event_id)
        db.commit()
    return ids if count > 1 else ids[0]


def observe(sessions, key, since):
    """Start of observation of an employer in InvestGuard."""
    with sessions() as db:
        db.execute(text('UPDATE vacancy_employers SET first_seen_at = :t WHERE employer_key = :k'), {'t': noon(since), 'k': key})
        db.commit()


def snapshot(sessions, key, day, active):
    with sessions() as db:
        db.add(VacancySnapshotRecord(employer_key=key, snapshot_date=day, active_count=active, active_work_places=active, new_30d=0,
                                     new_90d=0, production_count=0, engineering_count=0, management_count=0, municipalities=[]))
        db.commit()


def views(sessions, as_of=AS_OF):
    rules, book = signals.load_rules(), load_book()
    with sessions() as db:
        result = {v.key: v for v in aggregates.build_views(db, as_of, rules, book)}
        aggregates.update_exclusions(result.values())
        db.commit()
    return result


def found(view):
    return {(s.type, s.municipality): s for s in signals.evaluate(view, signals.load_rules(), load_book())}


def hiring_events(sessions):
    with sessions() as db:
        return list(db.scalars(select(EventRecord).where(EventRecord.source_id == SIGNAL_SOURCE).order_by(EventRecord.id)))


def test_normalisation_helpers():
    assert title_norm('Наладчик оборудования (м/ж)') == 'наладчик оборудования'
    assert title_norm('Ёмкостной ИНЖЕНЕР-технолог [Цех №2]') == 'емкостной инженер технолог'
    inn = make_inn('660900001')
    assert employer_key('trudvsem', inn, '1026600000019', 'x') == f'inn:{inn}'
    assert employer_key('trudvsem', '6609000019', '1026600000019', 'x') == 'ogrn:1026600000019', 'broken INN is not used'
    assert employer_key('trudvsem', None, '1026600000018', 'x') == 'trudvsem:x', 'broken OGRN is not used either'
    assert employer_key('hh', None, None, None) is None
    assert fingerprint('inn:1', 'Слесарь (м/ж)', 'Реж', 61000, 79000) == fingerprint('inn:1', 'слесарь', 'Реж', 59000, 81000)
    assert fingerprint('inn:1', 'Слесарь', 'Реж', 60000) != fingerprint('inn:2', 'Слесарь', 'Реж', 60000)
    assert municipality_from_address('Свердловская область, г Екатеринбург, Первоуральская улица, 1') == 'Екатеринбург'
    assert municipality_from_address('Свердловская область, Богдановичский район, г Богданович, Гагарина улица') == 'Богданович'
    assert municipality_from_address('Свердловская область, г Верхний Тагил, Ленина улица, 1') == 'Верхний Тагил', 'kept as written'
    assert municipality_from_address('Свердловская область, Белоярский район, п Растущий, Ленина улица') == 'Белоярский'
    assert municipality_from_address('Свердловская область, Артинский район, Арти поселок городского типа') is None
    assert municipality_from_address('') is None
    assert scrub_contacts('Звоните +7 (900) 123-45-67 или 8 (34364) 2-22-33, пишите hr@example.org', 200) == \
        'Звоните [телефон] или [телефон], пишите [e-mail]'


def test_categories_and_exclusions():
    book = load_book()
    assert book.classify('Наладчик оборудования 6 разряда') == ('setter', 'production')
    assert book.classify('Инженер-технолог') == ('technologist', 'engineering')
    assert book.classify('Оператор производственной линии') == ('line_operator', 'production')
    assert book.classify('Начальник цельномолочного цеха') == ('production_head', 'production_management')
    assert book.classify('Директор завода') == ('plant_director', 'executive')
    assert book.classify('Оператор связи') == (None, 'other')
    assert book.classify('Учитель технологии') == (None, 'other')
    assert book.classify('Мастер производственного обучения') == (None, 'other')
    assert book.classify('Кладовщик', 'Металлургия, металлообработка') == (None, 'production'), 'fallback by specialisation'
    assert book.excluded_reason('ООО "Кадры"', hr_agency=True).startswith('Кадровое агентство')
    for name in ('МАОУ СОШ № 1', 'ГАУЗ СО "ДГБ № 15"', 'АДМИНИСТРАЦИЯ АЧИТСКОГО МУНИЦИПАЛЬНОГО ОКРУГА', 'МБДОУ "ДЕТСКИЙ САД № 365"'):
        assert book.excluded_reason(name), name
    for name in ('МУП "ЕКАТЕРИНБУРГЭНЕРГО"', 'ООО "ТЕСТОВЫЙ ЗАВОД"', 'АО "НТМИЗ"'):
        assert book.excluded_reason(name) is None, name
    assert book.excluded_reason('ООО "УГМК-ЗДОРОВЬЕ"', specialisation_share=0.8).startswith('Бюджетная сфера')
    assert book.excluded_reason('ООО "УГМК-ЗДОРОВЬЕ"', specialisation_share=0.2) is None


def test_broken_dictionary_stops_startup(database_url, tmp_path, monkeypatch):
    base = json.loads(load_book().model_dump_json())
    with pytest.raises((ValidationError, ValueError)):
        CategoryBook.model_validate({**base, 'launchCombo': ['technologist', 'astronaut']})
    with pytest.raises((ValidationError, ValueError)):
        CategoryBook.model_validate({**base, 'categories': [{**base['categories'][0], 'keywords': ['(незакрытая']}]})
    broken = tmp_path / 'categories.json'
    broken.write_text(json.dumps({**base, 'launchCombo': ['astronaut']}), encoding='utf-8')
    monkeypatch.setenv('VACANCY_CATEGORIES_PATH', str(broken))
    with pytest.raises((ValidationError, ValueError)):
        initialize(make_engine(database_url))


def test_edited_dictionary_reclassifies_stored_vacancies(sessions, tmp_path, monkeypatch):
    event_id = add(sessions, PLANT, 'Аппаратчик', ago(5))
    assert jobs.refresh(sessions, as_of=AS_OF)['reclassified'] == 0, 'written with the current dictionary'
    edited = json.loads(load_book().model_dump_json())
    edited['categories'] = [c for c in edited['categories'] if c['code'] != 'line_operator']
    edited['launchCombo'] = [c for c in edited['launchCombo'] if c != 'line_operator']
    path = tmp_path / 'categories.json'
    path.write_text(json.dumps(edited, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setenv('VACANCY_CATEGORIES_PATH', str(path))
    assert jobs.refresh(sessions, as_of=AS_OF)['reclassified'] == 1
    assert jobs.refresh(sessions, as_of=AS_OF)['reclassified'] == 0, 'applied once'
    with sessions() as db:
        assert db.execute(text('SELECT category, profile FROM vacancy_details WHERE event_id = :id'), {'id': event_id}).one() == \
            (None, 'production')


def test_indicators_on_fixed_dates(sessions):
    key = f'inn:{PLANT}'
    add(sessions, PLANT, 'Слесарь-ремонтник', ago(10), places=3)
    add(sessions, PLANT, 'Слесарь-ремонтник', ago(40))
    add(sessions, PLANT, 'Слесарь-ремонтник', ago(100))
    add(sessions, PLANT, 'Инженер-конструктор', ago(20), place='Екатеринбург')
    add(sessions, PLANT, 'Начальник цеха', ago(5))
    add(sessions, PLANT, 'Бухгалтер', ago(60), spec='Бухгалтерия, налоги, управленческий учет')
    add(sessions, PLANT, 'Сварщик', ago(50), closed=ago(35))
    add(sessions, PLANT, 'Слесарь-ремонтник', ago(10), syndicated=True, places=3)  # republication: duplicate, not counted
    add(sessions, PLANT, 'Слесарь-ремонтник', AS_OF + timedelta(days=1))  # appears after the date: not known yet
    observe(sessions, key, ago(200))
    snapshot(sessions, key, ago(31), 4)
    view = views(sessions)[key]
    assert view.has_history and view.history_days == 200
    summary = view.summary()
    assert (summary['active'], summary['activeWorkPlaces'], summary['new30d'], summary['new90d']) == (6, 8, 3, 6)
    assert (summary['production'], summary['engineering'], summary['management']) == (3, 1, 1)
    assert summary['municipalities'] == ['Екатеринбург', 'Первоуральск'] and summary['newMunicipalities'] == ['Екатеринбург']
    assert summary['newMunicipalityVacancies'] == 1 and summary['newMunicipalityCounts'] == {'Екатеринбург': 1}
    assert summary['categories'] == {'engineer': 1, 'production_head': 1, 'worker': 3}
    assert summary['dynamics30d'] == {'days': 30, 'previous': 4, 'current': 6, 'delta': 2, 'ratio': 1.5, 'reason': None}
    assert summary['dynamics90d']['ratio'] is None and 'нет снимка' in summary['dynamics90d']['reason']
    with sessions() as db:
        aggregates.write_snapshots(db, [view])
        db.commit()
        saved = db.get(VacancySnapshotRecord, (key, AS_OF))
    assert (saved.active_count, saved.new_30d, saved.new_90d, saved.management_count) == (6, 3, 6, 1)
    assert (saved.new_municipalities, saved.new_municipality_vacancies) == (['Екатеринбург'], 1)


def test_copy_of_a_closed_vacancy_is_counted(sessions):
    """A republication that appears after the original closed heads the copies: it is open, so it is counted."""
    original = add(sessions, PLANT, 'Слесарь', ago(20), closed=ago(5))
    republished = add(sessions, PLANT, 'Слесарь', ago(10), syndicated=True)
    with sessions() as db:
        pointers = dict(db.execute(text('SELECT event_id, duplicate_of FROM vacancy_details')).all())
    assert pointers == {original: republished, republished: None}
    view = views(sessions)[f'inn:{PLANT}']
    assert view.summary()['active'] == 1 and [r.event_id for r in view.rows] == [republished]


def test_closing_regroups_copies(sessions):
    """close_missing marks on the first miss, closes on the second and hands the group to the earliest open copy."""
    first = add(sessions, PLANT, 'Слесарь', ago(20))
    second = add(sessions, PLANT, 'Слесарь', ago(15), syndicated=True)
    with sessions() as db:
        assert db.scalar(text('SELECT duplicate_of FROM vacancy_details WHERE event_id = :id'), {'id': second}) == first
        db.execute(text("UPDATE source_events SET last_seen_at = now() - interval '20 days' WHERE id = :id"), {'id': first})
        assert store.close_missing(db, 'trudvsem', datetime.now(TZ) - timedelta(days=10)) == (0, 0, 1)
        db.execute(text("UPDATE source_events SET last_seen_at = now() + interval '1 hour' WHERE id = :id"), {'id': second})
        assert store.close_missing(db, 'trudvsem', datetime.now(TZ)) == (1, 0, 0)
        pointers = dict(db.execute(text('SELECT event_id, duplicate_of FROM vacancy_details')).all())
        db.commit()
    assert pointers == {first: second, second: None}
    assert [r.event_id for r in views(sessions)[f'inn:{PLANT}'].active] == [second]


def test_first_export_is_not_a_hiring_surge(sessions):
    key = f'inn:{FRESH}'
    # Seen for the first time ten days ago, published long before: old for the market, so not "new".
    add(sessions, FRESH, 'Оператор линии', ago(100), seen=ago(10), count=20)
    observe(sessions, key, ago(10))
    view = views(sessions)[key]
    summary = view.summary()
    assert not view.has_history and summary['active'] == 20 and summary['new30d'] == 0
    assert summary['dynamics30d'] == {'days': 30, 'ratio': None, 'delta': None, 'reason': aggregates.NO_HISTORY}
    assert summary['newMunicipalities'] is None, 'absence before observation cannot be asserted'
    assert summary['newMunicipalityVacancies'] is None and view.snapshot_values()['new_municipality_vacancies'] is None
    assert found(view) == {}


def test_mass_hiring_is_measured_against_own_baseline(sessions):
    big, small, fresh = f'inn:{PLANT}', f'inn:{SMALL}', f'inn:{FRESH}'
    for month in range(1, 6):
        add(sessions, PLANT, 'Продавец-кассир', ago(30 * month + 5), closed=ago(30 * month - 10), count=10)
        add(sessions, SMALL, 'Слесарь', ago(30 * month + 5), closed=ago(30 * month - 10))
    add(sessions, PLANT, 'Продавец-кассир', ago(5), count=25)
    add(sessions, SMALL, 'Слесарь', ago(5), count=6)
    add(sessions, FRESH, 'Слесарь', ago(5), seen=ago(5), count=16)
    for key in (big, small):
        observe(sessions, key, ago(200))
    observe(sessions, fresh, ago(5))
    snapshot(sessions, big, ago(30), 40)
    snapshot(sessions, small, ago(30), 3)
    found_views = views(sessions)
    assert found(found_views[big]) == {}, '25 new against a usual 10 a month is ordinary for this employer'
    surge = found(found_views[small])[('vacancy_mass_hiring', None)]
    assert surge.value == 6 and surge.thresholds['threshold'] == 5 and surge.thresholds['baseline'] == 1
    assert surge.note.startswith('Относительно собственной базы')
    new = found(found_views[fresh])[('vacancy_mass_hiring', None)]
    assert new.thresholds['threshold'] == 15 and new.thresholds['baseline'] is None and new.note.startswith('Без базы сравнения')


def test_growth_only_with_history(sessions):
    old, young = f'inn:{PLANT}', f'inn:{SMALL}'
    for inn in (PLANT, SMALL):
        add(sessions, inn, 'Слесарь', ago(100), count=4)
        add(sessions, inn, 'Слесарь', ago(50), count=6)
    observe(sessions, old, ago(200))
    observe(sessions, young, ago(30))
    snapshot(sessions, old, ago(90), 4)
    snapshot(sessions, young, ago(90), 4)
    result = views(sessions)
    growth = found(result[old])[('vacancy_growth', None)]
    assert growth.value == 6 and growth.thresholds['ratio'] == 2.5 and growth.thresholds['previous'] == 4
    assert ('vacancy_growth', None) not in found(result[young])
    assert result[young].dynamics(90)['ratio'] is None and result[young].dynamics(90)['reason'] == aggregates.NO_HISTORY


def test_new_municipality_and_production_launch(sessions):
    add(sessions, PLANT, 'Инженер-конструктор', ago(150), place='Екатеринбург', closed=ago(100))
    add(sessions, PLANT, 'Слесарь', ago(120), place='Екатеринбург')
    add(sessions, PLANT, 'Инженер-конструктор', ago(10), place='Березовский', count=2)
    add(sessions, PLANT, 'Инженер-конструктор', ago(10), place='Ревда')
    add(sessions, PLANT, 'Инженер-конструктор', ago(8), place='Екатеринбург', count=2)
    observe(sessions, f'inn:{PLANT}', ago(200))
    starter = make_inn('660900014')
    add(sessions, starter, 'Инженер-конструктор', ago(10), place='Ревда', count=2)
    observe(sessions, f'inn:{starter}', ago(200))
    for title in ('Технолог', 'Инженер-конструктор', 'Наладчик оборудования', 'Начальник цеха'):
        add(sessions, SMALL, title, ago(20), place='Невьянск')
    for title in ('Технолог', 'Инженер-конструктор', 'Наладчик оборудования'):
        add(sessions, FRESH, title, ago(20), place='Невьянск')
    result = views(sessions)
    plant = found(result[f'inn:{PLANT}'])
    assert result[f'inn:{PLANT}'].new_municipalities() == ['Березовский', 'Ревда']
    assert result[f'inn:{PLANT}'].new_municipality_counts() == {'Березовский': 2, 'Ревда': 1}
    assert result[f'inn:{PLANT}'].summary()['newMunicipalityVacancies'] == 3, 'one vacancy in Ревда is counted, no signal'
    place = plant[('vacancy_new_municipality', 'Березовский')]
    assert place.value == 2 and place.start == ago(10)
    assert ('vacancy_new_municipality', 'Ревда') not in plant, 'one vacancy is below minVacancies'
    assert ('vacancy_new_municipality', 'Екатеринбург') not in plant, 'the employer hired there before'
    assert result[f'inn:{starter}'].new_municipalities() == [], 'the first place of an employer is not a new one'
    launch = found(result[f'inn:{SMALL}'])[('vacancy_production_launch', 'Невьянск')]
    assert launch.thresholds['categories'] == ['engineer', 'production_head', 'setter', 'technologist']
    assert launch.thresholds['management'] == ['production_head'] and len(launch.basis) == 4
    assert ('vacancy_production_launch', 'Невьянск') not in found(result[f'inn:{FRESH}']), 'no management vacancy'


def test_agencies_and_public_sector_get_no_signals(sessions):
    add(sessions, PLANT, 'Оператор линии', ago(5), name='ООО "КАДРОВОЕ АГЕНТСТВО"', agency=True, count=20)
    add(sessions, SMALL, 'Учитель', ago(5), name='МАОУ СОШ № 7', spec='Образование, наука', count=20)
    add(sessions, FRESH, 'Медсестра', ago(5), name='ООО "КЛИНИКА"', spec='Здравоохранение и социальное обеспечение', count=20)
    result = views(sessions)
    for inn in (PLANT, SMALL, FRESH):
        view = result[f'inn:{inn}']
        assert view.employer.excluded_reason and view.summary()['new30d'] == 20, 'still counted'
        assert found(view) == {}


def test_signals_are_stored_per_episode(sessions):
    add(sessions, PLANT, 'Оператор линии', ago(5), count=16)
    add(sessions, NOINN, 'Оператор линии', ago(5), code='0b1f0c7e-1', name='ИП ТЕСТОВ', count=16)
    observe(sessions, f'inn:{PLANT}', ago(5))
    observe(sessions, 'trudvsem:0b1f0c7e-1', ago(5))
    first = jobs.refresh(sessions, as_of=AS_OF)
    assert first['employers'] == 2 and first['run']['inserted'] == 2 and first['run']['status'] == 'ok'
    stored = {e.inn: e for e in hiring_events(sessions)}
    plant = stored[PLANT]
    assert plant.event_type == 'vacancy_mass_hiring' and plant.status == 'active' and plant.event_date == ago(5)
    assert plant.external_id == f'vacancy_mass_hiring:inn:{PLANT}:*:2026-11'
    assert plant.raw['thresholds']['threshold'] == 15 and plant.raw['indicators']['new30d'] == 16 and plant.raw['matching'] is None
    assert len(plant.raw['basis']) == 16 and plant.raw['basis'][0]['url'].startswith('https://trudvsem.ru/vacancy/card/')
    nameless = stored[None]
    assert nameless.source_company_id == '0b1f0c7e-1' and nameless.raw['matching'] and nameless.ogrn is None
    with sessions() as db:
        assert db.scalar(text('SELECT count(*) FROM vacancy_employer_snapshots WHERE snapshot_date = :d'), {'d': AS_OF}) == 2
        stats = hiring_stats(db)
    assert stats['trudvsem']['signals'] == 2 and stats[SIGNAL_SOURCE]['activeSignals'] == 2 and stats[SIGNAL_SOURCE]['employers'] == 2
    again = jobs.refresh(sessions, as_of=AS_OF)
    assert (again['run']['inserted'], again['run']['unchanged']) == (0, 2) and len(hiring_events(sessions)) == 2
    # A month later nothing new was published: the episode ends instead of a new signal appearing.
    later = jobs.refresh(sessions, as_of=AS_OF + timedelta(days=40))
    assert later['run']['updated'] == 2
    ended = {e.inn: e for e in hiring_events(sessions)}[PLANT]
    assert ended.status == 'ended' and ended.raw['endReason'] and len(hiring_events(sessions)) == 2
    with sessions() as db:
        assert db.scalar(select(EventVersionRecord.event_id).where(EventVersionRecord.event_id == ended.id)) == ended.id
    # A new wave in another month is a new episode with its own key.
    add(sessions, PLANT, 'Оператор линии', AS_OF + timedelta(days=60), count=16)
    jobs.refresh(sessions, as_of=AS_OF + timedelta(days=61))
    keys = [e.external_id for e in hiring_events(sessions) if e.inn == PLANT]
    assert keys == [f'vacancy_mass_hiring:inn:{PLANT}:*:2026-11', f'vacancy_mass_hiring:inn:{PLANT}:*:2027-01']


def test_hiring_signals_alone_create_no_card(sessions):
    add(sessions, PLANT, 'Оператор линии', date.today() - timedelta(days=3), count=16)
    add(sessions, NOINN, 'Оператор линии', date.today() - timedelta(days=3), code='0b1f0c7e-2', count=16)
    observe(sessions, f'inn:{PLANT}', date.today() - timedelta(days=3))
    observe(sessions, 'trudvsem:0b1f0c7e-2', date.today() - timedelta(days=3))
    jobs.refresh(sessions)
    with sessions() as db:
        cards = db.scalar(select(text('count(*)')).select_from(CandidateRecord))
    link_pending_events(sessions)
    with sessions() as db:
        statuses = dict(db.execute(text("SELECT coalesce(inn, 'none'), processing_status || ': ' || coalesce(processing_reason, '') "
                                        'FROM source_events WHERE source_id = :s'), {'s': SIGNAL_SOURCE}).all())
        vacancies = set(db.scalars(text("SELECT DISTINCT processing_status FROM source_events WHERE source_id = 'trudvsem'")))
        assert db.scalar(select(text('count(*)')).select_from(CandidateRecord)) == cards, 'hiring signals alone create no card'
    assert statuses[PLANT].startswith('waiting')
    assert statuses['none'].startswith('review') and 'нужно сопоставить с юрлицом' in statuses['none']
    assert vacancies == {'aggregated'}, 'single vacancies are never linked directly'


def test_worker_runs_hiring_analytics_after_sync(database_url, monkeypatch):
    import httpx
    from test_source_runner import make_api
    from test_trudvsem import PAGE
    from app.sources import base
    from app.worker import tick
    monkeypatch.setattr(base, 'pause', lambda seconds: None)
    monkeypatch.setenv('SOURCE_KINDS', 'trudvsem')
    with api_client(make_api(database_url, lambda request: httpx.Response(200, json=PAGE))) as api:
        sessions = api.app.state.sessions
        tick(sessions, api.app.state.llm, api.app.state.http, 5)
        with sessions() as db:
            snapshots = db.scalar(text('SELECT count(*) FROM vacancy_employer_snapshots WHERE snapshot_date = :d'), {'d': jobs.today()})
            daily = db.scalar(text("SELECT payload->>'date' FROM app_metadata WHERE key = 'hiring_daily'"))
        assert snapshots == 3 and daily == jobs.today().isoformat()
        listed = {s['id']: s for s in api.get('/api/sources').json()}
        source = listed[SIGNAL_SOURCE]
        assert source['lastRunStats']['status'] == 'ok' and source['lastRun'] != 'Ещё не запускался'
        assert listed['trudvsem']['hiring'] == {'vacancies': 4, 'activeVacancies': 3, 'employers': 3, 'signals': 0, 'activeSignals': 0}
        assert source['hiring']['signals'] == 0 and listed['oblgazeta']['hiring'] is None
        assert jobs.hiring_step(sessions) == {'employers': 0, 'signals': 0}, 'nothing new: nothing recomputed'
        api.post(f'/api/sources/{SIGNAL_SOURCE}/collect')
        assert jobs.hiring_step(sessions)['employers'] == 3, 'manual request recomputes everything'
