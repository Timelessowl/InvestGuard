import httpx
from fastapi.testclient import TestClient

from app import clickbait, dzen
from app.focus import FocusSettings
from app.llm import LlmSettings
from app.main import create_app
from test_focus import FakeFocus, INN
from test_pipeline import PLANT, FakeLlm, fetch, rss

BAIT = 'Во-первых, это красиво: уникальный завод в Свердловской области, который может увидеть каждый'


def test_clickbait_headlines():
    assert clickbait.verdict(BAIT, strict=True).startswith('Кликбейт')
    assert clickbait.verdict('Вы не поверите, что построят в Екатеринбурге!!')
    assert clickbait.verdict('5 причин, почему завод закрылся', strict=True)
    for title in ('ЕЗ ОЦМ планирует вложить 3 млрд руб. в модернизацию', 'Запуск завода автозапчастей перенесли на 2027 год'):
        assert clickbait.verdict(title, strict=True) is None


def test_dzen_rows_cleaned_and_deduplicated():
    rows = [{'url': 'https://dzen.ru/a/abc?feed_exp=1&rid=2', 'title': 'Завод «Огнеупоры» в Свердловской области запустил линию',
             'snippet': 'Завод «Огнеупоры» в Свердловской области запустил линию\nОбластная газета\n1 неделю назад\nКомпания вложила 300 млн рублей.'},
            {'url': 'https://dzen.ru/a/abc?other=1', 'title': 'Завод «Огнеупоры» в Свердловской области запустил линию', 'snippet': ''}]
    items = dzen.to_items(rows)
    assert len(items) == 1 and items[0]['url'] == 'https://dzen.ru/a/abc'
    assert items[0]['body'] == 'Областная газета Компания вложила 300 млн рублей.'


def test_dzen_collection_and_strict_clickbait_filter(database_url, monkeypatch):
    monkeypatch.setattr(dzen, 'search', lambda queries: [
        {'title': BAIT, 'url': 'https://dzen.ru/a/bait', 'body': 'завод инвестиции млрд Екатеринбург', 'published_at': None},
        {'title': 'Литейный завод в Ревде построит цех', 'url': 'https://dzen.ru/a/plant', 'body': PLANT, 'published_at': None}])
    llm = FakeLlm()
    app = create_app(database_url, worker=False, llm_transport=httpx.MockTransport(llm), fetch_transport=httpx.MockTransport(fetch),
                     llm_settings=LlmSettings(base_url='https://llm.test/v1', api_key='k'), focus_settings=FocusSettings())
    with TestClient(app) as api:
        result = api.post('/api/sources/dzen/collect').json()
        assert result['added'] == 2 and result['source']['kind'] == 'dzen' and result['source']['status'] == 'active'
        assert api.post('/api/pipeline/process', json={'limit': 10}).json()['signal'] == 1
        docs = {d['url']: d for d in api.get('/api/documents?source=dzen').json()}
        assert docs['https://dzen.ru/a/bait']['stage'] == 'skipped' and docs['https://dzen.ru/a/bait']['reason'].startswith('Кликбейт')
        assert docs['https://dzen.ru/a/plant']['stage'] == 'signal'


def test_inn_in_news_triggers_focus_check(database_url):
    news = PLANT + f' Инициатор — АО «Ревдинский литейный завод» (ИНН {INN}).'

    def feed(request):
        return httpx.Response(200, text=rss(('Литейный завод в Ревде построит цех', 'https://oblgazeta.ru/inn', news)))
    focus = FakeFocus()
    app = create_app(database_url, worker=False, llm_transport=httpx.MockTransport(FakeLlm()), fetch_transport=httpx.MockTransport(feed),
                     llm_settings=LlmSettings(base_url='https://llm.test/v1', api_key='k'),
                     focus_transport=httpx.MockTransport(focus), focus_settings=FocusSettings(api_key='test-key'))
    with TestClient(app) as api:
        api.post('/api/sources/oblgazeta/collect')
        api.post('/api/pipeline/process', json={'limit': 5})
        card = next(c for c in api.get('/api/candidates').json() if c['origin'] == 'ai')
        assert card['inn'] == INN and card['risk']['verdict'] == 'yellow' and len(focus.calls) == 10


def test_feed_with_undeclared_prefix():
    from app.collectors import parse_feed
    xml = ('<rss><channel><item><title>Завод</title><link>https://uralinform.ru/1</link>'
           '<yandex:full-text>Полный текст новости</yandex:full-text></item></channel></rss>')
    assert parse_feed(xml)[0]['body'] == 'Полный текст новости'


def test_gigachat_refusal_rejects_document_once(database_url):
    llm = FakeLlm()

    def refusing(request):
        if 'response_format' in request.content.decode():
            llm.calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'К сожалению, ответы на вопросы, связанные с чувствительными темами, временно ограничены.'}}],
                                             'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
        return llm(request)
    app = create_app(database_url, worker=False, llm_transport=httpx.MockTransport(refusing), fetch_transport=httpx.MockTransport(fetch),
                     llm_settings=LlmSettings(base_url='https://llm.test/v1', api_key='k'), focus_settings=FocusSettings())
    with TestClient(app) as api:
        api.post('/api/sources/oblgazeta/collect')
        result = api.post('/api/pipeline/process', json={'limit': 10}).json()
        assert result['error'] == 0 and result['rejected'] == 2
        doc = next(d for d in api.get('/api/documents?source=oblgazeta').json() if d['url'] == 'https://oblgazeta.ru/1')
        assert doc['stage'] == 'rejected' and 'отказался' in doc['reason']


def test_broken_item_does_not_drop_feed():
    from app.collectors import parse_feed
    xml = ('<rss><channel><item><title>Первая</title><link>https://u.ru/1</link></item>'
           '<item><title>Битая & запись</title><link>https://u.ru/2</link></item>'
           '<item><title>Третья</title><link>https://u.ru/3</link></item></channel></rss>')
    assert [i['url'] for i in parse_feed(xml)] == ['https://u.ru/1', 'https://u.ru/3']
