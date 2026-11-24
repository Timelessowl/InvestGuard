import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select, text

from .common import display_date, initials
from .database import CandidateRecord, DocumentRecord, SourceRecord, SyncStateRecord, insert_candidate
from .focus import FocusInputError
from .schemas import Candidate, CandidatePatch, DataSource, DocumentOut, NewSignal


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def candidate_number(candidate_id):
    if not re.fullmatch(r'INV-\d{1,18}', candidate_id or ''):
        raise NotFound('Карточка не найдена.')
    return int(candidate_id.split('-')[1])


def list_candidates(db):
    return [r.payload for r in db.scalars(select(CandidateRecord).order_by(CandidateRecord.number.desc()))]


def get_candidate(db, candidate_id):
    record = db.get(CandidateRecord, candidate_number(candidate_id))
    if not record:
        raise NotFound('Карточка не найдена.')
    return record.payload


def create_manual(db, signal: NewSignal):
    date = display_date()
    candidate = Candidate(**signal.model_dump(), id='pending', initials=initials(signal.company),
        signalType='Ручной сигнал', sourceDate=date, score=60, urgency='medium', urgencyLabel='Средняя',
        status='new', owner='Не назначен', amount='Не указано', stage='Требует проверки',
        summary='Карточка создана аналитиком вручную. Факты и параметры проекта требуют экспертной проверки.',
        factors={'Релевантность': 65, 'Уверенность': 55, 'Региональный эффект': 60, 'Срочность': 58},
        recommendation='Проверить источник и запросить недостающие данные',
        rationale='Для карточки пока недостаточно независимых подтверждений.', nextAction='Запросить данные',
        evidence=[{'source': signal.source, 'date': date, 'excerpt': signal.signal, 'highlights': []}], origin='manual')
    insert_candidate(db, candidate)
    db.commit()
    return candidate


def patch_candidate(db, candidate_id, patch: CandidatePatch):
    record = db.scalar(select(CandidateRecord).where(CandidateRecord.number == candidate_number(candidate_id)).with_for_update())
    if not record:
        raise NotFound('Карточка не найдена.')
    if patch.status and record.payload['status'] == 'exported':
        raise Conflict('Карточка уже экспортирована, её статус больше не меняется.')
    result = Candidate.model_validate({**record.payload, **patch.model_dump(exclude_unset=True)})
    if patch.status == 'approved' and result.owner == 'Не назначен':
        result.owner = 'А. Крылова'
    record.payload = result.model_dump()
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    return result


def iso_value(value):
    return value.isoformat() if isinstance(value, datetime) else value


def source_status(payload, connection):
    if not payload.get('enabled', True):
        return 'disabled'
    if connection in ('not_configured', 'no_access', 'error'):
        return 'not_configured'
    return payload.get('status') if payload.get('status') in ('active', 'retry') else 'active'


def list_sources(db):
    from .sources.runner import RUN_FIELDS, describe
    counts = {row.source_id: (row.total, row.signals) for row in db.execute(text(
        "SELECT source_id, count(*) AS total, count(*) FILTER (WHERE stage IN ('signal', 'merged')) AS signals "
        'FROM documents GROUP BY source_id'))}
    events = {row.source_id: (row.total, row.linked) for row in db.execute(text(
        "SELECT source_id, count(*) AS total, count(*) FILTER (WHERE processing_status = 'linked') AS linked "
        'FROM source_events GROUP BY source_id'))}
    errors = dict(db.execute(text("SELECT source_id, count(*) FROM source_runs WHERE status IN ('error', 'partial') "
                                  "AND started_at > now() - interval '7 days' GROUP BY source_id")).all())
    last_runs = {row.source_id: row for row in db.execute(text(
        'SELECT DISTINCT ON (source_id) * FROM source_runs ORDER BY source_id, started_at DESC, id DESC'))}
    states = {state.source_id: state for state in db.scalars(select(SyncStateRecord))}
    all_signals = sum(signals for _, signals in counts.values()) or 1
    result = []
    for record in db.scalars(select(SourceRecord).order_by(SourceRecord.id)):
        total, signals = counts.get(record.id, (0, 0))
        event_total, linked = events.get(record.id, (0, 0))
        state = states.get(record.id)
        connection, produces = describe(record.payload, state)
        run = last_runs.get(record.id)
        result.append(DataSource.model_validate({
            **record.payload, 'documents': total, 'signals': signals, 'share': round(signals / all_signals * 100, 1),
            'events': event_total, 'eventSignals': linked, 'errors7d': errors.get(record.id, 0), 'connection': connection,
            'produces': produces, 'status': source_status(record.payload, connection),
            'lastRunStats': {key: iso_value(getattr(run, field)) for key, field in RUN_FIELDS.items()} if run else None,
            'lastCheck': {'ok': state.last_check_ok, 'message': state.last_check_message, 'checkedAt': state.last_check_at.isoformat()}
            if state and state.last_check_at else None}))
    return result


def get_source(db, source_id):
    found = next((s for s in list_sources(db) if s.id == source_id), None)
    if not found:
        raise NotFound('Источник не найден.')
    return found


def list_documents(db, stage=None, source_id=None, query=None, limit=50):
    statement = select(DocumentRecord).order_by(DocumentRecord.collected_at.desc(), DocumentRecord.id.desc()).limit(min(limit, 200))
    if stage:
        statement = statement.where(DocumentRecord.stage == stage)
    if source_id:
        statement = statement.where(DocumentRecord.source_id == source_id)
    if query:
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        statement = statement.where(or_(DocumentRecord.title.ilike(pattern), DocumentRecord.body.ilike(pattern)))
    return [DocumentOut(id=d.id, sourceId=d.source_id, url=d.url, title=d.title,
                        publishedAt=d.published_at.isoformat() if d.published_at else None, collectedAt=d.collected_at.isoformat(),
                        stage=d.stage, reason=d.reason, candidateId=f'INV-{d.candidate_number:04d}' if d.candidate_number else None)
            for d in db.scalars(statement)]


def document_total(db):
    return db.scalar(select(func.count()).select_from(DocumentRecord))


def check_candidate(db, focus, candidate_id, inn=None, force=False):
    """Run the Kontur.Focus check for the card's initiator; the HTTP calls happen before the row is locked."""
    number = candidate_number(candidate_id)
    current = db.get(CandidateRecord, number)
    if not current:
        raise NotFound('Карточка не найдена.')
    inn = inn or current.payload.get('inn')
    if not inn:
        raise FocusInputError('Укажите ИНН компании-инициатора.')
    risk = focus.check(inn, force=force)
    db.rollback()
    record = db.scalar(select(CandidateRecord).where(CandidateRecord.number == number).with_for_update())
    record.payload = {**record.payload, 'inn': inn, 'risk': risk}
    record.updated_at = datetime.now(timezone.utc)
    db.commit()
    return Candidate.model_validate(record.payload)
