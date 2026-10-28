"""API integration tests use a disposable PostgreSQL database, never the demo database."""
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app


INPUT = {'company': 'ООО «Тестовый проект»', 'city': 'Екатеринбург', 'sector': 'Логистика',
         'signal': 'Компания планирует построить новый склад', 'source': 'Пресс-релиз компании'}


def test_seed_and_restart_preserve_changes(client):
    api, url = client
    assert api.get('/api/health').json()['database'] == 'ok'
    state = api.get('/api/state').json()
    assert len(state['candidates']) == 6
    assert [s['id'] for s in state['sources']] == ['66ru', 'e1', 'justmedia', 'kommersant-ural', 'oblgazeta', 'ura']
    assert state['pipeline']['usage']['configured'] is False
    assert state['pilotAnalytics']['confirmationRate'] == '41%'
    assert state['candidates'][0]['location']['precision'] == 'municipality'
    api.patch('/api/candidates/INV-0261', json={'owner': 'М. Орлова', 'status': 'approved'})
    with TestClient(create_app(url, worker=False)) as restarted:
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
    assert api.post('/api/sources/missing/collect').status_code == 404


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
    locked = api.patch('/api/candidates/' + created['id'], json={'status': 'review'})
    assert locked.status_code == 409
    assert api.patch('/api/candidates/' + created['id'], json={'owner': 'И. Титов'}).status_code == 200
    download = api.get('/api/exports/' + exported['exportId'])
    assert download.text == exported['csv']
    assert download.headers['content-type'].startswith('text/csv')
