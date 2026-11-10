import copy
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.focus import FocusSettings, valid_inn
from app.llm import LlmSettings
from app.main import create_app
from app.risk import evaluate

EZOCM = json.loads((Path(__file__).parent / 'fixtures/focus_6661005707.json').read_text())
INN, BAD_INN = '6661005707', '7707083893'


def bad_company():
    raw = copy.deepcopy(EZOCM)
    raw['req']['inn'] = BAD_INN
    raw['req']['UL']['legalName'] = {'short': 'ООО "Рога и копыта"'}
    raw['req']['UL']['status'] = {'statusString': 'В стадии ликвидации', 'dissolving': True}
    raw['legalAnalytics']['legalAnalyticsData']['unreliableAddress'] = {'data': True, 'isActual': True}
    raw['fnsBlockedBankAccounts']['blockedAccountsInfo'] = {'totalCount': 2}
    raw['fsspAnalytics']['fsspAnalyticsData'].update(salaryCount={'data': 3}, salarySum={'data': 450000}, sum12Month={'data': 2.5e6}, count12Month={'data': 7})
    raw['scoring']['scoringData'][1].update(rating=12, ratingLevel='Low')
    return raw


class FakeFocus:
    def __init__(self):
        self.calls = []
        self.companies = {INN: EZOCM, BAD_INN: bad_company()}

    def __call__(self, request):
        method, inn = request.url.path.rsplit('/', 1)[-1], request.url.params['inn']
        assert request.url.params['key'] == 'test-key'
        self.calls.append((method, inn))
        company = self.companies.get(inn)
        return httpx.Response(200, json=[company[method]] if company else [])


def make_api(database_url, limit=950):
    focus = FakeFocus()
    app = create_app(database_url, worker=False, llm_settings=LlmSettings(), focus_transport=httpx.MockTransport(focus),
                     focus_settings=FocusSettings(api_key='test-key', inn_limit=limit))
    return app, focus


def test_inn_checksum():
    assert valid_inn(INN) and valid_inn('500100732259')
    assert not valid_inn('6661005708') and not valid_inn('12345')


def test_scoring_on_real_and_risky_company():
    real = evaluate(INN, EZOCM)
    assert real['verdict'] == 'yellow' and real['company'] == 'АО "ЕЗ ОЦМ"'
    codes = {f['code']: f['level'] for f in real['flags']}
    assert codes['stale_reporting'] == 'yellow' and codes['bankrupt_links'] == 'yellow'
    assert codes['long_history'] == codes['no_fssp'] == codes['no_blocks'] == 'green'
    risky = evaluate(BAD_INN, bad_company())
    reds = {f['code'] for f in risky['flags'] if f['level'] == 'red'}
    assert risky['verdict'] == 'red'
    assert {'status_dissolving', 'unreliable_egrul', 'blocked_accounts', 'salary_debts', 'focus_rating_low'} <= reds
    assert next(f for f in risky['flags'] if f['code'] == 'fssp_debts')['level'] == 'yellow', '2,5 млн при выручке 11 млрд — не стоп-фактор'
    assert [f['level'] for f in risky['flags']] == sorted((f['level'] for f in risky['flags']), key=['red', 'yellow', 'green'].index)


def test_risk_check_endpoint_cache_limit_and_export(database_url):
    app, focus = make_api(database_url, limit=1)
    with TestClient(app) as api:
        assert api.post('/api/candidates/INV-0254/risk-check', json={}).status_code == 400
        assert api.post('/api/candidates/INV-0254/risk-check', json={'inn': '6661005708'}).status_code == 400
        assert not focus.calls
        card = api.post('/api/candidates/INV-0254/risk-check', json={'inn': INN}).json()
        assert card['inn'] == INN and card['risk']['verdict'] == 'yellow'
        assert len(focus.calls) == 10
        api.post('/api/candidates/INV-0258/risk-check', json={'inn': INN})
        assert len(focus.calls) == 10, 'повторная проверка того же ИНН берётся из кеша'
        limited = api.post('/api/candidates/INV-0261/risk-check', json={'inn': BAD_INN})
        assert limited.status_code == 429 and 'лимит' in limited.json()['detail']
        assert api.get('/api/focus/usage').json() == {'configured': True, 'used': 1, 'limit': 1}
        assert api.get('/api/state').json()['pipeline']['focus']['used'] == 1
        csv = api.post('/api/exports').json()['csv']
        assert f'"{INN}"' in csv and '"Жёлтый"' in csv


def test_assistant_checks_company(database_url):
    focus = FakeFocus()
    replies = iter([
        {'content': '', 'tool_calls': [{'id': 'c1', 'type': 'function',
                                        'function': {'name': 'check_company', 'arguments': json.dumps({'id': 'INV-0261', 'inn': BAD_INN})}}]},
        {'content': 'Компания в красной зоне.'},
    ])

    def llm(request):
        return httpx.Response(200, json={'choices': [{'message': next(replies)}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    app = create_app(database_url, worker=False, llm_transport=httpx.MockTransport(llm),
                     llm_settings=LlmSettings(base_url='https://llm.test/v1', api_key='k'),
                     focus_transport=httpx.MockTransport(focus), focus_settings=FocusSettings(api_key='test-key'))
    with TestClient(app) as api:
        reply = api.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Проверь инициатора INV-0261, ИНН 7707083893'}]}).json()
        assert reply['changed'] and reply['trace'][0]['summary'].startswith('ООО "Рога и копыта": красная зона')
        assert api.get('/api/candidates/INV-0261').json()['risk']['verdict'] == 'red'
