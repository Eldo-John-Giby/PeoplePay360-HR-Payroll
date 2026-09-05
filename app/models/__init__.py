"""Model registry — importing this module registers every table on
`Base.metadata`, which is what Alembic autogenerate diffs against.

NOTE: `app.models.views` is intentionally NOT imported here — the SQL views
are created by the initial migration, and registering them as tables would
make autogenerate try to recreate/drop them. Import it explicitly when you
want to query a view through the ORM:
    from app.models.views import TimeOffBalanceView, WorkingScheduleHoursView
"""

from app.models.attendance import Attendance
from app.models.auth import Permission, Role, RolePermission, User, UserRole
from app.models.employee import Contract, Employee, EmployeeBankDetail
from app.models.organization import (
    Company,
    Department,
    JobPosition,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.payroll import (
    Payrun,
    PayrunEmployee,
    Payslip,
    PayslipLine,
    PayslipWarning,
    SalaryRule,
    SalaryStructure,
    SalaryStructureRule,
)
from app.models.timeoff import TimeOffAllocation, TimeOffRequest, TimeOffType

__all__ = [
    "Company",
    "Department",
    "JobPosition",
    "WorkingSchedule",
    "WorkingScheduleLine",
    "Role",
    "Permission",
    "RolePermission",
    "User",
    "UserRole",
    "Employee",
    "EmployeeBankDetail",
    "Contract",
    "Attendance",
    "TimeOffType",
    "TimeOffAllocation",
    "TimeOffRequest",
    "SalaryRule",
    "SalaryStructure",
    "SalaryStructureRule",
    "Payrun",
    "PayrunEmployee",
    "Payslip",
    "PayslipLine",
    "PayslipWarning",
]