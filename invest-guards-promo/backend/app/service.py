import re
from datetime import datetime, timezone

from sqlalchemy import func, or_, select, text

from .common import display_date, initials
from .database import CandidateRecord, DocumentRecord, SourceRecord, insert_candidate
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


def list_sources(db):
    counts = {row.source_id: (row.total, row.signals) for row in db.execute(text(
        "SELECT source_id, count(*) AS total, count(*) FILTER (WHERE stage IN ('signal', 'merged')) AS signals "
        'FROM documents GROUP BY source_id'))}
    all_signals = sum(signals for _, signals in counts.values()) or 1
    result = []
    for record in db.scalars(select(SourceRecord).order_by(SourceRecord.id)):
        total, signals = counts.get(record.id, (0, 0))
        result.append(DataSource.model_validate({**record.payload, 'documents': total, 'signals': signals,
                                                 'share': round(signals / all_signals * 100, 1)}))
    return result


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
