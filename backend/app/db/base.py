"""SQLAlchemy declarative base shared by every ORM model in citymind-backend."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models. Alembic's `env.py` imports `Base.metadata` from here."""
