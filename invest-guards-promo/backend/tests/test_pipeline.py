import hashlib
import json
import math

import httpx
from fastapi.testclient import TestClient

from app.collectors import CollectError, parse_feed
from app.llm import LlmSettings
from app.main import create_app

PLANT = ('АО «Ревдинский литейный завод» начинает строительство нового цеха точного литья в Ревде. '
         'Инвестиции составят 1,2 млрд рублей, запуск производства — в 2027 году.')
SCHOOL = 'В Екатеринбурге продолжается строительство школы, инвестиции из бюджета составят 900 млн рублей.'
WEATHER = 'Синоптики обещают снег в выходные.'


def rss(*items, full_text=True):
    tag = 'yandex:full-text' if full_text else 'description'
    body = ''.join(f'<item><title>{t}</title><link>{u}</link><pubDate>Thu, 29 Oct 2026 10:00:00 +0500</pubDate>'
                   f'<description>{t}</description><{tag}>{b}</{tag}></item>' for t, u, b in items)
    return f'<?xml version="1.0"?><rss xmlns:yandex="http://news.yandex.ru"><channel>{body}</channel></rss>'


FEEDS = {
    'www.oblgazeta.ru': rss(('Литейный завод в Ревде построит цех', 'https://oblgazeta.ru/1', PLANT),
                            ('Строят школу', 'https://oblgazeta.ru/2', SCHOOL), ('Погода', 'https://oblgazeta.ru/3', WEATHER)),
    'www.e1.ru': rss(('Литейный завод в Ревде построит цех', 'https://e1.ru/9', PLANT)),
    'www.justmedia.ru': 'not xml',
}


def fetch(request):
    if request.url.host == 'www.justmedia.ru':
        return httpx.Response(500)
    return httpx.Response(200, text=FEEDS.get(request.url.host, rss()))


def vector(content):
    values = [0.0] * 32
    for word in content.lower().split():
        values[int(hashlib.md5(word.encode()).hexdigest(), 16) % 32] += 1
    return values


class FakeLlm:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        if request.url.path.endswith('/embeddings'):
            data = [{'index': i, 'embedding': vector(t)} for i, t in enumerate(body['input'])]
            return httpx.Response(200, json={'data': data, 'usage': {'prompt_tokens': 10}})
        user = body['messages'][-1]['content']
        if 'response_format' in body:
            props = body['response_format']['json_schema']['schema']['properties']
            if 'is_investment_signal' in props:
                signal = 'литейный' in user.lower()
                result = {'is_investment_signal': signal, 'in_sverdlovsk_region': True,
                          'company': 'АО «Ревдинский литейный завод»' if signal else '', 'municipality': 'Ревде',
                          'signal_title': 'Строительство цеха точного литья', 'signal_type': 'Новый объект',
                          'amount_rub': 1.2e9, 'key_quote': 'начинает строительство нового цеха точного литья', 'confidence': 85,
                          'reason': 'Компания объявила о строительстве цеха' if signal else 'Бюджетный объект'}
            else:
                result = {'company': 'АО «Ревдинский литейный завод»', 'sector': 'Металлургия', 'summary': 'Компания строит цех.',
                          'amount_display': '1,2 млрд ₽', 'stage': 'Строительство', 'relevance': 90, 'confidence': 80,
                          'regional_effect': 70, 'urgency': 75, 'recommendation': 'Связаться с компанией',
                          'rationale': 'Ранняя стадия.', 'next_action': 'Связаться с компанией'}
            return self.reply({'content': json.dumps(result, ensure_ascii=False)})
        if body['messages'][-1]['role'] == 'tool':
            return self.reply({'content': 'Найдена карточка INV-0254.'})
        if 'одобри' in user.lower():
            call = {'name': 'update_candidate', 'arguments': json.dumps({'id': 'INV-0261', 'status': 'approved'})}
        else:
            call = {'name': 'search_candidates', 'arguments': json.dumps({'query': 'Ревда'})}
        return self.reply({'content': '', 'tool_calls': [{'id': 'c1', 'type': 'function', 'function': call}]})

    @staticmethod
    def reply(message):
        return httpx.Response(200, json={'choices': [{'message': message}], 'usage': {'prompt_tokens': 300, 'completion_tokens': 100}},
                              headers={'x-litellm-response-cost': '0'})


def make_api(database_url, budget=10.0):
    llm = FakeLlm()
    settings = LlmSettings(base_url='https://llm.test/v1', api_key='test', budget_usd=budget, daily_budget_usd=budget)
    app = create_app(database_url, llm_transport=httpx.MockTransport(llm), fetch_transport=httpx.MockTransport(fetch),
                     llm_settings=settings, worker=False)
    return app, llm


def test_parse_feed_handles_rss_atom_and_rejects_entities():
    items = parse_feed(rss(('Заголовок', 'https://x.ru/1', '<p>Полный &amp; текст</p>')))
    assert items[0]['body'] == 'Полный & текст' and items[0]['published_at'].year == 2026
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link href="https://x.ru/a"/>'
            '<summary>S</summary><updated>2026-10-29T10:00:00Z</updated></entry></feed>')
    assert parse_feed(atom)[0]['url'] == 'https://x.ru/a'
    try:
        parse_feed('<!DOCTYPE x [<!ENTITY a "b">]><rss/>')
    except CollectError:
        pass
    else:
        raise AssertionError('entities must be rejected')


def test_collect_process_dedupe_and_card(database_url):
    app, llm = make_api(database_url)
    with TestClient(app) as api:
        first = api.post('/api/sources/oblgazeta/collect').json()
        assert first['added'] == 3 and first['source']['documents'] == 3
        assert api.post('/api/sources/oblgazeta/collect').json()['added'] == 0
        broken = api.post('/api/sources/justmedia/collect').json()['source']
        assert broken['status'] == 'retry' and broken['lastError'] == 'HTTP 500'
        result = api.post('/api/pipeline/process', json={'limit': 10}).json()
        assert result['signal'] == 1 and result['rejected'] == 1 and result['skipped'] == 1
        api.post('/api/sources/e1/collect')
        assert api.post('/api/pipeline/process', json={'limit': 10}).json()['merged'] == 1
        triage_calls = [c for c in llm.calls if 'is_investment_signal' in c.get('response_format', {}).get('json_schema', {}).get('schema', {}).get('properties', {})]
        assert len(triage_calls) == 2, 'дубль из второго источника не должен идти в LLM'
        card = next(c for c in api.get('/api/candidates').json() if c['origin'] == 'ai')
        assert card['city'] == 'Ревда' and card['location']['precision'] == 'municipality'
        assert len(card['evidence']) == 2 and card['evidence'][0]['url'] == 'https://oblgazeta.ru/1'
        assert card['factors']['Уверенность'] == 86 and card['score'] == 82
        docs = {d['url']: d for d in api.get('/api/documents').json()}
        assert docs['https://e1.ru/9']['stage'] == 'merged' and docs['https://e1.ru/9']['candidateId'] == card['id']
        assert docs['https://oblgazeta.ru/3']['reason'] == 'Нет признаков инвестпроекта'
        usage = api.get('/api/llm/usage').json()
        assert usage['calls'] == len(llm.calls) and usage['spentUsd'] > 0 and set(usage['byPurpose']) == {'embedding', 'triage', 'scoring'}


def test_budget_stops_processing(database_url):
    app, llm = make_api(database_url, budget=0.0005)
    with TestClient(app) as api:
        api.post('/api/sources/oblgazeta/collect')
        result = api.post('/api/pipeline/process', json={'limit': 10}).json()
        assert 'бюджет' in result['stopped']
        assert api.post('/api/pipeline/process', json={'limit': 10}).status_code == 429
        assert not any(c['origin'] == 'ai' for c in api.get('/api/candidates').json())


def test_chat_uses_tools_and_reports_changes(database_url):
    app, llm = make_api(database_url)
    with TestClient(app) as api:
        reply = api.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Что есть по Ревде?'}]}).json()
        assert reply['reply'] == 'Найдена карточка INV-0254.' and not reply['changed']
        assert reply['trace'][0]['name'] == 'search_candidates' and reply['trace'][0]['summary'] == 'Найдено карточек: 1'
        tool_message = llm.calls[-1]['messages'][-1]
        assert tool_message['role'] == 'tool' and 'Ревдинские приводы' in json.loads(tool_message['content'])['result']['items'][0]['company']
        changed = api.post('/api/chat', json={'messages': [{'role': 'user', 'content': 'Одобри INV-0261'}]}).json()
        assert changed['changed'] and api.get('/api/candidates/INV-0261').json()['status'] == 'approved'
