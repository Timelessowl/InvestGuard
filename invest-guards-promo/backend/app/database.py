import json
import os
from pathlib import Path
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, URL, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .common import schedule_label
from .schemas import Candidate, PilotAnalytics, SourceConfig

ROOT = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


class CandidateRecord(Base):
    __tablename__ = 'candidates'
    number: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


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


def insert_candidate(db, candidate):
    record = CandidateRecord(payload=candidate.model_dump())
    db.add(record)
    db.flush()
    candidate.id = f'INV-{record.number:04d}'
    record.payload = candidate.model_dump()
    return record


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
