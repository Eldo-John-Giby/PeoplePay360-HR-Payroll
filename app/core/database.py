"""Database engine, session factory, and FastAPI dependency.

Uses synchronous psycopg2 (simplest for a 24h hackathon — Alembic, seeds,
and all module services share one engine). All timestamps are TIMESTAMPTZ
(UTC) per the architecture doc §4.8.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # Echo SQL when debugging: settings.DEBUG  # (DEBUG flag not defined — leave off)
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()