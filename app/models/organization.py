"""Organization / master data models.

`company_id` is a nullable FK on every master table (except `companies`
itself) for future multi-company support — it defaults to company id 1 in the
seed data. Department has a self-referencing FK for the hierarchy used by
dashboard roll-ups. Working schedule weekly pattern lives in normalized
`working_schedule_lines` (3NF — no "Mon-Fri 9-5" blob).

`total_weekly_hours` is DERIVED: exposed via the SQL view
`v_working_schedule_hours` (created in the first Alembic migration) and a
Python convenience property on the ORM object. Never stored as a column.
"""

from datetime import time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin
from app.models.enums import ScheduleType


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class Department(Base, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("name", "company_id", name="uq_departments_name_company"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_department_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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

    parent: Mapped["Department | None"] = relationship(
        remote_side="Department.id", back_populates="children"
    )
    children: Mapped[list["Department"]] = relationship(
        back_populates="parent"
    )
    job_positions: Mapped[list["JobPosition"]] = relationship(
        back_populates="department"
    )


class JobPosition(Base, TimestampMixin):
    __tablename__ = "job_positions"
    __table_args__ = (
        UniqueConstraint(
            "title", "department_id", name="uq_job_positions_title_department"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    department_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    department: Mapped[Department] = relationship(back_populates="job_positions")


class WorkingSchedule(Base, TimestampMixin):
    __tablename__ = "working_schedules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    schedule_type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, name="schedule_type", native_enum=True), nullable=False
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

    lines: Mapped[list["WorkingScheduleLine"]] = relationship(
        back_populates="working_schedule",
        order_by="WorkingScheduleLine.day_of_week",
        cascade="all, delete-orphan",
    )

    @property
    def total_weekly_hours(self) -> float:
        """Python-side convenience for the derived weekly total.

        For SQL-side aggregation use the `v_working_schedule_hours` view
        (created in the initial Alembic migration). This property is NOT a
        stored column — see architecture doc §5.
        """
        total = sum(
            (
                (line.end_time.hour * 60 + line.end_time.minute)
                - (line.start_time.hour * 60 + line.start_time.minute)
                - line.break_minutes
            )
            for line in self.lines
        )
        return round(total / 60.0, 2)


class WorkingScheduleLine(Base):
    __tablename__ = "working_schedule_lines"
    __table_args__ = (
        CheckConstraint(
            "day_of_week BETWEEN 0 AND 6",
            name="ck_working_schedule_lines_day_of_week_range",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="ck_working_schedule_lines_end_after_start",
        ),
        Index(
            "ix_working_schedule_lines_schedule_id",
            "working_schedule_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    working_schedule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("working_schedules.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Mon..6=Sun
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )

    working_schedule: Mapped[WorkingSchedule] = relationship(
        back_populates="lines"
    )