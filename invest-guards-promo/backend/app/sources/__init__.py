"""Source adapter registry: one adapter per source kind."""
import importlib

from .base import (Batch, EventItem, SourceAccessDenied, SourceAdapter, SourceDataError, SourceError, SourceNotConfigured,
                   SourceRateLimited)

REGISTRY: dict[str, type[SourceAdapter]] = {}
MODULES = ('news', 'imports')
# Kinds reserved for later prompts: shown as "not configured", never as a working integration.
PLANNED = {
    'trudvsem': 'Адаптер «Работы России» ещё не подключён.',
    'hh': 'Адаптер HeadHunter не подключён: нужен доступ к API hh.ru.',
    'torgi': 'Адаптер ГИС Торги ещё не подключён.',
    'egrz': 'Адаптер ЕГРЗ ещё не подключён.',
}


def register(kind):
    def decorate(cls):
        cls.kind = kind
        REGISTRY[kind] = cls
        return cls
    return decorate


def _load():
    # Adapter modules import collectors, which imports this package: load them lazily.
    for name in MODULES:
        importlib.import_module(f'{__name__}.{name}')


def adapter_class(kind):
    _load()
    if kind in REGISTRY:
        return REGISTRY[kind]
    if kind in PLANNED:
        return PlannedAdapter
    raise LookupError(f'Адаптер для типа источника «{kind}» не найден.')


def adapter_for(source, http):
    return adapter_class(source.get('kind') or 'rss')(source, http)


class PlannedAdapter(SourceAdapter):
    kind = 'planned'
    produces = 'events'

    def availability(self):
        return 'not_configured'

    def not_ready_message(self):
        return PLANNED.get(self.source.get('kind'), 'Адаптер не подключён.')

    def fetch(self, cursor):
        raise SourceNotConfigured(self.not_ready_message())


__all__ = ['Batch', 'EventItem', 'SourceAccessDenied', 'SourceAdapter', 'SourceDataError', 'SourceError', 'SourceNotConfigured',
           'SourceRateLimited', 'adapter_class', 'adapter_for', 'register']
