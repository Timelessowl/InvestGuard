import logging
import os
import threading

from .database import initialize, make_engine, session_factory
from .focus import FocusClient
from .linking import link_pending_events
from .llm import LlmClient
from .net import http_client
from .pipeline import process_pending
from .sources.runner import due_sources, mark_stale_runs, sync_source

log = logging.getLogger(__name__)


def tick(sessions, llm, http, batch, focus=None):
    """Collect due sources, then AI processing of documents, then rule-based linking of events."""
    kinds = tuple(k.strip() for k in os.environ.get('SOURCE_KINDS', 'rss').split(',') if k.strip())
    with sessions() as db:
        due = due_sources(db, kinds)
    for source_id in due:
        # sync_source records its own failures; this guard keeps one source from stopping the tick.
        try:
            with sessions() as db:
                run = sync_source(db, source_id, http)
            log.info('Collected %s: %s, fetched %s, new %s, updated %s', source_id, run['status'], run['fetched'], run['inserted'], run['updated'])
        except Exception:
            log.exception('Source %s run failed', source_id)
    if llm.settings.configured and os.environ.get('PIPELINE_PROCESS', '1') == '1':
        try:
            result = process_pending(sessions, llm, batch, focus)
            if result['processed'] or result.get('stopped'):
                log.info('Pipeline: %s', result)
        except Exception:
            log.exception('Document processing failed')
    linked = link_pending_events(sessions, focus=focus)
    if linked['processed']:
        log.info('Event linking: %s', linked)


def run_forever(sessions, llm, http, stop: threading.Event, interval=None, batch=None, focus=None):
    interval = interval or int(os.environ.get('PIPELINE_INTERVAL_SECONDS', 60))
    batch = batch or int(os.environ.get('PIPELINE_BATCH', 20))
    try:
        mark_stale_runs(sessions)
    except Exception:
        log.exception('Closing stale source runs failed')
    while not stop.is_set():
        try:
            tick(sessions, llm, http, batch, focus)
        except Exception:
            log.exception('Pipeline tick failed')
        stop.wait(interval)


def start_background(sessions, llm, http, focus=None):
    stop = threading.Event()
    thread = threading.Thread(target=run_forever, args=(sessions, llm, http, stop), kwargs={'focus': focus}, name='pipeline', daemon=True)
    thread.start()
    return stop


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    engine = make_engine()
    initialize(engine)
    run_forever(session_factory(engine), LlmClient(engine), http_client(), threading.Event(), focus=FocusClient(engine))
