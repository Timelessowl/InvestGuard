"""Test-only source adapters and helpers; external APIs are replaced by httpx.MockTransport."""
import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.database import CandidateRecord
from app.sources import register
from app.sources.base import Batch, EventItem, SourceAdapter, keep_fields

RECENT = (date.today() - timedelta(days=10)).isoformat()


def make_inn(prefix):
    """Valid 10-digit INN for a 9-digit prefix."""
    digits = [int(c) for c in prefix]
    check = sum(w * d for w, d in zip([2, 4, 10, 3, 5, 9, 4, 6, 8], digits)) % 11 % 10
    return prefix + str(check)


def item_from(row):
    return EventItem(event_type=row['type'], title=row['title'], external_id=row.get('id'), company_name=row.get('company'),
                     inn=row.get('inn'), municipality=row.get('municipality'), address=row.get('address'),
                     event_date=date.fromisoformat(row['date']) if row.get('date') else None, status=row.get('status'),
                     value=Decimal(str(row['value'])) if row.get('value') is not None else None, value_unit=row.get('unit'),
                     metrics=tuple(row.get('metrics', ())), url=row.get('url'),
                     raw=keep_fields(row, ['id', 'type', 'title', 'company', 'inn', 'date', 'status', 'value', 'employer.name']))


@register('fake')
class FakeAdapter(SourceAdapter):
    produces = 'events'
    retry_delay = 0

    def fetch(self, cursor):
        page = (cursor or {}).get('page', 0)
        while True:
            data = self.request_json(self.source['url'], {'page': page})
            if self.options.get('explode') and page == 1:
                raise RuntimeError('unexpected adapter bug')
            page += 1
            yield Batch(events=[item_from(row) for row in data['items']], cursor={'page': page}, snapshot_complete=not data.get('more'))
            if not data.get('more'):
                return

    def check(self):
        data = self.request_json(self.source['url'], {'limit': 1}, attempts=1, timeout=10)
        return True, f'Доступно, записей: {len(data["items"])}'


@register('fake-keyed')
class KeyedAdapter(FakeAdapter):
    required_env = ('FAKE_SOURCE_KEY',)


def add_source(sessions, source_id, kind='fake', url='https://events.test/api', **extra):
    payload = {'id': source_id, 'name': f'Тестовый {source_id}', 'category': 'Тест', 'url': url, 'regional': True,
               'scheduleMinutes': 60, 'enabled': True, 'kind': kind, 'group': 'hiring', 'schedule': 'Каждый час',
               'lastRun': 'Ещё не запускался', 'lastRunAt': None, 'lastError': None, 'status': 'active', **extra}
    with sessions() as db:
        db.execute(text('INSERT INTO sources (id, payload) VALUES (:id, CAST(:p AS jsonb))'), {'id': source_id, 'p': json.dumps(payload)})
        db.commit()


def set_card(sessions, card_id, **fields):
    with sessions() as db:
        record = db.get(CandidateRecord, int(card_id.split('-')[1]))
        record.payload = {**record.payload, **fields}
        db.commit()


class Api:
    """Answers with queued responses; counts requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        import httpx
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(response):
            return response(request)
        status, body, *rest = response
        headers = rest[0] if rest else None
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(status, text=body, headers=headers)
