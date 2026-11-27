"""Virtual sources computed from stored data (hiring signals). Nothing is fetched: the worker writes their events."""
from . import register
from .base import SourceAdapter


@register('derived')
class DerivedAdapter(SourceAdapter):
    produces = 'events'
    scheduled = False  # recomputed by vacancies.jobs after vacancy syncs and once a day

    def availability(self):
        return 'ready'

    def check(self):
        return True, 'Сигналы рассчитываются из загруженных вакансий, внешних запросов нет.'

    def fetch(self, cursor):
        return iter(())
