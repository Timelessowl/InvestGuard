"""RSS and Dzen adapters: thin wrappers over collectors.fetch_items, parsing is unchanged."""
import importlib.util

import httpx

from .. import collectors
from . import register
from .base import Batch, SourceAdapter, SourceError


def http_failure(error):
    # Same messages as before the adapters; a feed's 403 is usually anti-bot, so it stays retryable.
    if isinstance(error, httpx.HTTPStatusError):
        return SourceError(f'HTTP {error.response.status_code}')
    return SourceError(str(error) or type(error).__name__)


@register('rss')
class RssAdapter(SourceAdapter):
    produces = 'documents'

    def availability(self):
        return 'ready' if self.source.get('url', '').startswith('http') else 'not_configured'

    def not_ready_message(self):
        return 'Требуется настройка: не задан адрес ленты.'

    def fetch(self, cursor):
        try:
            items = collectors.fetch_items(self.source, self.http)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise http_failure(error) from error
        yield Batch(documents=items)

    def check(self):
        if self.availability() != 'ready':
            return False, self.not_ready_message()
        try:
            response = self.http.get(self.source['url'], timeout=10)
            response.raise_for_status()
            items = collectors.parse_feed(response.text)
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            raise http_failure(error) from error
        return True, f'Лента доступна, записей: {len(items)}.'


@register('dzen')
class DzenAdapter(SourceAdapter):
    produces = 'documents'

    def fetch(self, cursor):
        yield Batch(documents=collectors.fetch_items(self.source, self.http))

    def check(self):
        # No network here: a test search could trigger Dzen's captcha.
        if importlib.util.find_spec('playwright') is None:
            return False, 'Сбор Дзена идёт в отдельном сервисе dzen (нужен Playwright).'
        return True, 'Браузер для поиска Дзена установлен. Доступность проверяется при сборе.'
