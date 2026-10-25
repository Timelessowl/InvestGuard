from app.collectors import CollectError, parse_feed


def rss(*items, full_text=True):
    tag = 'yandex:full-text' if full_text else 'description'
    body = ''.join(f'<item><title>{t}</title><link>{u}</link><pubDate>Thu, 29 Oct 2026 10:00:00 +0500</pubDate>'
                   f'<description>{t}</description><{tag}>{b}</{tag}></item>' for t, u, b in items)
    return f'<?xml version="1.0"?><rss xmlns:yandex="http://news.yandex.ru"><channel>{body}</channel></rss>'



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

