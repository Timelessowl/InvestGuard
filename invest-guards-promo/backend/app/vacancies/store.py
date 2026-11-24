"""Vacancy extension writer: employers, details, cross-source duplicates, closing after a full export."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from ..database import VacancyDetailRecord
from ..events import register_details
from .categories import load_book

DUPLICATE_DAYS = 30
EMPLOYER_UPSERT = text(
    'INSERT INTO vacancy_employers (employer_key, inn, ogrn, name, url, hr_agency, source_ids) '
    'VALUES (:key, :inn, :ogrn, :name, :url, :agency, CAST(ARRAY[:source] AS text[])) '
    'ON CONFLICT (employer_key) DO UPDATE SET name = EXCLUDED.name, '
    'inn = coalesce(EXCLUDED.inn, vacancy_employers.inn), ogrn = coalesce(EXCLUDED.ogrn, vacancy_employers.ogrn), '
    'url = coalesce(EXCLUDED.url, vacancy_employers.url), hr_agency = EXCLUDED.hr_agency, '
    'source_ids = CASE WHEN :source = ANY(vacancy_employers.source_ids) THEN vacancy_employers.source_ids '
    'ELSE array_append(vacancy_employers.source_ids, :source) END, updated_at = now()')


def as_date(value):
    return date.fromisoformat(value[:10]) if isinstance(value, str) and value else value or None


def as_moment(value):
    return datetime.fromisoformat(value) if isinstance(value, str) and value else value or None


def as_decimal(value):
    return Decimal(str(value)) if value is not None else None


def save_details(db, event_id, details):
    """Called by events.upsert_events for a new or changed vacancy; details arrive JSON-safe."""
    key = details['employer_key']
    db.execute(EMPLOYER_UPSERT, {'key': key, 'inn': details.get('inn'), 'ogrn': details.get('ogrn'),
                                 'name': details.get('employer_name') or key, 'url': details.get('employer_url'),
                                 'agency': bool(details.get('hr_agency')), 'source': details['source_id']})
    category, profile = load_book().classify(details.get('title'), details.get('specialisation'))
    values = {'source_id': details['source_id'], 'employer_key': key, 'source_employer_id': details.get('source_employer_id'),
              'title_norm': details.get('title_norm') or '', 'category': category, 'profile': profile,
              'profession_code': details.get('profession_code'), 'specialisation': details.get('specialisation'),
              'municipality': details.get('municipality'), 'work_places': details.get('work_places'),
              'salary_min': as_decimal(details.get('salary_min')), 'salary_max': as_decimal(details.get('salary_max')),
              'currency': details.get('currency'), 'employment': details.get('employment'), 'schedule': details.get('schedule'),
              'syndicated': bool(details.get('syndicated')), 'published_at': as_date(details.get('published_at')),
              'modified_at': as_moment(details.get('modified_at')), 'fingerprint': details['fingerprint']}
    # Seen again: the vacancy is open, whatever an earlier export concluded.
    db.execute(insert(VacancyDetailRecord).values(event_id=event_id, **values)
               .on_conflict_do_update(index_elements=['event_id'],
                                      set_={**values, 'closed_at': None, 'missing_since': None, 'duplicate_of': None,
                                            'updated_at': func.now()}))
    mark_duplicate(db, event_id, values)


def mark_duplicate(db, event_id, values):
    """The same vacancy from another source (or a republication) within ±30 days points to the earliest open copy.
    The written vacancy is open, so a closed copy points to it."""
    published = values['published_at']
    if published is None:
        return
    other = db.execute(
        select(VacancyDetailRecord.event_id, VacancyDetailRecord.published_at, VacancyDetailRecord.closed_at)
        .where(VacancyDetailRecord.fingerprint == values['fingerprint'], VacancyDetailRecord.event_id != event_id,
               VacancyDetailRecord.duplicate_of.is_(None),
               or_(VacancyDetailRecord.source_id != values['source_id'], VacancyDetailRecord.syndicated != values['syndicated']),
               VacancyDetailRecord.published_at.between(published - timedelta(days=DUPLICATE_DAYS), published + timedelta(days=DUPLICATE_DAYS)))
        .order_by(VacancyDetailRecord.closed_at.is_not(None), VacancyDetailRecord.published_at, VacancyDetailRecord.event_id)
        .limit(1)).first()
    if other is None:
        return
    if other.closed_at is None and (other.published_at, other.event_id) <= (published, event_id):
        db.execute(update(VacancyDetailRecord).where(VacancyDetailRecord.event_id == event_id).values(duplicate_of=other.event_id))
    else:
        db.execute(update(VacancyDetailRecord).where(VacancyDetailRecord.duplicate_of == other.event_id).values(duplicate_of=event_id))
        db.execute(update(VacancyDetailRecord).where(VacancyDetailRecord.event_id == other.event_id)
                   .values(duplicate_of=event_id, updated_at=func.now()))


def close_missing(db, source_id, started_at):
    """After a complete full export. Offset paging may skip a record once when the data shift under it, so a vacancy
    missing from the export is only marked; missing from the next full export too, and not seen in between, it is
    closed. Closed ones seen again reopen. Returns (closed, reopened, marked); the caller commits."""
    params = {'source': source_id, 'started': started_at}
    closed = list(db.scalars(text(
        'UPDATE vacancy_details d SET closed_at = now(), missing_since = NULL, updated_at = now() FROM source_events e '
        'WHERE e.id = d.event_id AND d.source_id = :source AND d.closed_at IS NULL AND d.missing_since < :started '
        'AND e.last_seen_at < d.missing_since RETURNING d.event_id'), params))
    marked = db.execute(text(
        'UPDATE vacancy_details d SET missing_since = :started FROM source_events e '
        'WHERE e.id = d.event_id AND d.source_id = :source AND d.closed_at IS NULL AND e.last_seen_at < :started '
        'AND (d.missing_since IS NULL OR e.last_seen_at >= d.missing_since)'), params).rowcount
    db.execute(text('UPDATE vacancy_details d SET missing_since = NULL FROM source_events e WHERE e.id = d.event_id '
                    'AND d.source_id = :source AND d.missing_since IS NOT NULL AND e.last_seen_at >= :started'), params)
    reopened = list(db.scalars(text(
        'UPDATE vacancy_details d SET closed_at = NULL, updated_at = now() FROM source_events e '
        'WHERE e.id = d.event_id AND d.source_id = :source AND d.closed_at IS NOT NULL AND e.last_seen_at >= :started '
        'RETURNING d.event_id'), params))
    # Shown in the events API; the source never sends a status, so the next update of the vacancy clears it.
    if closed:
        db.execute(text("UPDATE source_events SET status = 'closed' WHERE id = ANY(:ids)"), {'ids': closed})
    if reopened:
        db.execute(text("UPDATE source_events SET status = NULL WHERE id = ANY(:ids) AND status = 'closed'"), {'ids': reopened})
    if closed or reopened:
        regroup_duplicates(db)
    return len(closed), len(reopened), marked


def regroup_duplicates(db):
    """Each group of copies is headed by its earliest open copy, so an open copy never hides behind a closed one.
    Returns the number of re-pointed rows."""
    return db.execute(text(
        'WITH g AS (SELECT event_id, first_value(event_id) OVER (PARTITION BY coalesce(duplicate_of, event_id) '
        'ORDER BY closed_at IS NOT NULL, published_at, event_id) AS head FROM vacancy_details) '
        'UPDATE vacancy_details d SET duplicate_of = NULLIF(g.head, d.event_id), updated_at = now() FROM g '
        'WHERE g.event_id = d.event_id AND d.duplicate_of IS DISTINCT FROM NULLIF(g.head, d.event_id)')).rowcount


def reclassify(db, book=None):
    """Applies an edited category dictionary to stored vacancies; returns the number of changed rows."""
    book = book or load_book()
    changed = 0
    rows = db.execute(text('SELECT d.event_id, e.title, d.specialisation, d.category, d.profile FROM vacancy_details d '
                           'JOIN source_events e ON e.id = d.event_id'))
    for event_id, title, specialisation, category, profile in rows.all():
        new = book.classify(title, specialisation)
        if new != (category, profile):
            db.execute(update(VacancyDetailRecord).where(VacancyDetailRecord.event_id == event_id)
                       .values(category=new[0], profile=new[1], updated_at=func.now()))
            changed += 1
    return changed


register_details('vacancy', save_details)
