"""Read-only ORM mappers for the SQL views created in the initial migration.

The views are created by raw SQL in the Alembic migration (see
`alembic/versions/xxxx_initial_schema.py`); these classes exist only so
other engineers can query them through the ORM:

    from app.models.views import TimeOffBalanceView, WorkingScheduleHoursView
    from sqlalchemy import select

    rows = db.scalars(select(TimeOffBalanceView).where(...)).all()

These are NOT imported by `app/models/__init__.py` — registering them as
tables would confuse Alembic autogenerate.

- `v_time_off_balances`   — live leave balance per employee+type
  (allocated - taken), computed by the view, never stored (architecture doc §5).
- `v_working_schedule_hours` — derived weekly hours per working schedule,
  also never stored.
"""

from decimal import Decimal

from sqlalchemy import BigInteger, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class TimeOffBalanceView(Base):
    """SELECT * FROM v_time_off_balances (read-only)."""

    __tablename__ = "v_time_off_balances"

    employee_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    time_off_type_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    allocated: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    taken: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    remaining: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class WorkingScheduleHoursView(Base):
    """SELECT * FROM v_working_schedule_hours (read-only)."""

    __tablename__ = "v_working_schedule_hours"

    working_schedule_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    total_weekly_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2))