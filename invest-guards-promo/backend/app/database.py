import json
import os
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Computed, Date, DateTime, Float, ForeignKey, Integer, Numeric, Text, URL, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .common import schedule_label
from .schemas import Candidate, EventTypeConfig, PilotAnalytics, SourceConfig

ROOT = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


class CandidateRecord(Base):
    __tablename__ = 'candidates'
    number: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    # Generated from payload: indexed INN lookup without touching the write paths.
    inn_key: Mapped[str | None] = mapped_column(Text, Computed("payload ->> 'inn'", persisted=True))


class SourceRecord(Base):
    __tablename__ = 'sources'
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class DocumentRecord(Base):
    __tablename__ = 'documents'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    stage: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    reason: Mapped[str | None] = mapped_column(Text)
    extraction: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list | None] = mapped_column(JSONB)
    candidate_number: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('candidates.number'))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventTypeRecord(Base):
    __tablename__ = 'event_types'
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(Text)
    link_policy: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    deprecated: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))


class EventRecord(Base):
    __tablename__ = 'source_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, ForeignKey('event_types.code'))
    title: Mapped[str] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(Text)
    inn: Mapped[str | None] = mapped_column(Text)
    ogrn: Mapped[str | None] = mapped_column(Text)
    source_company_id: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    cadastral_number: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_precision: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    value_unit: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    processing_status: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    processing_reason: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    duplicate_of: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('source_events.id'))


class EventVersionRecord(Base):
    __tablename__ = 'source_event_versions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('source_events.id'))
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('clock_timestamp()'))
    content_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[dict] = mapped_column(JSONB)
    raw: Mapped[dict] = mapped_column(JSONB)


class CandidateEventRecord(Base):
    __tablename__ = 'candidate_events'
    candidate_number: Mapped[int] = mapped_column(BigInteger, ForeignKey('candidates.number'), primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('source_events.id'), primary_key=True)
    confidence: Mapped[str] = mapped_column(Text)
    linked_by: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


class SyncStateRecord(Base):
    __tablename__ = 'source_sync_state'
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sync_cursor: Mapped[dict | None] = mapped_column(JSONB)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_kind: Mapped[str | None] = mapped_column(Text)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_message: Mapped[str | None] = mapped_column(Text)
    snapshot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceRunRecord(Base):
    __tablename__ = 'source_runs'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text)
    run_trigger: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    fetched: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    inserted: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    updated: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    unchanged: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    rejected: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    retries: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    error: Mapped[str | None] = mapped_column(Text)


class VacancyEmployerRecord(Base):
    __tablename__ = 'vacancy_employers'
    employer_key: Mapped[str] = mapped_column(Text, primary_key=True)
    inn: Mapped[str | None] = mapped_column(Text)
    ogrn: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    hr_agency: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    source_ids: Mapped[list] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    excluded_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


class VacancyDetailRecord(Base):
    __tablename__ = 'vacancy_details'
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('source_events.id'), primary_key=True)
    source_id: Mapped[str] = mapped_column(Text)
    employer_key: Mapped[str] = mapped_column(Text, ForeignKey('vacancy_employers.employer_key'))
    source_employer_id: Mapped[str | None] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[str] = mapped_column(Text, server_default=text("'other'"))
    profession_code: Mapped[str | None] = mapped_column(Text)
    specialisation: Mapped[str | None] = mapped_column(Text)
    municipality: Mapped[str | None] = mapped_column(Text)
    work_places: Mapped[int | None] = mapped_column(Integer)
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric)
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric)
    currency: Mapped[str | None] = mapped_column(Text)
    employment: Mapped[str | None] = mapped_column(Text)
    schedule: Mapped[str | None] = mapped_column(Text)
    syndicated: Mapped[bool] = mapped_column(Boolean, server_default=text('false'))
    published_at: Mapped[date | None] = mapped_column(Date)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    fingerprint: Mapped[str] = mapped_column(Text)
    duplicate_of: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('vacancy_details.event_id'))


class VacancySnapshotRecord(Base):
    __tablename__ = 'vacancy_employer_snapshots'
    employer_key: Mapped[str] = mapped_column(Text, ForeignKey('vacancy_employers.employer_key'), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    active_count: Mapped[int] = mapped_column(Integer)
    active_work_places: Mapped[int] = mapped_column(Integer)
    new_30d: Mapped[int] = mapped_column(Integer)
    new_30d_work_places: Mapped[int] = mapped_column(Integer, server_default=text('0'))
    new_90d: Mapped[int] = mapped_column(Integer)
    production_count: Mapped[int] = mapped_column(Integer)
    engineering_count: Mapped[int] = mapped_column(Integer)
    management_count: Mapped[int] = mapped_column(Integer)
    municipalities: Mapped[list] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    new_municipalities: Mapped[list | None] = mapped_column(ARRAY(Text))
    categories: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class MetadataRecord(Base):
    __tablename__ = 'app_metadata'
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class ExportRecord(Base):
    __tablename__ = 'exports'
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    csv: Mapped[str] = mapped_column(Text)
    count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


def make_engine(url=None):
    database_url = url or os.environ.get('DATABASE_URL')
    if not database_url:
        database_url = URL.create('postgresql+psycopg', username=os.environ['PGUSER'], password=os.environ['PGPASSWORD'],
                                  host=os.environ.get('PGHOST', 'db'), database=os.environ['PGDATABASE'])
    return create_engine(database_url, pool_pre_ping=True, pool_size=3, max_overflow=2)


def initialize(engine):
    # Migrations and first-time demo seed form one transaction, serialized across startups.
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(1847301)'))
        connection.execute(text('CREATE TABLE IF NOT EXISTS schema_versions (version TEXT PRIMARY KEY)'))
        for migration in sorted((ROOT / 'migrations').glob('*.sql')):
            if connection.execute(text('SELECT 1 FROM schema_versions WHERE version=:v'), {'v': migration.name}).scalar():
                continue
            for statement in migration.read_text().split(';'):
                if statement.strip():
                    connection.execute(text(statement))
            connection.execute(text('INSERT INTO schema_versions VALUES (:v)'), {'v': migration.name})
        sync_sources(connection)
        sync_event_types(connection)
        validate_hiring_config()
        seeded = connection.execute(text("SELECT 1 FROM app_metadata WHERE key='demo_seed_v1'")).scalar()
        if seeded:
            return
        candidates = [Candidate.model_validate(c).model_dump() for c in json.loads((ROOT / 'seed/candidates.json').read_text())]
        analytics = PilotAnalytics.model_validate_json((ROOT / 'seed/analytics.json').read_text()).model_dump()
        connection.execute(CandidateRecord.__table__.insert(), [{'number': int(c['id'].split('-')[1]), 'payload': c} for c in candidates])
        connection.execute(MetadataRecord.__table__.insert(), [{'key': 'pilot_analytics', 'payload': analytics}, {'key': 'demo_seed_v1', 'payload': {'version': 1}}])


def sync_sources(connection):
    # Source config lives in seed/sources.json; runtime fields (last run, error) are preserved.
    configs = [SourceConfig.model_validate(s) for s in json.loads((ROOT / 'seed/sources.json').read_text())]
    current = {row.id: row.payload for row in connection.execute(text('SELECT id, payload FROM sources'))}
    runtime = {'lastRun': 'Ещё не запускался', 'lastRunAt': None, 'lastError': None, 'status': 'active'}
    for config in configs:
        previous = current.get(config.id, {})
        payload = {**runtime, **{k: previous[k] for k in runtime if k in previous}, **config.model_dump(),
                   'schedule': schedule_label(config.scheduleMinutes)}
        connection.execute(text('INSERT INTO sources (id, payload) VALUES (:id, CAST(:p AS jsonb)) '
                                'ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload'), {'id': config.id, 'p': json.dumps(payload)})
    connection.execute(text('DELETE FROM sources WHERE NOT (id = ANY(:ids))'), {'ids': [c.id for c in configs]})


def sync_event_types(connection):
    # Types are referenced by events: ones dropped from the seed are marked deprecated, never deleted.
    types = [EventTypeConfig.model_validate(t) for t in json.loads((ROOT / 'seed/event_types.json').read_text())]
    for item in types:
        connection.execute(text(
            'INSERT INTO event_types (code, title, category, polarity, link_policy, stage, description, deprecated) '
            'VALUES (:code, :title, :category, :polarity, :policy, :stage, :description, false) '
            'ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title, category = EXCLUDED.category, polarity = EXCLUDED.polarity, '
            'link_policy = EXCLUDED.link_policy, stage = EXCLUDED.stage, description = EXCLUDED.description, deprecated = false'),
            {**item.model_dump(exclude={'linkPolicy'}), 'policy': item.linkPolicy})
    connection.execute(text('UPDATE event_types SET deprecated = true WHERE NOT (code = ANY(:codes))'), {'codes': [t.code for t in types]})


def validate_hiring_config():
    # A broken category dictionary or threshold file stops startup instead of silently skipping signals.
    from .vacancies.categories import load_book
    from .vacancies.signals import load_rules
    load_book()
    load_rules()


def insert_candidate(db, candidate):
    record = CandidateRecord(payload=candidate.model_dump())
    db.add(record)
    db.flush()
    candidate.id = f'INV-{record.number:04d}'
    record.payload = candidate.model_dump()
    return record


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
