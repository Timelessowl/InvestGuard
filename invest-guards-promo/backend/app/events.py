"""Structured events: dedup keys, content hashes, upsert with versions and API queries."""
import hashlib
import json
import logging
import math
import re
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .common import normalize_company
from .database import CandidateEventRecord, CandidateRecord, EventRecord, EventTypeRecord, EventVersionRecord
from .focus import valid_inn
from .geo import canonical_municipality
from .schemas import EventDetail, EventOut, EventPage, EventType
from .service import NotFound, candidate_number

log = logging.getLogger(__name__)
PRECISIONS = {'exact', 'address', 'municipality'}
# Extension writers by details_kind, e.g. 'vacancy' -> vacancies.store.save_details(db, event_id, details).
DETAILS_WRITERS = {}


def register_details(kind, writer):
    DETAILS_WRITERS[kind] = writer


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def sha256(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def norm_text(value):
    value = (value or '').lower().replace('ё', 'е')
    return re.sub(r'[\W_]+', ' ', value).strip()


def norm_number(value):
    if value is None:
        return None
    text = format(Decimal(value).normalize(), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def scrub(value):
    """jsonb rejects \\u0000 and NaN/Infinity: NUL is dropped from strings and keys, non-finite numbers become null."""
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {scrub(key): scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def json_safe(value):
    return scrub(json.loads(canonical_json(value)))


def dedup_key(item):
    """'id:' + external id within the source, otherwise a stable hash of the fields that identify the event."""
    if item.external_id:
        return 'id:' + item.external_id.strip()[:400]
    return 'h:' + sha256({'type': item.event_type, 'org': item.inn or item.ogrn or normalize_company(item.company_name or ''),
                          'date': item.event_date.isoformat() if item.event_date else None, 'cadastral': item.cadastral_number,
                          'address': norm_text(item.address), 'title': norm_text(item.title), 'value': norm_number(item.value)})


def content_hash(item):
    """Hash of everything meaningful, without service dates: equal hash means nothing to update."""
    data = asdict(item)
    data['value'] = norm_number(item.value)
    data['metrics'] = list(item.metrics)
    return sha256(data)


def clean(value, limit=1000):
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value)).strip() if value is not None else ''
    return value[:limit] or None


def municipality_of(value):
    """Canonical name from the regional reference; an unknown place (another region, a settlement outside the list)
    keeps its source spelling so it is not mistaken for a missing one."""
    value = clean(value, 200)
    return (canonical_municipality(value) or value) if value else None


def sanitize(item):
    """Normalise an adapter item; drops values that would break table constraints instead of the whole batch."""
    inn = clean(item.inn, 12)
    ogrn = clean(item.ogrn, 15)
    latitude, longitude = item.latitude, item.longitude
    if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        latitude = longitude = None
    precision = item.location_precision if item.location_precision in PRECISIONS else None
    if latitude is None and precision == 'exact':
        precision = None
    return replace(item, title=clean(item.title, 500) or '', external_id=clean(item.external_id, 400),
                   company_name=clean(item.company_name, 500), inn=inn if valid_inn(inn) else None,
                   ogrn=ogrn if ogrn and re.fullmatch(r'[0-9]{13}|[0-9]{15}', ogrn) else None,
                   source_company_id=clean(item.source_company_id, 200),
                   municipality=municipality_of(item.municipality),
                   address=clean(item.address), cadastral_number=clean(item.cadastral_number, 100), latitude=latitude,
                   longitude=longitude, location_precision=precision, status=clean(item.status, 200),
                   value=Decimal(str(item.value)) if item.value is not None else None, value_unit=clean(item.value_unit, 50),
                   metrics=tuple(json_safe(list(item.metrics))), url=clean(item.url, 2000) if (item.url or '').startswith('http') else None,
                   raw=json_safe(item.raw or {}), details=json_safe(item.details) if item.details is not None else None)


def row_values(item):
    return {'external_id': item.external_id, 'event_type': item.event_type, 'title': item.title, 'company_name': item.company_name,
            'inn': item.inn, 'ogrn': item.ogrn, 'source_company_id': item.source_company_id, 'municipality': item.municipality,
            'address': item.address, 'cadastral_number': item.cadastral_number, 'latitude': item.latitude, 'longitude': item.longitude,
            'location_precision': item.location_precision, 'event_date': item.event_date, 'status': item.status, 'value': item.value,
            'value_unit': item.value_unit, 'metrics': list(item.metrics), 'url': item.url, 'raw': item.raw}


def snapshot(record):
    fields = ('title', 'company_name', 'inn', 'ogrn', 'municipality', 'address', 'cadastral_number', 'event_date', 'status',
              'value', 'value_unit', 'metrics', 'url', 'event_type')
    return json_safe({name: getattr(record, name) for name in fields})


def upsert_events(db, source_id, items):
    """Insert new events, touch unchanged ones, keep a version of changed ones. Caller commits."""
    stats = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'rejected': 0}
    known = set(db.scalars(select(EventTypeRecord.code)))
    unique = {}
    for item in items:
        item = sanitize(item)
        if item.event_type not in known or not item.title:
            log.warning('Source %s: rejected event %r (type %s)', source_id, item.title[:80], item.event_type)
            stats['rejected'] += 1
            continue
        # The same event twice in one batch is stored once, the later copy wins.
        unique[dedup_key(item)] = item
    if not unique:
        return stats
    existing = {r.dedup_key: r for r in db.scalars(select(EventRecord).where(
        EventRecord.source_id == source_id, EventRecord.dedup_key.in_(list(unique))).with_for_update())}
    now = datetime.now(timezone.utc)
    for key, item in unique.items():
        digest = content_hash(item)
        record = existing.get(key)
        if record is None:
            event_id = db.scalar(insert(EventRecord).values(source_id=source_id, dedup_key=key, content_hash=digest, **row_values(item))
                                 .on_conflict_do_nothing(index_elements=['source_id', 'dedup_key']).returning(EventRecord.id))
            stats['inserted' if event_id else 'unchanged'] += 1
        elif record.content_hash == digest:
            record.last_seen_at = now
            stats['unchanged'] += 1
            continue
        else:
            db.add(EventVersionRecord(event_id=record.id, content_hash=record.content_hash, status=record.status,
                                      fields=snapshot(record), raw=record.raw))
            for name, value in row_values(item).items():
                setattr(record, name, value)
            record.content_hash, record.updated_at, record.last_seen_at = digest, now, now
            if record.processing_status != 'ignored':
                record.processing_status, record.processing_reason, record.attempts = 'new', None, 0
            event_id = record.id
            stats['updated'] += 1
        writer = DETAILS_WRITERS.get(item.details_kind)
        if event_id and writer and item.details is not None:
            writer(db, event_id, item.details)
    db.flush()
    return stats


def event_types(db):
    return [EventType(code=t.code, title=t.title, category=t.category, polarity=t.polarity, linkPolicy=t.link_policy, stage=t.stage,
                      description=t.description, deprecated=t.deprecated)
            for t in db.scalars(select(EventTypeRecord).order_by(EventTypeRecord.category, EventTypeRecord.code))]


def iso(value):
    return value.isoformat() if value else None


def links_for(db, event_ids):
    links = {}
    rows = db.execute(select(CandidateEventRecord.event_id, CandidateEventRecord.candidate_number, CandidateEventRecord.confidence)
                      .where(CandidateEventRecord.event_id.in_(event_ids)).order_by(CandidateEventRecord.candidate_number))
    for event_id, number, confidence in rows:
        links.setdefault(event_id, []).append({'id': f'INV-{number:04d}', 'confidence': confidence})
    return links


def event_out(record, types, links):
    kind = types.get(record.event_type)
    return EventOut(id=record.id, sourceId=record.source_id, externalId=record.external_id, type=record.event_type,
                    typeTitle=kind.title if kind else record.event_type, category=kind.category if kind else 'other',
                    title=record.title, companyName=record.company_name, inn=record.inn, ogrn=record.ogrn,
                    municipality=record.municipality, address=record.address, cadastralNumber=record.cadastral_number,
                    latitude=record.latitude, longitude=record.longitude, locationPrecision=record.location_precision,
                    eventDate=iso(record.event_date), status=record.status,
                    value=float(record.value) if record.value is not None else None, valueUnit=record.value_unit,
                    metrics=record.metrics or [], url=record.url, collectedAt=iso(record.collected_at), updatedAt=iso(record.updated_at),
                    lastSeenAt=iso(record.last_seen_at), processingStatus=record.processing_status,
                    processingReason=record.processing_reason, candidates=links.get(record.id, []))


def type_index(db):
    return {t.code: t for t in db.scalars(select(EventTypeRecord))}


def escape_like(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


UPPER = 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯё'
LOWER = 'абвгдеежзийклмнопрстуфхцчшщъыьэюяе'  # Ё and ё fold to е


def folded(column):
    # Case-insensitive for Cyrillic even when the database locale is C, where ILIKE and lower() fold ASCII only.
    return func.lower(func.translate(column, UPPER, LOWER))


def list_events(db, types=None, inn=None, company=None, municipality=None, source=None, processing=None, candidate=None,
                date_from=None, date_to=None, limit=50, offset=0):
    conditions = []
    if types:
        conditions.append(EventRecord.event_type.in_(types))
    if inn:
        conditions.append(EventRecord.inn == inn)
    if company:
        conditions.append(folded(EventRecord.company_name).like(escape_like(company.lower().replace('ё', 'е'))))
    if municipality:
        known = canonical_municipality(municipality)
        # Places outside the reference are stored as the source spelled them: matched by substring.
        conditions.append(EventRecord.municipality == known if known else
                          folded(EventRecord.municipality).like(escape_like(municipality.strip().lower().replace('ё', 'е'))))
    if source:
        conditions.append(EventRecord.source_id == source)
    if processing:
        conditions.append(EventRecord.processing_status == processing)
    if candidate:
        linked = select(CandidateEventRecord.event_id).where(CandidateEventRecord.candidate_number == candidate_number(candidate))
        conditions.append(EventRecord.id.in_(linked))
    if date_from:
        conditions.append(EventRecord.event_date >= date_from)
    if date_to:
        conditions.append(EventRecord.event_date <= date_to)
    limit = max(1, min(limit, 200))
    total = db.scalar(select(func.count()).select_from(EventRecord).where(*conditions))
    records = list(db.scalars(select(EventRecord).where(*conditions)
                              .order_by(EventRecord.event_date.desc().nulls_last(), EventRecord.id.desc()).limit(limit).offset(offset)))
    links = links_for(db, [r.id for r in records])
    types_by_code = type_index(db)
    return EventPage(items=[event_out(r, types_by_code, links) for r in records], total=total, limit=limit, offset=offset)


def get_event(db, event_id):
    record = db.get(EventRecord, event_id)
    if not record:
        raise NotFound('Событие не найдено.')
    base = event_out(record, type_index(db), links_for(db, [record.id]))
    rows = db.execute(select(CandidateEventRecord, CandidateRecord.payload['company'].astext)
                      .join(CandidateRecord, CandidateRecord.number == CandidateEventRecord.candidate_number)
                      .where(CandidateEventRecord.event_id == record.id).order_by(CandidateEventRecord.created_at))
    links = [{'id': f'INV-{link.candidate_number:04d}', 'confidence': link.confidence, 'company': company or '',
              'linkedBy': link.linked_by, 'reason': link.reason, 'createdAt': iso(link.created_at)} for link, company in rows]
    versions = [{'replacedAt': iso(v.replaced_at), 'contentHash': v.content_hash, 'status': v.status, 'fields': v.fields, 'raw': v.raw}
                for v in db.scalars(select(EventVersionRecord).where(EventVersionRecord.event_id == record.id)
                                    .order_by(EventVersionRecord.replaced_at.desc()))]
    return EventDetail(**base.model_dump(), sourceCompanyId=record.source_company_id, raw=record.raw, duplicateOf=record.duplicate_of,
                       links=links, versions=versions)


def candidate_events(db, candidate_id):
    number = candidate_number(candidate_id)
    if not db.get(CandidateRecord, number):
        raise NotFound('Карточка не найдена.')
    records = list(db.scalars(select(EventRecord).join(CandidateEventRecord, CandidateEventRecord.event_id == EventRecord.id)
                              .where(CandidateEventRecord.candidate_number == number)
                              .order_by(EventRecord.event_date.asc().nulls_last(), EventRecord.id)))
    links = links_for(db, [r.id for r in records])
    types_by_code = type_index(db)
    return [event_out(r, types_by_code, links) for r in records]
