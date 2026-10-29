import logging
import os
import threading

from .collectors import collect_source, due_sources
from .database import initialize, make_engine, session_factory
from .llm import LlmClient
from .net import http_client
from .pipeline import process_pending

log = logging.getLogger(__name__)


def tick(sessions, llm, http, batch):
    with sessions() as db:
        due = due_sources(db)
    for source_id in due:
        with sessions() as db:
            _, added = collect_source(db, source_id, http)
            log.info('Collected %s: %s new documents', source_id, added)
    if llm.settings.configured:
        result = process_pending(sessions, llm, batch)
        if result['processed'] or result.get('stopped'):
            log.info('Pipeline: %s', result)


def run_forever(sessions, llm, http, stop: threading.Event, interval=None, batch=None):
    interval = interval or int(os.environ.get('PIPELINE_INTERVAL_SECONDS', 60))
    batch = batch or int(os.environ.get('PIPELINE_BATCH', 20))
    while not stop.is_set():
        try:
            tick(sessions, llm, http, batch)
        except Exception:
            log.exception('Pipeline tick failed')
        stop.wait(interval)


def start_background(sessions, llm, http):
    stop = threading.Event()
    thread = threading.Thread(target=run_forever, args=(sessions, llm, http, stop), name='pipeline', daemon=True)
    thread.start()
    return stop


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    engine = make_engine()
    initialize(engine)
    run_forever(session_factory(engine), LlmClient(engine), http_client(), threading.Event())
