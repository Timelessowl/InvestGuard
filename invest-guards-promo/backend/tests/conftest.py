"""API integration tests use a disposable PostgreSQL database, never the demo database."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import make_engine
from app.main import create_app


@pytest.fixture
def database_url():
    admin = make_engine()
    name = 'investsignal_test_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(f'CREATE DATABASE {name}'))
    try:
        yield admin.url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def client(database_url):
    with TestClient(create_app(database_url)) as api:
        yield api, database_url
