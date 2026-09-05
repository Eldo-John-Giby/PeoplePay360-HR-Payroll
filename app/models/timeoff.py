"""Time Off models.

The leave balance is deliberately NOT stored: it is computed by the SQL view
`v_time_off_balances` (created in the initial migration) as
`SUM(approved allocations) - SUM(approved requests)` per employee+type.
A stored running total would drift out of sync — this is the documented
counter-example to denormalization (architecture doc §5).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin
from app.models.enums import (
    AllocationStatus,
    TimeOffRequestStatus,
    TimeOffUnit,
)


class TimeOffType(Base, TimestampMixin):
    __tablename__ = "time_off_types"
    __table_args__ = (
        UniqueConstraint("name", "company_id", name="uq_time_off_types_name_company"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[TimeOffUnit] = mapped_column(
        Enum(TimeOffUnit, name="time_off_unit", native_enum=True), nullable=False
    )
    requires_allocation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    affects_payroll: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    company_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class TimeOffAllocation(Base, TimestampMixin):
    __tablename__ = "time_off_allocations"
    __table_args__ = (
        CheckConstraint(
            "allocated_amount > 0",
            name="ck_time_off_allocations_amount_positive",
        ),
        # Balance-lookup hot path: employee + type + status.
        Index(
            "ix_time_off_allocations_emp_type_status",
            "employee_id",
            "time_off_type_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    time_off_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("time_off_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[AllocationStatus] = mapped_column(
        Enum(AllocationStatus, name="allocationstatus", native_enum=True),
        nullable=False,
        server_default="draft",
    )
    approver_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Optimistic lock (architecture doc §5.1).
    version_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )

    __mapper_args__ = {"version_id_col": version_id}


class TimeOffRequest(Base, TimestampMixin):
    __tablename__ = "time_off_requests"
    __table_args__ = (
        CheckConstraint("duration > 0", name="ck_time_off_requests_duration_positive"),
        CheckConstraint(
            "date_to >= date_from", name="ck_time_off_requests_date_range"
        ),
        Index("ix_time_off_requests_employee_status", "employee_id", "status"),
        Index("ix_time_off_requests_type", "time_off_type_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    time_off_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("time_off_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    duration: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[TimeOffRequestStatus] = mapped_column(
        Enum(TimeOffRequestStatus, name="timeoffrequeststatus", native_enum=True),
        nullable=False,
        server_default="to_approve",
    )
    approver_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship(
        back_populates="time_off_requests",
        # Two FKs to employees (employee_id, approver_id) — disambiguate.
        foreign_keys=[employee_id],
    )