"""Hiring analytics in the worker: after a vacancy sync for touched employers, once a day for everyone."""
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from ..common import TZ, display_date
from ..database import MetadataRecord, SourceRecord, VacancyDetailRecord
from ..sources.runner import ensure_state, push_events, requested
from . import SIGNAL_SOURCE, VACANCY_KINDS, aggregates, signals
from .categories import load_book
from .store import reclassify

log = logging.getLogger(__name__)
LOCK_ID = 1847304
DAILY_KEY = 'hiring_daily'
CATEGORIES_KEY = 'vacancy_categories'


def today():
    return datetime.now(TZ).date()


def refresh(sessions, keys=None, as_of=None, trigger='schedule'):
    """Indicators, the day's snapshot and signals for the given employers (None: all). Signals are written as one run
    of the hiring-signals source. Returns counters."""
    as_of = as_of or today()
    rules, book = signals.load_rules(), load_book()
    with sessions() as db:
        reclassified = apply_dictionary(db, book) if keys is None else 0
        views = aggregates.build_views(db, as_of, rules, book, keys)
        aggregates.update_exclusions(views)
        aggregates.write_snapshots(db, views)
        items = signals.collect(db, views, rules, book, as_of, keys)
        db.commit()
    result = {'employers': len(views), 'signals': len(items), 'reclassified': reclassified}
    with sessions() as db:
        if db.get(SourceRecord, SIGNAL_SOURCE) is None:
            log.warning('Source %s is missing from seed: hiring signals are not stored', SIGNAL_SOURCE)
            return {**result, 'stored': False}
        run = push_events(db, SIGNAL_SOURCE, items, trigger)
        record_run(db, run)
    return {**result, 'stored': run['status'] == 'ok', 'run': run}


def apply_dictionary(db, book):
    """New vacancies are classified on write; stored ones only when the dictionary file changed."""
    digest = hashlib.sha256(book.model_dump_json().encode()).hexdigest()
    record = db.get(MetadataRecord, CATEGORIES_KEY)
    if record and record.payload.get('digest') == digest:
        return 0
    changed = reclassify(db, book)
    if record:
        record.payload = {'digest': digest, 'version': book.version}
    else:
        db.add(MetadataRecord(key=CATEGORIES_KEY, payload={'digest': digest, 'version': book.version}))
    return changed


def record_run(db, run):
    """Keeps the sources list fields (last run, error, status) in step, as the runner does for fetched sources."""
    now = datetime.now(timezone.utc)
    state = ensure_state(db, SIGNAL_SOURCE)
    state.last_attempt_at = now
    record = db.get(SourceRecord, SIGNAL_SOURCE, with_for_update=True)
    record.payload = {**record.payload, 'status': 'active' if run['status'] == 'ok' else 'retry', 'lastError': run['error'],
                      'lastRun': display_date(now), 'lastRunAt': now.isoformat()}
    db.commit()


def touched_employers(db, since):
    return list(db.scalars(select(VacancyDetailRecord.employer_key).where(VacancyDetailRecord.updated_at >= since).distinct()))


def daily_due(db, day):
    record = db.get(MetadataRecord, DAILY_KEY)
    return not record or record.payload.get('date') != day.isoformat()


def mark_daily(db, day, result):
    payload = {'date': day.isoformat(), 'employers': result['employers'], 'signals': result['signals']}
    record = db.get(MetadataRecord, DAILY_KEY)
    if record:
        record.payload = payload
    else:
        db.add(MetadataRecord(key=DAILY_KEY, payload=payload))
    db.commit()


def hiring_step(sessions, runs=()):
    """Runs: summaries of this tick's vacancy source runs. Daily full pass, manual request or touched employers only."""
    with sessions() as lock:
        if not lock.execute(text('SELECT pg_try_advisory_lock(:id)'), {'id': LOCK_ID}).scalar():
            return {'busy': True}
        try:
            day = today()
            with sessions() as db:
                daily = daily_due(db, day)
                manual = requested(ensure_state(db, SIGNAL_SOURCE)) if db.get(SourceRecord, SIGNAL_SOURCE) else False
                db.commit()
                starts = [datetime.fromisoformat(r['startedAt']) for r in runs
                          if r['status'] in ('ok', 'partial') and (r['inserted'] or r['updated'] or r['status'] == 'ok')]
                keys = touched_employers(db, min(starts)) if starts else []
            if daily or manual:
                result = refresh(sessions, None, day, 'manual' if manual and not daily else 'schedule')
                if daily:
                    with sessions() as db:
                        mark_daily(db, day, result)
            elif keys:
                result = refresh(sessions, keys, day)
            else:
                return {'employers': 0, 'signals': 0}
            log.info('Hiring analytics: %s employers, %s signals', result['employers'], result['signals'])
            return result
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': LOCK_ID})
            lock.commit()


def wanted(kinds):
    """The worker that collects vacancies also computes hiring analytics."""
    return any(kind in VACANCY_KINDS for kind in kinds)
