"""API integration tests use a disposable PostgreSQL database, never the demo database."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.database import make_engine
from app.main import create_app


@pytest.fixture
def client():
    admin = make_engine()
    name = 'investsignal_test_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'CREATE DATABASE {name}'))
    url = admin.url.set(database=name).render_as_string(hide_password=False)
    try:
        with TestClient(create_app(url)) as api:
            yield api, url
    finally:
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()


INPUT = {'company': 'ООО «Тестовый проект»', 'city': 'Екатеринбург', 'sector': 'Логистика',
         'signal': 'Компания планирует построить новый склад', 'source': 'Пресс-релиз компании'}


def test_seed_and_restart_preserve_changes(client):
    api, url = client
    assert api.get('/api/health').json()['database'] == 'ok'
    state = api.get('/api/state').json()
    assert len(state['candidates']) == 6
    assert len(state['sources']) == 4
    assert state['pilotAnalytics']['confirmationRate'] == '41%'
    assert state['candidates'][0]['location']['precision'] == 'municipality'
    api.patch('/api/candidates/INV-0261', json={'owner': 'М. Орлова', 'status': 'approved'})
    with TestClient(create_app(url)) as restarted:
        card = restarted.get('/api/candidates/INV-0261').json()
        assert card['owner'] == 'М. Орлова'
        assert card['status'] == 'approved'
        assert len(restarted.get('/api/candidates').json()) == 6


def test_concurrent_creates_and_patches(client):
    api, _ = client
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: api.post('/api/candidates', json=INPUT), range(6)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()['id'] for r in responses}) == 6
    candidate_id = responses[0].json()['id']
    assert api.get('/api/candidates/' + candidate_id).json()['location'] is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda patch: api.patch('/api/candidates/' + candidate_id, json=patch),
                                  [{'owner': 'И. Титов'}, {'nextAction': 'Передать эксперту'}]))
    assert all(r.status_code == 200 for r in responses)
    saved = api.get('/api/candidates/' + candidate_id).json()
    assert saved['owner'] == 'И. Титов'
    assert saved['nextAction'] == 'Передать эксперту'


def test_validation_unknown_ids_and_export_only_status(client):
    api, _ = client
    assert api.post('/api/candidates', json={**INPUT, 'company': '  '}).status_code == 422
    for patch in [{}, {'owner': None}, {'status': 'exported'}, {'score': 99}]:
        assert api.patch('/api/candidates/INV-0261', json=patch).status_code == 422
    assert api.patch('/api/candidates/missing', json={'status': 'review'}).status_code == 404
    assert api.get('/api/candidates/INV-99999999999999999999999999999999999').status_code == 404
    assert api.post('/api/sources/missing/retry').status_code == 404


def test_atomic_approved_export_and_repeat_download(client):
    api, _ = client
    created = api.post('/api/candidates', json={**INPUT, 'company': '=DANGEROUS()'}).json()
    api.patch('/api/candidates/' + created['id'], json={'status': 'approved'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.post('/api/exports'), range(2)))
    assert sorted(r.status_code for r in responses) == [201, 409]
    exported = next(r.json() for r in responses if r.status_code == 201)
    assert exported['count'] == 2
    assert 'Ревдинские приводы' in exported['csv']
    assert 'Урал Композит' not in exported['csv']
    assert "'=DANGEROUS()" in exported['csv']
    assert api.get('/api/candidates/' + created['id']).json()['status'] == 'exported'
    download = api.get('/api/exports/' + exported['exportId'])
    assert download.text == exported['csv']
    assert download.headers['content-type'].startswith('text/csv')


def test_simulated_retry_does_not_invent_documents(client):
    api, _ = client
    retried = api.post('/api/sources/jobs/retry').json()
    assert retried['status'] == 'active'
    assert retried['documents'] == 213
    assert retried['lastRun'] != '24 сен, 08:50'
    assert next(s for s in api.get('/api/sources').json() if s['id'] == 'jobs') == retried
