"""Engine and session factory.

Sessions are created per request and committed explicitly by the service
layer, so a single request maps to a single database transaction.

Two engines exist because the database is remote and a round trip is expensive:
``engine`` is the transactional engine used for writes, and ``read_engine`` runs
reads in autocommit. A transactional read pays BEGIN and COMMIT as well as the
statement, and the pre-ping that guards the write pool adds another round trip;
for a read that does not need atomicity across statements that is pure latency.
The read pool recycles connections so one dropped by the provider is replaced
rather than handed out dead, and ``read_session`` retries once if it dies anyway.
"""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=settings.db_echo,
    future=True,
)

read_engine = create_engine(
    settings.database_url,
    pool_pre_ping=False,
    pool_recycle=settings.db_pool_recycle,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    echo=settings.db_echo,
    isolation_level="AUTOCOMMIT",
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
ReadSessionLocal = sessionmaker(
    bind=read_engine, autoflush=False, expire_on_commit=False, class_=Session
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def read_session() -> Iterator[Session]:
    """A short-lived autocommit session, retried once on a dropped connection."""
    for attempt in (1, 2):
        session = ReadSessionLocal()
        try:
            yield session
            return
        except OperationalError:
            if attempt == 2:
                raise
        finally:
            session.close()
