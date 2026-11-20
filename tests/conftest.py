"""Test fixtures: a transactional database session and an HTTP client.

Tests run against real PostgreSQL and truncate tables between cases, so they
must never touch a live database. The environment is redirected to a dedicated
test database *before* the application is imported, and a guard refuses to run
if the two ever point at the same place — a mistaken test run must not be able
to delete a customer's books.
"""
from __future__ import annotations

import os
import urllib.parse

import pytest

# Argon2 is deliberately slow so a stolen hash is expensive to crack. In tests
# it is pure overhead — hundreds of registrations at production cost dominate
# the run. These must be set before the application (and its Settings object)
# is imported, hence the module-level assignment rather than a fixture.
os.environ.setdefault("MSFACTORY_ARGON2_TIME_COST", "1")
os.environ.setdefault("MSFACTORY_ARGON2_MEMORY_COST", "8192")
os.environ.setdefault("MSFACTORY_ARGON2_PARALLELISM", "1")


def _load_dotenv() -> None:
    """Load ``.env`` into the environment so the derivation below sees it.

    pydantic-settings reads ``.env`` into the Settings object, not into
    ``os.environ``, so a value set only there would be invisible here.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _test_database_url() -> str:
    """Derive the test database from the configured one.

    ``.../postgres`` becomes ``.../msfactory_test``. A dedicated
    ``MSFACTORY_TEST_DATABASE_URL`` overrides the derivation entirely.
    """
    _load_dotenv()
    override = os.environ.get("MSFACTORY_TEST_DATABASE_URL")
    if override:
        return override
    configured = os.environ.get("MSFACTORY_DATABASE_URL", "")
    if not configured:
        return "postgresql+psycopg://msfactory:msfactory_dev_pw@127.0.0.1:5432/msfactory_test"
    parts = urllib.parse.urlsplit(configured)
    return urllib.parse.urlunsplit(parts._replace(path="/msfactory_test"))


TEST_DATABASE_URL = _test_database_url()
os.environ["MSFACTORY_DATABASE_URL"] = TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


def _database_name(url: str) -> str:
    return urllib.parse.urlsplit(url).path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def _refuse_to_run_on_live_data():
    """Abort if the test URL resolves to anything other than a ``*_test`` database."""
    name = _database_name(settings.database_url)
    if not name.endswith("_test"):
        pytest.exit(
            f"Refusing to run: test database '{name}' does not end in '_test'. "
            "Set MSFACTORY_TEST_DATABASE_URL to a disposable database.",
            returncode=1,
        )


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Rebuild the schema from the models at the start of every session.

    Dropping first matters: ``create_all`` only creates missing *tables*, so a
    column added to an existing table would otherwise be silently absent and
    the tests would fail on a database that no longer matches the models. This
    is safe precisely because the guard above proves we are on a *_test
    database.
    """
    Base.metadata.drop_all(bind=engine)
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
                "TRUNCATE stock_consumptions, stock_layers, stock_movements, item_variants, "
                "items, item_groups, warehouses, units_of_measure, "
                "work_order_materials, work_orders, "
                "journal_lines, journal_entries, payment_allocations, payments, "
                "invoice_lines, invoices, contacts, tax_codes, company_settings, sequences, "
                "fiscal_periods, audit_logs, idempotency_keys, refresh_tokens, memberships, "
                "import_mappings, import_jobs, "
                "accounts, companies, users RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
