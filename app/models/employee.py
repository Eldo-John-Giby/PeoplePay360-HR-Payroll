"""Employee & contract models.

- `employees` self-references via `manager_id` for the reporting line.
- `employee_bank_details` is a separate 1:1 table (sensitive, optional,
  independently-updatable; "missing bank details" is a clean NOT EXISTS check).
- `contracts` snapshots department/job/schedule/structure at signing time and
  enforces "at most one RUNNING contract per employee" with a partial unique
  index (see the migration). Full non-overlapping-date-range exclusion
  (btree_gist EXCLUDE) is a documented future-hardening note in the README.

Optimistic locking (`version_id`) is enabled on Contract per architecture
doc §5.1.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin
from app.models.enums import ContractStatus, EmployeeStatus, EmployeeType


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"
    __table_args__ = (
        Index("ix_employees_department_id", "department_id"),
        Index("ix_employees_job_position_id", "job_position_id"),
        Index("ix_employees_manager_id", "manager_id"),
        Index("ix_employees_status", "status"),
        Index("ix_employees_employee_type", "employee_type"),
        # Trigram GIN index for the "Search employees…" box — created in the
        # initial migration (needs pg_trgm extension), defined here so
        # autogenerate keeps it in sync.
        Index(
            "ix_employees_full_name_trgm",
            "full_name",
            postgresql_using="gin",
            postgresql_ops={"full_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    work_email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_position_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("job_positions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    manager_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
    )
    working_schedule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("working_schedules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    employee_type: Mapped[EmployeeType] = mapped_column(
        Enum(EmployeeType, name="employeetype", native_enum=True), nullable=False
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(EmployeeStatus, name="employeestatus", native_enum=True),
        nullable=False,
        server_default="active",
    )
    date_of_joining: Mapped[date] = mapped_column(Date, nullable=False)
    work_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    company_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    manager: Mapped["Employee | None"] = relationship(
        remote_side="Employee.id", back_populates="reports"
    )
    reports: Mapped[list["Employee"]] = relationship(back_populates="manager")
    bank_detail: Mapped["EmployeeBankDetail | None"] = relationship(
        back_populates="employee", uselist=False, cascade="all, delete-orphan"
    )
    contracts: Mapped[list["Contract"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    user: Mapped["User | None"] = relationship(back_populates="employee")
    attendances: Mapped[list["Attendance"]] = relationship(
        back_populates="employee"
    )
    time_off_requests: Mapped[list["TimeOffRequest"]] = relationship(
        back_populates="employee",
        # TimeOffRequest has two FKs to employees (employee_id, approver_id).
        foreign_keys="TimeOffRequest.employee_id",
    )
    payslips: Mapped[list["Payslip"]] = relationship(back_populates="employee")
    payruns: Mapped[list["Payrun"]] = relationship(
        secondary="payrun_employees", back_populates="employees"
    )


class EmployeeBankDetail(Base, TimestampMixin):
    __tablename__ = "employee_bank_details"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="CASCADE"),
        unique=True,  # 1:1
        nullable=False,
    )
    account_holder_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(34), nullable=True)
    ifsc_or_swift: Mapped[str | None] = mapped_column(String(20), nullable=True)

    employee: Mapped[Employee] = relationship(back_populates="bank_detail")


class Contract(Base, TimestampMixin):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint(
            "wage_monthly >= 0", name="ck_contracts_wage_non_negative"
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_contracts_end_after_start",
        ),
        # The wireframe's "one employee should not have multiple Running
        # contracts for the same period" — guaranteed at the DB level by a
        # partial unique index (one running row per employee).
        Index(
            "uq_contracts_one_running_per_employee",
            "employee_id",
            unique=True,
            postgresql_where="status = 'running'",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_number: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    department_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_position_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("job_positions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    working_schedule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("working_schedules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    salary_structure_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("salary_structures.id", ondelete="RESTRICT"),
        nullable=False,
    )
    wage_monthly: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # null = open-ended
    status: Mapped[ContractStatus] = mapped_column(
        Enum(ContractStatus, name="contractstatus", native_enum=True),
        nullable=False,
        server_default="draft",
    )
    # Optimistic lock (architecture doc §5.1). Must be defined BEFORE
    # __mapper_args__ so the name resolves inside the class body.
    version_id: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    __mapper_args__ = {"version_id_col": version_id}

    employee: Mapped[Employee] = relationship(back_populates="contracts")
    # Defined in app/models/payroll.py (forward reference).
    salary_structure: Mapped["SalaryStructure"] = relationship(
        back_populates="contracts"
    )