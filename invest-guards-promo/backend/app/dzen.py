"""Dzen collector. Dzen has no public RSS or API, so search pages are rendered in a headless browser like a reader would;
a captcha stops the run instead of being worked around. Clickbait is filtered later in the pipeline prefilter."""
import logging
import os
import re
from urllib.parse import quote, urlsplit, urlunsplit

from .collectors import CollectError

log = logging.getLogger(__name__)
SEARCH_URL = 'https://dzen.ru/search?query={}'
EXTRACT_JS = r"""() => [...document.querySelectorAll('a[href]')]
  .filter(a => /dzen\.ru\/a\//.test(a.href) && a.innerText.trim().length > 25)
  .map(a => {
    let card = a;
    for (let i = 0; i < 6 && card.parentElement && card.innerText.length < 200; i++) card = card.parentElement;
    return { url: a.href, title: a.innerText.trim(), snippet: card.innerText.slice(0, 800) };
  })"""
NOISE = re.compile(r'\b(\d+\s+(смотрели|прочтени\w*|просмотр\w*)|\d+\s+(минут\w*|час\w*|дн\w*|недел\w*|месяц\w*|год\w*|лет)\s+назад|вчера|сегодня)\b', re.I)


def clean_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def to_items(rows, limit=None):
    items, seen = [], set()
    for row in rows:
        url, title = clean_url(row.get('url', '')), re.sub(r'\s+', ' ', row.get('title', '')).strip()
        if not url or url in seen or not title:
            continue
        seen.add(url)
        lines = [line.strip() for line in (row.get('snippet') or '').split('\n') if line.strip() and line.strip() != title]
        body = ' '.join(line for line in lines if not NOISE.fullmatch(line) and len(line) > 3)[:2000]
        items.append({'title': title[:500], 'url': url, 'body': body, 'published_at': None})
        if limit and len(items) >= limit:
            break
    return items


def search(queries, per_query=15, timeout_ms=30000):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise CollectError('Сбор Дзена идёт в отдельном сервисе dzen (нужен Playwright).') from error
    from playwright.sync_api import Error as PlaywrightError
    rows = []
    channel = os.environ.get('DZEN_BROWSER_CHANNEL') or None
    try:
        with sync_playwright() as playwright:
            # A system Chrome no longer has the old headless mode Playwright 1.47 asks for, so request the new one explicitly.
            options = {'channel': channel, 'headless': False, 'args': ['--headless=new']} if channel else {'headless': True}
            browser = playwright.chromium.launch(**options)
            try:
                page = browser.new_page(locale='ru-RU')
                for query in queries:
                    page.goto(SEARCH_URL.format(quote(query)), wait_until='domcontentloaded', timeout=timeout_ms)
                    page.wait_for_timeout(3000)
                    if 'captcha' in page.url or 'showcaptcha' in page.content()[:5000]:
                        raise CollectError('Дзен запросил капчу — сбор остановлен.')
                    found = page.evaluate(EXTRACT_JS)
                    rows.extend(found[:per_query * 2])
                    log.info('Dzen query %r: %s links', query, len(found))
            finally:
                browser.close()
    except PlaywrightError as error:
        raise CollectError(f'Браузер для Дзена не запустился: {str(error).splitlines()[0][:200]}') from error
    return to_items(rows)
