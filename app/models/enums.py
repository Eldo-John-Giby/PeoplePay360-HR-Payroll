"""Shared enums — closed sets stored as Postgres native ENUM types.

Postgres native ENUMs give DB-level validation for free (architecture doc §1).
Use the SAME SQLAlchemy `Enum(SomeEnum, name=...)` with a shared `name` for
columns that should share one PG type (e.g. employee_type and
payrun.employee_type_filter both use `employeetype`).
"""

from enum import Enum


class ScheduleType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    custom = "custom"


class EmployeeType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    intern = "intern"


class EmployeeStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    terminated = "terminated"


class ContractStatus(str, Enum):
    draft = "draft"
    running = "running"
    expired = "expired"
    cancelled = "cancelled"


class AttendanceStatus(str, Enum):
    present = "present"
    late = "late"
    absent = "absent"
    overtime = "overtime"
    missing_checkout = "missing_checkout"


class TimeOffUnit(str, Enum):
    days = "days"
    hours = "hours"


class AllocationStatus(str, Enum):
    draft = "draft"
    to_approve = "to_approve"
    approved = "approved"
    refused = "refused"


class TimeOffRequestStatus(str, Enum):
    draft = "draft"
    to_approve = "to_approve"
    approved = "approved"
    refused = "refused"
    cancelled = "cancelled"


class SalaryRuleCategory(str, Enum):
    basic = "basic"
    allowance = "allowance"
    deduction = "deduction"
    gross = "gross"
    contribution = "contribution"
    net = "net"


class ComputationMethod(str, Enum):
    fixed = "fixed"
    percentage = "percentage"
    formula = "formula"


class PayrunStatus(str, Enum):
    draft = "draft"
    computed = "computed"
    validated = "validated"
    paid = "paid"
    cancelled = "cancelled"


class PayslipWarningType(str, Enum):
    missing_bank_details = "missing_bank_details"
    duplicate_payslip = "duplicate_payslip"
    missing_contract = "missing_contract"
    negative_net = "negative_net"
    overlapping_period = "overlapping_period"
    other = "other"