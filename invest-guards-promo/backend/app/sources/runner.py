"""Source runner: per-source lock, run history, batches with cursor, backoff. Never lets an exception escape."""
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from ..common import display_date
from ..database import DocumentRecord, SourceRecord, SourceRunRecord, SyncStateRecord
from ..events import upsert_events
from . import adapter_class, adapter_for
from .base import SourceError, env_int

log = logging.getLogger(__name__)
LOCK_NS = 1847310
COUNTERS = ('fetched', 'inserted', 'updated', 'unchanged', 'rejected')
RUN_FIELDS = {'status': 'status', 'startedAt': 'started_at', 'finishedAt': 'finished_at', 'error': 'error', 'retries': 'retries',
              **{key: key for key in COUNTERS}}


def utcnow():
    return datetime.now(timezone.utc)


@contextmanager
def source_lock(db, source_id):
    """Session-level advisory lock on its own connection, so batch commits do not release it."""
    with db.get_bind().connect() as connection:
        params = {'ns': LOCK_NS, 'id': source_id}
        locked = connection.execute(text('SELECT pg_try_advisory_lock(:ns, hashtext(:id))'), params).scalar()
        connection.commit()
        try:
            yield locked
        finally:
            if locked:
                connection.execute(text('SELECT pg_advisory_unlock(:ns, hashtext(:id))'), params)
                connection.commit()


def ensure_state(db, source_id):
    db.execute(insert(SyncStateRecord).values(source_id=source_id).on_conflict_do_nothing(index_elements=['source_id']))
    return db.get(SyncStateRecord, source_id, populate_existing=True)


def describe(payload, state=None):
    """(connection, produces) for the sources list, without network."""
    try:
        adapter = adapter_for(payload, None)
    except LookupError:
        return 'error', 'events'
    connection = adapter.availability()
    if connection == 'ready' and state is not None and state.last_error_kind == 'access_denied':
        connection = 'no_access'
    return connection, adapter.produces


def clean_document(item):
    # PostgreSQL text cannot hold NUL bytes; Dzen snippets sometimes carry them.
    return {key: value.replace('\x00', '') if isinstance(value, str) else value for key, value in item.items()}


def write_batch(db, source_id, batch, stats):
    if batch.documents:
        documents = [clean_document(d) for d in batch.documents]
        result = db.execute(insert(DocumentRecord).values([{'source_id': source_id, **d} for d in documents])
                            .on_conflict_do_nothing(index_elements=['url']).returning(DocumentRecord.id))
        added = len(result.all())
        stats['fetched'] += len(documents)
        stats['inserted'] += added
        stats['unchanged'] += len(documents) - added
    if batch.events:
        stats['fetched'] += len(batch.events)
        for key, value in upsert_events(db, source_id, batch.events).items():
            stats[key] += value
    if batch.rejected:
        stats['fetched'] += batch.rejected
        stats['rejected'] += batch.rejected


def start_run(db, source_id, trigger):
    run = SourceRunRecord(source_id=source_id, run_trigger=trigger, started_at=utcnow(), status='running')
    db.add(run)
    db.flush()
    return run


def finish_run(db, run, status, stats, error=None, retries=0):
    run.status, run.finished_at, run.error, run.retries = status, utcnow(), (error or '')[:1000] or None, retries
    for key in COUNTERS:
        setattr(run, key, stats.get(key, 0))


def run_summary(run, payload=None):
    return {'id': run.id, 'sourceId': run.source_id, 'trigger': run.run_trigger, 'status': run.status, 'error': run.error,
            'retries': run.retries, 'startedAt': run.started_at.isoformat(), 'finishedAt': run.finished_at.isoformat() if run.finished_at else None,
            **{key: getattr(run, key) for key in COUNTERS}, 'payload': payload}


def requested(state):
    return bool(state and state.requested_at and (not state.last_attempt_at or state.requested_at > state.last_attempt_at))


def sync_source(db, source_id, http, trigger=None):
    """Collect one source. Returns the run summary with the updated source payload; raises only LookupError."""
    record = db.get(SourceRecord, source_id)
    if not record:
        raise LookupError(source_id)
    payload = dict(record.payload)
    trigger = trigger or ('manual' if requested(db.get(SyncStateRecord, source_id)) else 'schedule')
    db.commit()
    with source_lock(db, source_id) as locked:
        if not locked:
            run = start_run(db, source_id, trigger)
            finish_run(db, run, 'skipped', {}, 'Сбор этого источника уже выполняется.')
            db.commit()
            return run_summary(run, payload)
        return _run(db, source_id, payload, http, trigger)


def _run(db, source_id, payload, http, trigger):
    state = ensure_state(db, source_id)
    run = start_run(db, source_id, trigger)
    state.last_attempt_at = run.started_at
    db.commit()
    stats = dict.fromkeys(COUNTERS, 0)
    status, error, kind, retryable, retry_after, batches, adapter = 'ok', None, None, True, None, 0, None
    try:
        adapter = adapter_for(payload, http)
        availability = adapter.availability()
        if availability != 'ready':
            status, error, kind = ('skipped' if availability == 'manual_only' else 'not_configured'), adapter.not_ready_message(), availability
        else:
            # A full export may span several runs continued by cursor: it began with the first run after the previous one ended.
            # Saved with the first batch; a run that fails before it leaves the start to the next run.
            if state.snapshot_started_at is None:
                state.snapshot_started_at = run.started_at
            completed = False
            for batch in adapter.fetch(state.sync_cursor):
                write_batch(db, source_id, batch, stats)
                if batch.cursor is not None:
                    state.sync_cursor = batch.cursor
                for key in COUNTERS:
                    setattr(run, key, stats[key])
                # Each batch is durable on its own: a failure on the next page keeps it, a rerun is a safe upsert.
                db.commit()
                batches += 1
                if batch.snapshot_complete:
                    adapter.snapshot_finished(db, state.snapshot_started_at or run.started_at)
                    state.snapshot_started_at = None
                    completed = True
                    db.commit()
            if not completed and state.snapshot_started_at is not None:
                # A finished run without a full export was incremental: the next full export starts afresh.
                state.snapshot_started_at = None
                db.commit()
    except LookupError as failure:
        db.rollback()
        status, error, kind, retryable = 'error', str(failure), 'config', False
    except SourceError as failure:
        db.rollback()
        status, error, kind = ('partial' if batches else 'error'), str(failure) or type(failure).__name__, failure.kind
        retryable, retry_after = failure.retryable, getattr(failure, 'retry_after', None)
    except httpx.HTTPError as failure:
        db.rollback()
        status, error, kind = ('partial' if batches else 'error'), str(failure) or type(failure).__name__, 'network'
    except Exception as failure:  # noqa: BLE001 - a broken source must not stop the worker tick
        db.rollback()
        log.exception('Source %s failed', source_id)
        status, error, kind = ('partial' if batches else 'error'), f'Внутренняя ошибка: {type(failure).__name__}', 'internal'
    if status in ('error', 'partial'):
        log.warning('Source %s failed: %s', source_id, error)
    backoff = retryable and adapter is not None and adapter.short_backoff
    return _finish(db, source_id, payload, run.id, status, stats, error, kind, backoff, retry_after, adapter.retries if adapter else 0)


def _finish(db, source_id, payload, run_id, status, stats, error, kind, backoff, retry_after, retries):
    run = db.get(SourceRunRecord, run_id, populate_existing=True)
    finish_run(db, run, status, stats, error, retries)
    state = ensure_state(db, source_id)
    now = run.finished_at
    schedule = timedelta(minutes=payload.get('scheduleMinutes', 60))
    if status == 'ok':
        state.last_success_at, state.consecutive_failures, state.last_error, state.last_error_kind = now, 0, None, None
        state.next_run_at = now + schedule
    elif status in ('not_configured', 'skipped'):
        state.last_error, state.last_error_kind, state.next_run_at = error, kind, now + schedule
    else:
        state.consecutive_failures += 1
        base = timedelta(minutes=env_int('SOURCE_BACKOFF_BASE_MINUTES', 5))
        # Short backoff only for retryable errors of adapters that allow it; RSS and Dzen keep their schedule.
        delay = min(schedule, base * 2 ** min(state.consecutive_failures - 1, 16)) if backoff else schedule
        if retry_after:
            delay = max(delay, timedelta(seconds=retry_after))
        state.last_error, state.last_error_kind, state.next_run_at = error, kind, now + delay
    result = dict(payload)
    if status != 'skipped':
        # Old fields in sources.payload stay in sync for the existing UI and assistant.
        record = db.scalar(select(SourceRecord).where(SourceRecord.id == source_id).with_for_update().execution_options(populate_existing=True))
        if record:
            source_status = {'ok': 'active', 'not_configured': 'not_configured'}.get(status, 'retry')
            result = {**record.payload, 'status': source_status, 'lastError': error[:300] if error else None,
                      'lastRun': display_date(now), 'lastRunAt': now.isoformat()}
            record.payload = result
    db.commit()
    return run_summary(run, result)


def push_events(db, source_id, items, trigger='manual'):
    """Store events handed over directly (file import) as one recorded run; a failed batch is recorded, not raised."""
    if not db.get(SourceRecord, source_id):
        raise LookupError(source_id)
    db.commit()
    with source_lock(db, source_id) as locked:
        run = start_run(db, source_id, trigger)
        stats = dict.fromkeys(COUNTERS, 0)
        if not locked:
            finish_run(db, run, 'skipped', stats, 'Источник сейчас обновляется, повторите загрузку позже.')
            db.commit()
            return run_summary(run)
        db.commit()  # the run record survives a rollback of the batch
        status, error = 'ok', None
        try:
            for key, value in upsert_events(db, source_id, items).items():
                stats[key] += value
        except Exception as failure:  # noqa: BLE001 - the import is answered with a failed run
            db.rollback()
            log.exception('Import into %s failed', source_id)
            status, error, stats = 'error', f'Внутренняя ошибка: {type(failure).__name__}', dict.fromkeys(COUNTERS, 0)
        stats['fetched'] = len(items)
        run = db.get(SourceRunRecord, run.id, populate_existing=True)
        finish_run(db, run, status, stats, error)
        if status == 'ok':
            ensure_state(db, source_id).last_success_at = run.finished_at
        db.commit()
        return run_summary(run)


def request_run(db, source_id):
    """Manual run of an API source: the worker picks it up, the HTTP request does not wait for a full export."""
    state = ensure_state(db, source_id)
    state.requested_at = utcnow()
    db.commit()


def check_source(db, source_id, http):
    record = db.get(SourceRecord, source_id)
    if not record:
        raise LookupError(source_id)
    payload, kind = dict(record.payload), None
    db.commit()
    try:
        adapter = adapter_for(payload, http)
        # Not configured: answer without touching the network.
        ok, message = adapter.check() if adapter.availability() == 'ready' else (False, adapter.not_ready_message())
    except LookupError as failure:
        ok, message = False, str(failure)
    except SourceError as failure:
        ok, message, kind = False, str(failure) or type(failure).__name__, failure.kind
    except httpx.HTTPError as failure:
        ok, message = False, f'Нет связи с источником: {type(failure).__name__}'
    except Exception as failure:  # noqa: BLE001
        log.exception('Source %s check failed', source_id)
        ok, message = False, f'Внутренняя ошибка проверки: {type(failure).__name__}'
    state = ensure_state(db, source_id)
    state.last_check_at, state.last_check_ok, state.last_check_message = utcnow(), ok, message[:500]
    if kind == 'access_denied':
        state.last_error_kind = kind
    elif ok and state.last_error_kind == 'access_denied':
        state.last_error_kind = None
    db.commit()
    connection, _ = describe(payload, state)
    return {'ok': ok, 'message': message, 'checkedAt': state.last_check_at.isoformat(), 'connection': connection}


def due_sources(db, kinds=('rss',)):
    now = utcnow()
    due = []
    rows = db.execute(select(SourceRecord.id, SourceRecord.payload, SyncStateRecord)
                      .outerjoin(SyncStateRecord, SyncStateRecord.source_id == SourceRecord.id).order_by(SourceRecord.id))
    for source_id, payload, state in rows:
        kind = payload.get('kind', 'rss')
        if not payload.get('enabled', True) or kind not in kinds:
            continue
        try:
            scheduled = adapter_class(kind).scheduled
        except LookupError:
            continue
        if requested(state):
            due.append(source_id)
        elif not scheduled:
            continue
        elif state is not None and state.next_run_at:
            if now >= state.next_run_at:
                due.append(source_id)
        else:
            last = payload.get('lastRunAt')
            if not last or (now - datetime.fromisoformat(last)).total_seconds() >= payload.get('scheduleMinutes', 60) * 60:
                due.append(source_id)
    return due


def mark_stale_runs(sessions):
    """Runs left 'running' by a stopped process are closed when the worker starts."""
    minutes = env_int('SOURCE_RUN_STALE_MINUTES', 60)
    with sessions() as db:
        count = db.execute(text("UPDATE source_runs SET status = 'error', finished_at = now(), "
                                "error = 'Запуск прерван: процесс сбора остановился' "
                                "WHERE status = 'running' AND started_at < now() - make_interval(mins => :minutes)"),
                           {'minutes': minutes}).rowcount
        db.commit()
    if count:
        log.warning('Marked %s stale source runs as failed', count)
    return count


def last_runs(db, source_id, limit=20):
    return [run_summary(r) for r in db.scalars(select(SourceRunRecord).where(SourceRunRecord.source_id == source_id)
                                                .order_by(SourceRunRecord.started_at.desc(), SourceRunRecord.id.desc()).limit(limit))]
