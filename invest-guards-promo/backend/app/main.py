import csv
import io
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .database import CandidateRecord, ExportRecord, MetadataRecord, SourceRecord, initialize, make_engine, session_factory
from .schemas import Candidate, CandidatePatch, DataSource, ExportResult, NewSignal, PilotAnalytics, State

log = logging.getLogger(__name__)


def display_date():
    now = datetime.now(ZoneInfo('Asia/Yekaterinburg'))
    months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
    return f'{now.day} {months[now.month-1]}, {now:%H:%M}'


def candidate_number(candidate_id):
    if not re.fullmatch(r'INV-\d+', candidate_id):
        raise HTTPException(404, 'Карточка не найдена.')
    number = int(candidate_id.split('-')[1])
    if number > 9223372036854775807:
        raise HTTPException(404, 'Карточка не найдена.')
    return number


def csv_text(candidates):
    output = io.StringIO(newline='')
    output.write('\ufeff')
    writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(['ID', 'Компания', 'ИНН', 'Отрасль', 'Муниципалитет', 'Сигнал', 'Стадия', 'Инвестиции', 'Приоритет', 'Ответственный', 'Следующее действие'])
    for c in candidates:
        row = [c.id, c.company, '', c.sector, c.city, c.signal, c.stage, c.amount, c.score, c.owner, c.nextAction]
        writer.writerow(["'" + str(v) if re.match(r'^[\s\x00-\x1f]*[=+@-]', str(v)) else v for v in row])
    return output.getvalue()


def create_app(database_url=None):
    @asynccontextmanager
    async def lifespan(app):
        engine = make_engine(database_url)
        try:
            initialize(engine)
            app.state.sessions = session_factory(engine)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title='ИнвестСигнал Demo API', version='0.1.0', lifespan=lifespan,
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
        return JSONResponse(status_code=422, content={'detail': 'Проверьте поля запроса: ' + '; '.join('.'.join(str(p) for p in e['loc'][1:]) + ': ' + e['msg'] for e in error.errors())})

    @app.get('/api/health')
    def health(db: Session = Depends(session)):
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'database': 'ok', 'mode': 'demo'}

    @app.get('/api/state', response_model=State)
    def state(db: Session = Depends(session)):
        return State(candidates=[r.payload for r in db.scalars(select(CandidateRecord).order_by(CandidateRecord.number.desc()))],
                     sources=[r.payload for r in db.scalars(select(SourceRecord).order_by(SourceRecord.id))],
                     pilotAnalytics=db.get(MetadataRecord, 'pilot_analytics').payload)

    @app.get('/api/candidates', response_model=list[Candidate])
    def candidates(db: Session = Depends(session)):
        return [r.payload for r in db.scalars(select(CandidateRecord).order_by(CandidateRecord.number.desc()))]

    @app.get('/api/candidates/{candidate_id}', response_model=Candidate)
    def candidate(candidate_id: str, db: Session = Depends(session)):
        record = db.get(CandidateRecord, candidate_number(candidate_id))
        if not record:
            raise HTTPException(404, 'Карточка не найдена.')
        return record.payload

    @app.post('/api/candidates', response_model=Candidate, status_code=201)
    def create(input: NewSignal, db: Session = Depends(session)):
        date = display_date()
        initials = re.sub(r'[«»"\s]', '', re.sub(r'^(ООО|АО|ПАО)\s*', '', input.company))[:2].upper()
        result = Candidate(**input.model_dump(), id='pending', initials=initials,
            signalType='Ручной сигнал', sourceDate=date, score=60, urgency='medium', urgencyLabel='Средняя',
            status='new', owner='Не назначен', amount='Не указано', stage='Требует проверки',
            summary='Карточка создана аналитиком вручную. Факты и параметры проекта требуют экспертной проверки.',
            factors={'Релевантность': 65, 'Уверенность': 55, 'Региональный эффект': 60, 'Срочность': 58},
            recommendation='Проверить источник и запросить недостающие данные',
            rationale='Для карточки пока недостаточно независимых подтверждений.', nextAction='Запросить данные',
            evidence=[{'source': input.source, 'date': date, 'excerpt': input.signal, 'highlights': []}])
        record = CandidateRecord(payload=result.model_dump())
        db.add(record)
        db.flush()
        result.id = f'INV-{record.number:04d}'
        record.payload = result.model_dump()
        db.commit()
        return result

    @app.patch('/api/candidates/{candidate_id}', response_model=Candidate)
    def update(candidate_id: str, patch: CandidatePatch, db: Session = Depends(session)):
        record = db.scalar(select(CandidateRecord).where(CandidateRecord.number == candidate_number(candidate_id)).with_for_update())
        if not record:
            raise HTTPException(404, 'Карточка не найдена.')
        result = Candidate.model_validate({**record.payload, **patch.model_dump(exclude_unset=True)})
        if patch.status == 'approved' and result.owner == 'Не назначен':
            result.owner = 'А. Крылова'
        record.payload = result.model_dump()
        record.updated_at = datetime.now(ZoneInfo('UTC'))
        db.commit()
        return result

    @app.get('/api/sources', response_model=list[DataSource])
    def sources(db: Session = Depends(session)):
        return [r.payload for r in db.scalars(select(SourceRecord).order_by(SourceRecord.id))]

    @app.post('/api/sources/{source_id}/retry', response_model=DataSource,
              description='Simulated collection only; no external documents are fetched.')
    def retry(source_id: str, db: Session = Depends(session)):
        record = db.scalar(select(SourceRecord).where(SourceRecord.id == source_id).with_for_update())
        if not record:
            raise HTTPException(404, 'Источник не найден.')
        result = DataSource.model_validate({**record.payload, 'status': 'active', 'lastRun': display_date()})
        record.payload = result.model_dump()
        db.commit()
        return result

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
