import json
import os
from pathlib import Path
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, URL, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .schemas import Candidate, DataSource, PilotAnalytics

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
        seeded = connection.execute(text("SELECT 1 FROM app_metadata WHERE key='demo_seed_v1'")).scalar()
        if seeded:
            return
        candidates = [Candidate.model_validate(c).model_dump() for c in json.loads((ROOT / 'seed/candidates.json').read_text())]
        sources = [DataSource.model_validate(s).model_dump() for s in json.loads((ROOT / 'seed/sources.json').read_text())]
        analytics = PilotAnalytics.model_validate_json((ROOT / 'seed/analytics.json').read_text()).model_dump()
        connection.execute(CandidateRecord.__table__.insert(), [{'number': int(c['id'].split('-')[1]), 'payload': c} for c in candidates])
        connection.execute(SourceRecord.__table__.insert(), [{'id': s['id'], 'payload': s} for s in sources])
        connection.execute(MetadataRecord.__table__.insert(), [{'key': 'pilot_analytics', 'payload': analytics}, {'key': 'demo_seed_v1', 'payload': {'version': 1}}])


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
