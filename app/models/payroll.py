"""Payroll models — salary structures/rules, payruns, payslips.

Key contract decisions (see README for the full rationale):
- `payslip_lines.code/name/category` SNAPSHOT the salary rule at computation
  time — payslips are legal/historical records (denormalization #1, §5).
- `payslips.contract_id` is NULLABLE on purpose: an employee selected into a
  payrun with no running contract still gets a payslip carrying a
  `missing_contract` warning (per the schema doc's edge cases).
- `UNIQUE(payrun_id, employee_id)` makes duplicate payslips per payrun
  structurally impossible; `overlapping_period` across *different* payruns is
  a service-layer query + payslip_warning.
- Optimistic locking via `version_id` on Payrun and Payslip (§5.1).
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base import Base, TimestampMixin
from app.models.enums import (
    ComputationMethod,
    EmployeeType,
    PayslipWarningType,
    PayrunStatus,
    SalaryRuleCategory,
)


class SalaryRule(Base, TimestampMixin):
    __tablename__ = "salary_rules"
    __table_args__ = (
        # Exactly one of amount/percentage/formula must be set, matching the
        # computation_method. `percentage_base_code` references another rule's
        # `code` — validated in the app layer, not as an FK (bulk imports may
        # reference rules inserted later in the same batch).
        CheckConstraint(
            "(computation_method = 'fixed' AND amount IS NOT NULL"
            " AND percentage IS NULL AND formula IS NULL)"
            " OR (computation_method = 'percentage' AND percentage IS NOT NULL"
            " AND amount IS NULL AND formula IS NULL)"
            " OR (computation_method = 'formula' AND formula IS NOT NULL"
            " AND amount IS NULL AND percentage IS NULL)",
            name="ck_salary_rules_method_consistency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[SalaryRuleCategory] = mapped_column(
        Enum(SalaryRuleCategory, name="salaryrulecategory", native_enum=True),
        nullable=False,
    )
    computation_method: Mapped[ComputationMethod] = mapped_column(
        Enum(ComputationMethod, name="computationmethod", native_enum=True),
        nullable=False,
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    percentage_base_code: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_sequence: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )


class SalaryStructure(Base, TimestampMixin):
    __tablename__ = "salary_structures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    company_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    rules: Mapped[list["SalaryStructureRule"]] = relationship(
        back_populates="salary_structure",
        order_by="SalaryStructureRule.sequence",
        cascade="all, delete-orphan",
    )
    contracts: Mapped[list["Contract"]] = relationship(back_populates="salary_structure")


class SalaryStructureRule(Base):
    """Junction: which rules a structure includes, in which execution order.

    This is what makes rule *order* configurable per structure (wireframe:
    "form view manages included salary rules and their execution sequence").
    """

    __tablename__ = "salary_structure_rules"
    __table_args__ = (
        UniqueConstraint(
            "salary_structure_id",
            "salary_rule_id",
            name="uq_salary_structure_rules_structure_rule",
        ),
        # The computation engine runs this query first.
        Index(
            "ix_salary_structure_rules_structure_sequence",
            "salary_structure_id",
            "sequence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    salary_structure_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("salary_structures.id", ondelete="CASCADE"),
        nullable=False,
    )
    salary_rule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("salary_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    salary_structure: Mapped[SalaryStructure] = relationship(
        back_populates="rules"
    )
    salary_rule: Mapped[SalaryRule] = relationship()


class Payrun(Base, TimestampMixin):
    __tablename__ = "payruns"
    __table_args__ = (
        CheckConstraint(
            "period_end >= period_start", name="ck_payruns_period_range"
        ),
        Index("ix_payruns_status", "status"),
        Index("ix_payruns_period", "period_start", "period_end"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    salary_structure_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("salary_structures.id", ondelete="RESTRICT"),
        nullable=False,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    department_filter_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    employee_type_filter: Mapped[EmployeeType | None] = mapped_column(
        Enum(EmployeeType, name="employeetype", native_enum=True), nullable=True
    )
    status: Mapped[PayrunStatus] = mapped_column(
        Enum(PayrunStatus, name="payrunstatus", native_enum=True),
        nullable=False,
        server_default="draft",
    )
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Optimistic lock (§5.1). Defined before __mapper_args__ (name resolution).
    version_id: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    __mapper_args__ = {"version_id_col": version_id}

    salary_structure: Mapped[SalaryStructure] = relationship()
    employees: Mapped[list["Employee"]] = relationship(
        secondary="payrun_employees", back_populates="payruns"
    )
    payslips: Mapped[list["Payslip"]] = relationship(
        back_populates="payrun", cascade="all, delete-orphan"
    )


class PayrunEmployee(Base):
    """Junction: the EXPLICIT employee selection from wizard Step 2."""

    __tablename__ = "payrun_employees"

    payrun_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payruns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class Payslip(Base, TimestampMixin):
    __tablename__ = "payslips"
    __table_args__ = (
        # THE duplicate-payslip guard: one payslip per employee per payrun.
        UniqueConstraint(
            "payrun_id", "employee_id", name="uq_payslips_payrun_employee"
        ),
        # Overlap checks across different payruns (service-layer query).
        Index(
            "ix_payslips_employee_period",
            "employee_id",
            "period_start",
            "period_end",
        ),
        Index("ix_payslips_payrun_id", "payrun_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payrun_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payruns.id", ondelete="CASCADE"),
        nullable=False,
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # NULLABLE on purpose — a missing-contract payslip still exists with a
    # `missing_contract` warning (schema doc edge cases).
    contract_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("contracts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    worked_days: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )
    gross_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    net_salary: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    status: Mapped[PayrunStatus] = mapped_column(
        Enum(PayrunStatus, name="payrunstatus", native_enum=True),
        nullable=False,
        server_default="draft",
    )
    # Optimistic lock (§5.1). Defined before __mapper_args__.
    version_id: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    __mapper_args__ = {"version_id_col": version_id}

    payrun: Mapped[Payrun] = relationship(back_populates="payslips")
    employee: Mapped["Employee"] = relationship(back_populates="payslips")
    contract: Mapped["Contract | None"] = relationship()
    lines: Mapped[list["PayslipLine"]] = relationship(
        back_populates="payslip",
        order_by="PayslipLine.sequence",
        cascade="all, delete-orphan",
    )
    warnings: Mapped[list["PayslipWarning"]] = relationship(
        back_populates="payslip", cascade="all, delete-orphan"
    )


class PayslipLine(Base):
    """Salary-rule breakdown on a payslip.

    `code/name/category` are intentionally denormalized SNAPSHOTS of the rule
    at computation time (denormalization #1, §5) — editing a Salary Rule next
    month must not retroactively change a paid payslip.
    """

    __tablename__ = "payslip_lines"
    __table_args__ = (
        Index("ix_payslip_lines_payslip_sequence", "payslip_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payslip_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payslips.id", ondelete="CASCADE"),
        nullable=False,
    )
    salary_rule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("salary_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[SalaryRuleCategory] = mapped_column(
        Enum(SalaryRuleCategory, name="salaryrulecategory", native_enum=True),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    payslip: Mapped[Payslip] = relationship(back_populates="lines")


class PayslipWarning(Base):
    __tablename__ = "payslip_warnings"
    __table_args__ = (Index("ix_payslip_warnings_payslip_id", "payslip_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    payslip_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payslips.id", ondelete="CASCADE"),
        nullable=False,
    )
    warning_type: Mapped[PayslipWarningType] = mapped_column(
        Enum(PayslipWarningType, name="payslipwarningtype", native_enum=True),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    payslip: Mapped[Payslip] = relationship(back_populates="warnings")