"""Database layer for citymind-backend: engine/session management and ORM models."""

from __future__ import annotations

from app.db.base import Base
from app.db import models  # noqa: F401 - import registers ORM models on Base.metadata

__all__ = ["Base", "models"]
