import csv
import io
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import service
from .collectors import collect_source
from .database import CandidateRecord, ExportRecord, MetadataRecord, initialize, make_engine, session_factory
from .net import http_client
from .schemas import Candidate, CandidatePatch, DataSource, DocumentOut, ExportResult, NewSignal, PilotAnalytics, State

log = logging.getLogger(__name__)


def csv_text(candidates):
    output = io.StringIO(newline='')
    output.write('﻿')
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(['ID', 'Компания', 'ИНН', 'Отрасль', 'Муниципалитет', 'Сигнал', 'Стадия', 'Инвестиции', 'Приоритет', 'Ответственный', 'Следующее действие'])
    for c in candidates:
        row = [c.id, c.company, '', c.sector, c.city, c.signal, c.stage, c.amount, c.score, c.owner, c.nextAction]
        writer.writerow(["'" + str(v) if re.match(r'^[\s\x00-\x1f]*[=+@-]', str(v)) else v for v in row])
    return output.getvalue()


def create_app(database_url=None, fetch_transport=None):
    @asynccontextmanager
    async def lifespan(app):
        engine = make_engine(database_url)
        try:
            initialize(engine)
            app.state.sessions = session_factory(engine)
            app.state.http = http_client(fetch_transport)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title='ИнвестСигнал API', version='0.2.0', lifespan=lifespan,
                  docs_url='/api/docs', openapi_url='/api/openapi.json', redoc_url=None)

    def session(request: Request):
        with request.app.state.sessions() as db:
            yield db

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        log.error('Database operation failed: %s', type(error).__name__)
        return JSONResponse(status_code=503, content={'detail': 'Сервер временно недоступен. Изменения не сохранены. Повторите попытку.'})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        def describe(e):
            field = '.'.join(str(p) for p in e['loc'][1:])
            return f'{field}: {e["msg"]}' if field else e['msg']
        return JSONResponse(status_code=422, content={'detail': 'Проверьте поля запроса: ' + '; '.join(describe(e) for e in error.errors())})

    @app.exception_handler(service.NotFound)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={'detail': str(error)})

    @app.exception_handler(service.Conflict)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={'detail': str(error)})

    @app.get('/api/health')
    def health(db: Session = Depends(session)):
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'database': 'ok', 'mode': 'demo'}

    @app.get('/api/state', response_model=State)
    def state(db: Session = Depends(session)):
        return State(candidates=service.list_candidates(db), sources=service.list_sources(db),
                     pilotAnalytics=db.get(MetadataRecord, 'pilot_analytics').payload)

    @app.get('/api/candidates', response_model=list[Candidate])
    def candidates(db: Session = Depends(session)):
        return service.list_candidates(db)

    @app.get('/api/candidates/{candidate_id}', response_model=Candidate)
    def candidate(candidate_id: str, db: Session = Depends(session)):
        return service.get_candidate(db, candidate_id)

    @app.post('/api/candidates', response_model=Candidate, status_code=201)
    def create(input: NewSignal, db: Session = Depends(session)):
        return service.create_manual(db, input)

    @app.patch('/api/candidates/{candidate_id}', response_model=Candidate)
    def update(candidate_id: str, patch: CandidatePatch, db: Session = Depends(session)):
        return service.patch_candidate(db, candidate_id, patch)

    @app.get('/api/sources', response_model=list[DataSource])
    def sources(db: Session = Depends(session)):
        return service.list_sources(db)

    @app.post('/api/sources/{source_id}/collect', description='Скачивает ленту источника и сохраняет новые документы.')
    def collect(source_id: str, request: Request, db: Session = Depends(session)):
        try:
            _, added = collect_source(db, source_id, request.app.state.http)
        except LookupError:
            raise HTTPException(404, 'Источник не найден.')
        source = next(s for s in service.list_sources(db) if s.id == source_id)
        return {'source': source, 'added': added}

    @app.get('/api/documents', response_model=list[DocumentOut])
    def documents(stage: Literal['new', 'skipped', 'rejected', 'signal', 'merged', 'error'] | None = None,
                  source: str | None = None, q: str | None = Query(None, max_length=200),
                  limit: int = Query(50, ge=1, le=200), db: Session = Depends(session)):
        return service.list_documents(db, stage, source, q, limit)

    @app.get('/api/analytics', response_model=PilotAnalytics,
             description='Fixed sample pilot metrics, not measured performance.')
    def analytics(db: Session = Depends(session)):
        return db.get(MetadataRecord, 'pilot_analytics').payload

    @app.post('/api/exports', response_model=ExportResult, status_code=201)
    def export(db: Session = Depends(session)):
        records = list(db.scalars(select(CandidateRecord).where(CandidateRecord.payload['status'].astext == 'approved').order_by(CandidateRecord.number).with_for_update()))
        # PostgreSQL rechecks the status predicate after waiting on another exporter.
        if not records:
            raise HTTPException(409, 'Нет одобренных проектов для экспорта. Сначала примите решение по карточке.')
        csv = csv_text([Candidate.model_validate(r.payload) for r in records])
        export_id = str(uuid4())
        db.add(ExportRecord(id=export_id, csv=csv, count=len(records)))
        for record in records:
            record.payload = {**record.payload, 'status': 'exported'}
            record.updated_at = datetime.now(ZoneInfo('UTC'))
        db.commit()
        return ExportResult(csv=csv, count=len(records), exportId=export_id)

    @app.get('/api/exports/{export_id}')
    def download(export_id: UUID, db: Session = Depends(session)):
        record = db.get(ExportRecord, str(export_id))
        if not record:
            raise HTTPException(404, 'Экспорт не найден.')
        return Response(record.csv, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="invest-signal-export.csv"'})

    return app


app = create_app()
