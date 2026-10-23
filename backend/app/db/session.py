"""Database session configuration."""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def database_connect_args(database_url: str) -> dict[str, bool]:
    """Return driver-specific synchronous connection arguments."""
    if make_url(database_url).get_backend_name() == "sqlite":
        return {"check_same_thread": False}
    return {}


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args=database_connect_args(settings.DATABASE_URL),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
