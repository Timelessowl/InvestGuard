"""Rule-based linking of events to candidate cards, no AI. The first matching rule wins."""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from .common import TZ, display_day, initials, overall_score, urgency_for
from .database import CandidateEventRecord, CandidateRecord, EventRecord, EventTypeRecord, SourceRecord, insert_candidate
from .focus import FocusError
from .geo import canonical_municipality, lookup
from .pipeline import STAGES, attach_evidence
from .schemas import Candidate

log = logging.getLogger(__name__)
LOCK_ID = 1847303
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = {'windowDays': 180, 'minCategoriesForCard': 2, 'maxReviewSuggestions': 5}
# Neutral placeholder factors until lead scoring replaces them; the card says so in its rationale.
EVENT_FACTORS = {'Релевантность': 60, 'Уверенность': 60, 'Региональный эффект': 50, 'Срочность': 55}


def load_rules():
    path = os.environ.get('LINKING_RULES_PATH') or ROOT / 'seed/linking_rules.json'
    return {**DEFAULT_RULES, **json.loads(Path(path).read_text())}


def occurred(event):
    return event.event_date or event.collected_at.astimezone(TZ).date()


def event_excerpt(event):
    parts = [event.title]
    if event.value is not None:
        parts.append(f'{event.value.normalize():f} {event.value_unit or ""}'.strip())
    parts += [f'{m.get("title") or m.get("name")}: {m.get("value")} {m.get("unit") or ""}'.strip() for m in (event.metrics or [])[:3]
              if isinstance(m, dict) and m.get('value') is not None]
    if event.address:
        parts.append(event.address)
    return '. '.join(p for p in parts if p)[:400]


def evidence_for_event(event, kind, source_name):
    return {'source': f'{source_name} · {kind.title if kind else event.event_type}', 'date': display_day(occurred(event)),
            'excerpt': event_excerpt(event), 'highlights': [], 'url': event.url, 'kind': 'event', 'eventId': event.id,
            'eventType': event.event_type, 'occurredAt': occurred(event).isoformat()}


def reference(db):
    """Event types and source names, loaded once per linking batch."""
    return ({t.code: t for t in db.scalars(select(EventTypeRecord))},
            {r.id: r.payload.get('name', r.id) for r in db.scalars(select(SourceRecord))})


class Linker:
    def __init__(self, db, rules=None, focus=None, types=None, sources=None):
        self.db, self.rules, self.focus = db, rules or load_rules(), focus
        if types is None or sources is None:
            types, sources = reference(db)
        self.types, self.sources = types, sources

    def finish(self, event, status, reason=None):
        event.processing_status, event.processing_reason, event.processed_at = status, (reason or '')[:500] or None, datetime.now(timezone.utc)
        return status

    def link(self, record, event, confidence, reason):
        self.db.execute(insert(CandidateEventRecord).values(candidate_number=record.number, event_id=event.id, confidence=confidence,
                                                            linked_by='rule', reason=reason)
                        .on_conflict_do_update(index_elements=['candidate_number', 'event_id'],
                                               set_={'confidence': confidence, 'reason': reason}))
        if confidence != 'manual_review':
            attach_evidence(record, evidence_for_event(event, self.types.get(event.event_type), self.sources.get(event.source_id, event.source_id)))

    def suggest(self, records, event, reason):
        for record in records[:self.rules['maxReviewSuggestions']]:
            self.link(record, event, 'manual_review', reason)
        return self.finish(event, 'review', reason)

    def run(self, event):
        # A re-evaluated event (content changed) drops its old rule links, analyst links stay.
        self.db.execute(delete(CandidateEventRecord).where(CandidateEventRecord.event_id == event.id, CandidateEventRecord.linked_by == 'rule'))
        kind = self.types.get(event.event_type)
        policy = kind.link_policy if kind else 'company'
        if policy == 'aggregate':
            return self.finish(event, 'aggregated', 'Учитывается только в агрегированных сигналах')
        if policy == 'context':
            return self.finish(event, 'context', 'Контекстное событие: компания не привязывается')
        if not event.inn:
            reason = 'Есть только ОГРН: карточки сопоставляются по ИНН' if event.ogrn else 'Нет идентификатора юрлица (ИНН или ОГРН)'
            return self.finish(event, 'review', reason)
        records = list(self.db.scalars(select(CandidateRecord).where(CandidateRecord.inn_key == event.inn)
                                       .order_by(CandidateRecord.number).with_for_update()))
        if not records:
            return self.threshold(event)
        place = event.municipality
        if len(records) == 1:
            record = records[0]
            city = canonical_municipality(record.payload.get('city'))
            if not place or not city or city == place:
                self.link(record, event, 'confirmed', 'Совпал ИНН' + (' и муниципалитет' if place and city else ''))
                return self.finish(event, 'linked', f'Добавлено к {record.payload["id"]}')
            return self.suggest(records, event, f'Возможно, другой проект компании: событие в {place}, карточка — {city}')
        matches = [r for r in records if place and canonical_municipality(r.payload.get('city')) == place]
        if len(matches) == 1:
            self.link(matches[0], event, 'probable', 'Совпали ИНН и муниципалитет, у компании несколько карточек')
            return self.finish(event, 'linked', f'Добавлено к {matches[0].payload["id"]}')
        reason = 'Несколько карточек компании, площадку однозначно определить нельзя'
        return self.suggest(matches or records, event, reason)

    def threshold(self, event):
        """No card for this INN: a new one only when enough independent signs agree in one place."""
        window, need = self.rules['windowDays'], self.rules['minCategoriesForCard']
        since = date.today() - timedelta(days=window)
        group = list(self.db.scalars(
            select(EventRecord).join(EventTypeRecord, EventTypeRecord.code == EventRecord.event_type)
            .where(EventRecord.inn == event.inn, EventTypeRecord.polarity == 'positive', EventTypeRecord.link_policy == 'company',
                   EventRecord.processing_status.in_(['new', 'waiting']),
                   func.coalesce(EventRecord.event_date, func.date(EventRecord.collected_at)) >= since)
            .order_by(EventRecord.id).with_for_update(of=EventRecord)))
        if event not in group:
            return self.finish(event, 'waiting', 'Ждёт карточку компании: само по себе событие карточку не создаёт')
        places = {e.municipality for e in group if e.municipality}
        if len(places) > 1:
            return self.finish(event, 'review', 'События компании в разных муниципалитетах: ' + ', '.join(sorted(places)))
        categories = {self.types[e.event_type].category for e in group}
        if len(categories) < need:
            return self.finish(event, 'waiting', f'Недостаточно признаков проекта: категорий {len(categories)} из {need} за {window} дней')
        record = self.create_card(group, places)
        for item in group:
            self.link(record, item, 'confirmed', f'Порог: категорий событий {len(categories)} за {window} дней')
            self.finish(item, 'linked', f'Создана карточка {record.payload["id"]}')
        return 'linked'

    def create_card(self, group, places):
        first = group[0]
        place = next(iter(places), None)
        company = next((e.company_name for e in group if e.company_name), None) or f'ИНН {first.inn}'
        kinds = [self.types[e.event_type] for e in group]
        stages = [k.stage for k in kinds if k.stage in STAGES]
        titles = sorted({k.title for k in kinds})
        latest = max(group, key=occurred)
        urgency, label = urgency_for(EVENT_FACTORS['Срочность'])
        facts = '; '.join(f'{display_day(occurred(e))} — {self.types[e.event_type].title.lower()}: {e.title}' for e in group[:5])
        candidate = Candidate(
            id='pending', company=company, initials=initials(company), sector='Не определена', city=place or 'Не указан',
            location=lookup(place) if place else None, signalType='События из источников', signal='; '.join(titles)[:160],
            source=self.sources.get(latest.source_id, latest.source_id), sourceDate=display_day(occurred(latest)),
            score=overall_score(EVENT_FACTORS), urgency=urgency, urgencyLabel=label, status='new', owner='Не назначен',
            amount='Не указано', stage=max(stages, key=STAGES.index) if stages else 'Неизвестно',
            summary=f'Карточка собрана из {len(group)} событий по ИНН {first.inn}. {facts}.'[:1500],
            factors=dict(EVENT_FACTORS), recommendation='Проверить события и запросить у компании сведения о проекте',
            rationale=(f'Не меньше {self.rules["minCategoriesForCard"]} разных категорий положительных событий за '
                       f'{self.rules["windowDays"]} дней в одном муниципалитете. Оценка предварительная, по правилам без ИИ.'),
            nextAction='Запросить данные', evidence=[], origin='events', inn=first.inn)
        record = insert_candidate(self.db, candidate)
        self.check_focus(record)
        return record

    def check_focus(self, record):
        # Off by default: thousands of employer INNs would exhaust the Focus company limit in a day.
        if os.environ.get('FOCUS_AUTO_CHECK_EVENTS', '0') != '1' or not (self.focus and self.focus.settings.configured):
            return
        try:
            record.payload = {**record.payload, 'risk': self.focus.check(record.payload['inn'])}
        except FocusError as error:
            log.warning('Focus check for %s failed: %s', record.number, error)


def wake_waiting(db):
    """Waiting events of a company that now has a card are evaluated again."""
    return db.execute(update(EventRecord).where(EventRecord.processing_status == 'waiting',
                                                EventRecord.inn.in_(select(CandidateRecord.inn_key).where(CandidateRecord.inn_key.is_not(None))))
                      .values(processing_status='new')).rowcount


def link_pending_events(sessions, limit=None, focus=None):
    limit = limit or int(os.environ.get('EVENT_LINK_BATCH', 200))
    counts = {'processed': 0, 'linked': 0, 'waiting': 0, 'review': 0, 'context': 0, 'aggregated': 0, 'error': 0}
    with sessions() as lock:
        if not lock.execute(text('SELECT pg_try_advisory_lock(:id)'), {'id': LOCK_ID}).scalar():
            return {**counts, 'busy': True}
        try:
            with sessions() as db:
                counts['woken'] = wake_waiting(db)
                db.commit()
                ids = db.scalars(select(EventRecord.id).where(EventRecord.processing_status == 'new')
                                 .order_by(EventRecord.collected_at, EventRecord.id).limit(limit)).all()
                rules = load_rules()
                types, sources = reference(db)
            for event_id in ids:
                with sessions() as db:
                    event = db.get(EventRecord, event_id, with_for_update=True)
                    if event is None or event.processing_status != 'new':
                        continue
                    try:
                        status = Linker(db, rules, focus, types, sources).run(event)
                        event.attempts = 0
                    except Exception as error:  # noqa: BLE001 - one bad event must not stop the batch
                        db.rollback()
                        log.exception('Linking event %s failed', event_id)
                        event = db.get(EventRecord, event_id, with_for_update=True)
                        event.attempts += 1
                        status = 'error' if event.attempts >= 3 else 'new'
                        event.processing_status = status
                        event.processing_reason = f'Ошибка связывания: {type(error).__name__}'
                    db.commit()
                    counts['processed'] += 1
                    counts[status if status in counts else 'error'] += 1
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': LOCK_ID})
            lock.commit()
    return counts
