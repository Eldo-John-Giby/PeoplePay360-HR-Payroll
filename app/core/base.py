"""Declarative Base and shared mixins.

Naming convention is mandatory so every constraint/index gets a deterministic
name — this keeps Alembic autogenerate diffs clean across the whole team.

Conventions used across ALL tables (architecture doc §4):
- PKs are BIGSERIAL (BIGINT autoincrement).
- created_at / updated_at are TIMESTAMPTZ with server-side defaults.
- Money is NUMERIC(12,2) — never float (see §4.9).
- Soft delete via is_active / status — never hard DELETE (see §4.5).
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Server-side timestamptz created_at / updated_at on every table (§4.6)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
