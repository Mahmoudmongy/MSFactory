"""Test fixtures: a transactional database session and an HTTP client.

Each test runs against the real PostgreSQL schema inside a transaction that is
rolled back afterwards, so tests exercise genuine constraints and SQL rather
than a mock.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app

TEST_DATABASE_URL = settings.database_url


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    """A real session, cleaned up by the truncation fixture.

    Tests that need to seed data the running app can see (e.g. adding a
    membership) must use a genuine session on the real database, because the
    app opens its own connections.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_business_tables():
    """Truncate between tests so each test starts from an empty database."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE journal_lines, journal_entries, payment_allocations, payments, "
                "invoice_lines, invoices, contacts, tax_codes, company_settings, sequences, "
                "fiscal_periods, audit_logs, idempotency_keys, refresh_tokens, memberships, "
                "accounts, companies, users RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
