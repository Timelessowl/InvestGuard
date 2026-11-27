"""API integration tests use a disposable PostgreSQL database, never the demo database."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import make_engine
from app.llm import LlmSettings
from app.main import create_app

USERNAME = 'poc_user'
PASSWORD = os.environ.get('AUTH_PASSWORD') or 'local-test'


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    # The API fails closed without credentials; tests log in like the frontend does.
    monkeypatch.setenv('AUTH_USERNAME', USERNAME)
    monkeypatch.setenv('AUTH_PASSWORD', PASSWORD)


def api_client(app, auth=(USERNAME, PASSWORD)):
    client = TestClient(app)
    if auth:
        client.auth = auth
    return client


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
    with api_client(create_app(database_url, worker=False, llm_settings=LlmSettings())) as api:
        yield api, database_url
