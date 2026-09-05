"""Attendance model.

`worked_hours` and `status` are computed once at check-out time and STORED,
not recomputed live — a later change to the employee's working schedule must
not silently rewrite historical attendance records. This is documented
denormalization #2 (architecture doc §5).

Overlapping check-ins (a second check_in before the previous row got a
check_out) can't be expressed as a DB constraint — the service layer rejects
a new check-in while an open (`check_out IS NULL`) row exists.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin
from app.models.enums import AttendanceStatus


class Attendance(Base, TimestampMixin):
    __tablename__ = "attendances"
    __table_args__ = (
        CheckConstraint(
            "check_out IS NULL OR check_out > check_in",
            name="ck_attendances_checkout_after_checkin",
        ),
        # Dashboard's hottest query: attendance for employee X in date range Y.
        Index("ix_attendances_employee_checkin", "employee_id", "check_in"),
        Index("ix_attendances_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True  # null while still checked in
    )
    # NUMERIC(6,2) hours; computed at check-out, stored per §5 denormalization.
    worked_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    status: Mapped[AttendanceStatus] = mapped_column(
        Enum(AttendanceStatus, name="attendancestatus", native_enum=True),
        nullable=False,
    )
    is_manual_correction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    corrected_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship(back_populates="attendances")