"""Demo seed data — enough for a 5-minute walkthrough (schema doc §6).

IDEMPOTENT: if the OXP company row already exists it skips everything, so it
is safe to run on every `docker compose up` boot and to re-run manually:

    python -m app.seed.seed_data

Highlights for the demo:
- 1 company, 5 departments, 10 job positions, 2 working schedules
- 18 employees (wireframe names), 7 user accounts covering all 5 roles
- Contract history for 3 employees; Kiran has NO contract (missing_contract)
- Sneha has NO bank details (missing_bank_details)
- 3 weeks of attendance incl. late / overtime / missing_checkout / correction
- Time-off allocations + approved / to_approve / refused requests
- "Regular Salary" structure: BASIC(% of CONTRACT_WAGE) -> HRA -> MEAL ->
  PF_DEDUCTION -> GROSS -> NET
- 1 PAID historical payrun (August 2026) + 1 DRAFT payrun (September 2026)
  ready for the Compute step to surface warnings
"""

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    Attendance,
    Company,
    Contract,
    Department,
    Employee,
    EmployeeBankDetail,
    JobPosition,
    Payrun,
    PayrunEmployee,
    Payslip,
    PayslipLine,
    PayslipWarning,
    Permission,
    Role,
    SalaryRule,
    SalaryStructure,
    SalaryStructureRule,
    TimeOffAllocation,
    TimeOffRequest,
    TimeOffType,
    User,
    WorkingSchedule,
    WorkingScheduleLine,
)
from app.models.enums import (
    AllocationStatus,
    AttendanceStatus,
    ComputationMethod,
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    PayslipWarningType,
    PayrunStatus,
    SalaryRuleCategory,
    ScheduleType,
    TimeOffRequestStatus,
    TimeOffUnit,
)

COMPANY_NAME = "OXP Pvt Ltd"
DEFAULT_PASSWORD = "Password@123"  # every seeded account

IST = timezone(timedelta(hours=5, minutes=30))
rng = random.Random(42)  # deterministic demo data

# ---------------------------------------------------------------------------
# Master data definitions
# ---------------------------------------------------------------------------

DEPARTMENTS = [
    "Engineering",
    "Human Resources",
    "Sales & Marketing",
    "Finance",
    "Operations",
]

JOB_POSITIONS = {
    "Engineering": ["Software Engineer", "Senior Software Engineer"],
    "Human Resources": ["HR Executive", "HR Manager"],
    "Sales & Marketing": ["Sales Executive", "Sales Manager"],
    "Finance": ["Accountant", "Finance Manager"],
    "Operations": ["Operations Associate", "Operations Manager"],
}

# name, dept, position, email, phone, manager, type, status, doj, wage
EMPLOYEES = [
    ("John Dsouza", "Engineering", "Senior Software Engineer", "john.dsouza@oxp.com", "+91 98200 11001", None, "full_time", "active", date(2022, 3, 14), Decimal("120000.00")),
    ("Aarav Mehta", "Engineering", "Software Engineer", "aarav.mehta@oxp.com", "+91 98200 11002", "John Dsouza", "full_time", "active", date(2023, 6, 1), Decimal("85000.00")),
    ("Ravi Sharma", "Engineering", "Software Engineer", "ravi.sharma@oxp.com", "+91 98200 11003", "John Dsouza", "full_time", "active", date(2024, 1, 15), Decimal("70000.00")),
    ("Sneha Iyer", "Engineering", "Software Engineer", "sneha.iyer@oxp.com", "+91 98200 11004", "John Dsouza", "full_time", "active", date(2023, 9, 11), Decimal("75000.00")),
    ("Pooja Reddy", "Engineering", "Software Engineer", "pooja.reddy@oxp.com", "+91 98200 11005", "John Dsouza", "full_time", "active", date(2024, 7, 22), Decimal("65000.00")),
    ("Karan Malhotra", "Engineering", "Senior Software Engineer", "karan.malhotra@oxp.com", "+91 98200 11006", "John Dsouza", "full_time", "active", date(2021, 11, 8), Decimal("135000.00")),
    ("Kiran Joshi", "Engineering", "Software Engineer", "kiran.joshi@oxp.com", "+91 98200 11007", "John Dsouza", "intern", "active", date(2026, 6, 1), Decimal("15000.00")),
    ("Divya Nair", "Human Resources", "HR Manager", "divya.nair@oxp.com", "+91 98200 11008", None, "full_time", "active", date(2021, 4, 19), Decimal("95000.00")),
    ("Priya Singh", "Human Resources", "HR Executive", "priya.singh@oxp.com", "+91 98200 11009", "Divya Nair", "full_time", "active", date(2023, 2, 6), Decimal("60000.00")),
    ("Meera Pillai", "Human Resources", "HR Executive", "meera.pillai@oxp.com", "+91 98200 11010", "Divya Nair", "full_time", "inactive", date(2020, 8, 3), Decimal("58000.00")),
    ("Vikram Rao", "Sales & Marketing", "Sales Manager", "vikram.rao@oxp.com", "+91 98200 11011", None, "full_time", "active", date(2022, 7, 25), Decimal("110000.00")),
    ("Sara Khan", "Sales & Marketing", "Sales Executive", "sara.khan@oxp.com", "+91 98200 11012", "Vikram Rao", "full_time", "active", date(2024, 3, 18), Decimal("55000.00")),
    ("Arjun Nambiar", "Sales & Marketing", "Sales Executive", "arjun.nambiar@oxp.com", "+91 98200 11013", "Vikram Rao", "part_time", "active", date(2025, 1, 13), Decimal("30000.00")),
    ("Ananya Das", "Finance", "Finance Manager", "ananya.das@oxp.com", "+91 98200 11014", None, "full_time", "active", date(2022, 1, 10), Decimal("130000.00")),
    ("Neha Patel", "Finance", "Accountant", "neha.patel@oxp.com", "+91 98200 11015", "Ananya Das", "full_time", "active", date(2023, 5, 2), Decimal("65000.00")),
    ("Amit Verma", "Operations", "Operations Manager", "amit.verma@oxp.com", "+91 98200 11016", None, "full_time", "active", date(2021, 9, 27), Decimal("90000.00")),
    ("Rahul Gupta", "Operations", "Operations Associate", "rahul.gupta@oxp.com", "+91 98200 11017", "Amit Verma", "contract", "active", date(2025, 6, 16), Decimal("40000.00")),
    ("Nikhil Saxena", "Operations", "Operations Associate", "nikhil.saxena@oxp.com", "+91 98200 11018", "Amit Verma", "full_time", "terminated", date(2023, 10, 9), Decimal("48000.00")),
]

# Contract history: employee -> [(wage, start, end)] EXPIRED contracts.
# The running contract is created from the join date (open-ended).
CONTRACT_HISTORY = {
    "Aarav Mehta": [(Decimal("65000.00"), date(2023, 6, 1), date(2024, 5, 31))],
    "Priya Singh": [(Decimal("50000.00"), date(2023, 2, 6), date(2024, 1, 31))],
    "Karan Malhotra": [(Decimal("110000.00"), date(2021, 11, 8), date(2022, 10, 31))],
}
# Terminated/inactive staff: only an expired contract (no running).
ONLY_EXPIRED = {
    "Nikhil Saxena": (Decimal("48000.00"), date(2023, 10, 9), date(2026, 6, 30)),
    "Meera Pillai": (Decimal("58000.00"), date(2020, 8, 3), date(2025, 12, 31)),
}
# Employee with NO contract at all (missing_contract warning demo).
NO_CONTRACT = {"Kiran Joshi"}
# Employee with NO bank details (missing_bank_details warning demo).
NO_BANK_DETAILS = {"Sneha Iyer"}

# user email, role names, linked employee name (None = admin-only account)
USERS = [
    ("admin@oxp.com", ["ADMIN"], None),
    ("divya.nair@oxp.com", ["HR_MANAGER"], "Divya Nair"),
    ("priya.singh@oxp.com", ["HR_PAYROLL_MANAGER"], "Priya Singh"),
    ("neha.patel@oxp.com", ["HR_PAYROLL_USER"], "Neha Patel"),
    ("john.dsouza@oxp.com", ["EMPLOYEE"], "John Dsouza"),
    ("aarav.mehta@oxp.com", ["EMPLOYEE"], "Aarav Mehta"),
    ("sara.khan@oxp.com", ["EMPLOYEE"], "Sara Khan"),
]

ROLE_DESCRIPTIONS = {
    "EMPLOYEE": "Read own records; create own attendance + time-off requests",
    "HR_MANAGER": "Full CRUD on employees/attendance/contracts/time off; no payroll",
    "HR_PAYROLL_USER": "HR_MANAGER + create/update payruns & payslips; read-only rules",
    "HR_PAYROLL_MANAGER": "HR_PAYROLL_USER + full CRUD on payruns, payslips, structures, rules",
    "ADMIN": "Full access to everything; user & role management",
}

# permission code -> roles that hold it
PERMISSIONS = {
    "employee.read": ["EMPLOYEE", "HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "employee.write": ["HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "contract.write": ["HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "attendance.write": ["HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "timeoff.approve": ["HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "payrun.write": ["HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "payslip.write": ["HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "salary_rule.read": ["HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "salary_rule.write": ["HR_PAYROLL_MANAGER", "ADMIN"],
    "dashboard.read": ["EMPLOYEE", "HR_MANAGER", "HR_PAYROLL_USER", "HR_PAYROLL_MANAGER", "ADMIN"],
    "user.manage": ["ADMIN"],
}

SALARY_RULES = [
    # code, name, category, method, amount, percentage, base_code, formula, sequence
    ("BASIC", "Basic Salary", SalaryRuleCategory.basic, ComputationMethod.percentage, None, Decimal("100.00"), "CONTRACT_WAGE", None, 10),
    ("HRA", "House Rent Allowance", SalaryRuleCategory.allowance, ComputationMethod.percentage, None, Decimal("40.00"), "BASIC", None, 20),
    ("MEAL_ALLOWANCE", "Meal Allowance", SalaryRuleCategory.allowance, ComputationMethod.fixed, Decimal("2200.00"), None, None, None, 30),
    ("PF_DEDUCTION", "Provident Fund Deduction", SalaryRuleCategory.deduction, ComputationMethod.percentage, None, Decimal("12.00"), "BASIC", None, 40),
    ("GROSS", "Gross Salary", SalaryRuleCategory.gross, ComputationMethod.formula, None, None, None, "BASIC + HRA + MEAL_ALLOWANCE", 50),
    ("NET", "Net Salary", SalaryRuleCategory.net, ComputationMethod.formula, None, None, None, "GROSS - PF_DEDUCTION", 60),
]

TIME_OFF_TYPES = [
    # name, unit, requires_allocation, requires_approval, affects_payroll
    ("Paid Time Off", TimeOffUnit.days, True, True, True),
    ("Sick Leave", TimeOffUnit.days, True, True, False),
    ("Unpaid Leave", TimeOffUnit.days, False, True, False),
    ("Work From Home", TimeOffUnit.hours, True, True, False),
]

SCHEDULE_HOURS = {ScheduleType.full_time: (time(9, 0), time(18, 0), 60),
                  ScheduleType.part_time: (time(9, 0), time(13, 0), 0)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weekdays_between(start: date, end: date) -> list[date]:
    return [d for i in range((end - start).days + 1)
            if (d := start + timedelta(days=i)).weekday() < 5]


def _compute_payslip_amounts(wage: Decimal) -> dict[str, Decimal]:
    basic = wage
    hra = (basic * Decimal("0.40")).quantize(Decimal("0.01"))
    meal = Decimal("2200.00")
    pf = (basic * Decimal("0.12")).quantize(Decimal("0.01"))
    gross = (basic + hra + meal).quantize(Decimal("0.01"))
    net = (gross - pf).quantize(Decimal("0.01"))
    return {"BASIC": basic, "HRA": hra, "MEAL_ALLOWANCE": meal,
            "PF_DEDUCTION": pf, "GROSS": gross, "NET": net}


def _emp_wage(emp_name: str) -> Decimal:
    for row in EMPLOYEES:
        if row[0] == emp_name:
            return row[9]
    raise KeyError(emp_name)


def _emp_dept_name(emp: Employee, departments: dict[str, Department]) -> str:
    for dept_name, dept in departments.items():
        if dept.id == emp.department_id:
            return dept_name
    raise KeyError(emp.department_id)


def _add_time(t: time, minutes: int) -> time:
    return (datetime.combine(date(2026, 1, 1), t) + timedelta(minutes=minutes)).time()


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed(db: Session) -> None:
    if db.scalar(select(Company).where(Company.name == COMPANY_NAME)):
        print(f"[seed] '{COMPANY_NAME}' already present — skipping (idempotent).")
        return

    print("[seed] Seeding PeoplePay360 demo data...")

    # -- Company, departments, job positions -------------------------------
    company = Company(name=COMPANY_NAME, is_active=True)
    db.add(company)
    db.flush()

    departments: dict[str, Department] = {}
    for name in DEPARTMENTS:
        dept = Department(name=name, company_id=company.id, is_active=True)
        db.add(dept)
        departments[name] = dept
    db.flush()

    positions: dict[tuple[str, str], JobPosition] = {}
    for dept_name, titles in JOB_POSITIONS.items():
        for title in titles:
            pos = JobPosition(
                title=title, department_id=departments[dept_name].id, is_active=True
            )
            db.add(pos)
            positions[(dept_name, title)] = pos
    db.flush()

    # -- Working schedules -------------------------------------------------
    full_time = WorkingSchedule(
        name="Full-Time 40h", schedule_type=ScheduleType.full_time,
        company_id=company.id, is_active=True,
    )
    part_time = WorkingSchedule(
        name="Part-Time 20h", schedule_type=ScheduleType.part_time,
        company_id=company.id, is_active=True,
    )
    db.add_all([full_time, part_time])
    db.flush()
    for dow, (start, end, break_min) in enumerate([SCHEDULE_HOURS[ScheduleType.full_time]] * 5):
        db.add(WorkingScheduleLine(
            working_schedule_id=full_time.id, day_of_week=dow,
            start_time=start, end_time=end, break_minutes=break_min,
        ))
    for dow, (start, end, break_min) in enumerate([SCHEDULE_HOURS[ScheduleType.part_time]] * 5):
        db.add(WorkingScheduleLine(
            working_schedule_id=part_time.id, day_of_week=dow,
            start_time=start, end_time=end, break_minutes=break_min,
        ))
    db.flush()

    # -- Salary structure + rules ------------------------------------------
    structure = SalaryStructure(
        name="Regular Salary", code="REGULAR", company_id=company.id, is_active=True
    )
    db.add(structure)
    db.flush()
    rules: dict[str, SalaryRule] = {}
    for (code, name, category, method, amount, pct, base, formula, seq) in SALARY_RULES:
        rule = SalaryRule(
            code=code, name=name, category=category, computation_method=method,
            amount=amount, percentage=pct, percentage_base_code=base,
            formula=formula, default_sequence=seq, is_active=True,
        )
        db.add(rule)
        rules[code] = rule
    db.flush()
    for (code, *_rest, seq) in SALARY_RULES:
        db.add(SalaryStructureRule(
            salary_structure_id=structure.id,
            salary_rule_id=rules[code].id,
            sequence=seq,
        ))
    db.flush()

    # -- Roles + permissions ------------------------------------------------
    roles: dict[str, Role] = {}
    for name, desc in ROLE_DESCRIPTIONS.items():
        role = Role(name=name, description=desc)
        db.add(role)
        roles[name] = role
    db.flush()
    permissions: dict[str, Permission] = {}
    for code in PERMISSIONS:
        perm = Permission(code=code, description=f"Can perform {code}")
        db.add(perm)
        permissions[code] = perm
    db.flush()
    for code, holder_roles in PERMISSIONS.items():
        for role_name in holder_roles:
            roles[role_name].permissions.append(permissions[code])

    # -- Employees -----------------------------------------------------------
    employees: dict[str, Employee] = {}
    for (name, dept, pos_title, email, phone, manager_name, etype,
         status, doj, wage) in EMPLOYEES:
        emp = Employee(
            full_name=name, work_email=email, phone=phone,
            department_id=departments[dept].id,
            job_position_id=positions[(dept, pos_title)].id,
            working_schedule_id=(
                part_time.id if etype == "part_time" else full_time.id
            ),
            employee_type=EmployeeType(etype),
            status=EmployeeStatus(status),
            date_of_joining=doj,
            work_location="Mumbai (Hybrid)" if dept in ("Engineering", "Operations") else "Pune (Onsite)",
            company_id=company.id,
        )
        db.add(emp)
        employees[name] = emp
    db.flush()
    # Wire up managers (second pass so manager rows exist).
    for (name, dept, pos_title, email, phone, manager_name, etype,
         status, doj, wage) in EMPLOYEES:
        if manager_name:
            employees[name].manager = employees[manager_name]
    db.flush()

    # -- Contracts ------------------------------------------------------------
    contract_no = 0

    def add_contract(emp: Employee, wage: Decimal, start: date,
                     end: date | None, status: ContractStatus) -> Contract:
        nonlocal contract_no
        contract_no += 1
        c = Contract(
            contract_number=f"CON/{start.year}/{contract_no:04d}",
            employee_id=emp.id,
            department_id=emp.department_id,
            job_position_id=emp.job_position_id,
            working_schedule_id=emp.working_schedule_id,
            salary_structure_id=structure.id,
            wage_monthly=wage,
            start_date=start,
            end_date=end,
            status=status,
        )
        db.add(c)
        return c

    for emp_name, emp in employees.items():
        if emp_name in NO_CONTRACT:
            continue
        if emp_name in CONTRACT_HISTORY:
            for (wage, start, end) in CONTRACT_HISTORY[emp_name]:
                add_contract(emp, wage, start, end, ContractStatus.expired)
        elif emp_name in ONLY_EXPIRED:
            wage, start, end = ONLY_EXPIRED[emp_name]
            add_contract(emp, wage, start, end, ContractStatus.expired)
            continue
        add_contract(emp, _emp_wage(emp_name), emp.date_of_joining, None,
                     ContractStatus.running)
    db.flush()

    # -- Bank details ---------------------------------------------------------
    for emp_name, emp in employees.items():
        if emp_name in NO_BANK_DETAILS:
            continue
        db.add(EmployeeBankDetail(
            employee_id=emp.id,
            account_holder_name=emp.full_name,
            bank_name="HDFC Bank",
            account_number=str(rng.randrange(10**10, 10**11)),
            ifsc_or_swift="HDFC0001234",
        ))
    db.flush()

    # -- Users -----------------------------------------------------------------
    users: dict[str, User] = {}
    for (email, role_names, emp_name) in USERS:
        user = User(
            email=email,
            hashed_password=hash_password(DEFAULT_PASSWORD),
            employee_id=employees[emp_name].id if emp_name else None,
            is_active=True,
        )
        user.roles = [roles[r] for r in role_names]
        db.add(user)
        users[email] = user
    db.flush()

    # -- Time off types + allocations + requests --------------------------------
    tot: dict[str, TimeOffType] = {}
    for (name, unit, req_alloc, req_approval, affects_payroll) in TIME_OFF_TYPES:
        t = TimeOffType(
            name=name, unit=unit, requires_allocation=req_alloc,
            requires_approval=req_approval, affects_payroll=affects_payroll,
            company_id=company.id, is_active=True,
        )
        db.add(t)
        tot[name] = t
    db.flush()

    approvers = {
        "Engineering": employees["John Dsouza"],
        "Human Resources": employees["Divya Nair"],
        "Sales & Marketing": employees["Vikram Rao"],
        "Finance": employees["Ananya Das"],
        "Operations": employees["Amit Verma"],
    }

    for emp_name, emp in employees.items():
        if emp.status == EmployeeStatus.terminated or emp_name in NO_CONTRACT:
            continue  # terminated staff and no-contract interns get no allocations
        dept = _emp_dept_name(emp, departments)
        is_part_time = emp.employee_type == EmployeeType.part_time
        for (type_name, amount) in [
            ("Paid Time Off", Decimal("10.00") if is_part_time else Decimal("18.00")),
            ("Sick Leave", Decimal("10.00")),
            ("Work From Home", Decimal("20.00") if is_part_time else Decimal("40.00")),
        ]:
            db.add(TimeOffAllocation(
                employee_id=emp.id, time_off_type_id=tot[type_name].id,
                allocated_amount=amount,
                valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
                status=AllocationStatus.approved, approver_id=approvers[dept].id,
            ))
    # A couple of in-flight allocations + one refused for demo variety.
    db.add(TimeOffAllocation(
        employee_id=employees["Priya Singh"].id,
        time_off_type_id=tot["Paid Time Off"].id,
        allocated_amount=Decimal("5.00"),
        valid_from=date(2026, 9, 1), valid_to=None,
        status=AllocationStatus.to_approve,
        approver_id=employees["Divya Nair"].id,
    ))
    db.add(TimeOffAllocation(
        employee_id=employees["Rahul Gupta"].id,
        time_off_type_id=tot["Work From Home"].id,
        allocated_amount=Decimal("16.00"),
        valid_from=date(2026, 8, 1), valid_to=date(2026, 8, 31),
        status=AllocationStatus.refused,
        approver_id=employees["Amit Verma"].id,
    ))
    db.flush()

    # Requests: approved (past), to_approve (future), refused, cancelled.
    def add_request(emp_name: str, type_name: str, d_from: date, d_to: date,
                    status: TimeOffRequestStatus, reason: str) -> None:
        emp = employees[emp_name]
        dept = _emp_dept_name(emp, departments)
        unit = tot[type_name].unit
        duration = (Decimal("8.00") if unit == TimeOffUnit.hours
                    else Decimal(str((d_to - d_from).days + 1)))
        db.add(TimeOffRequest(
            employee_id=emp.id, time_off_type_id=tot[type_name].id,
            date_from=d_from, date_to=d_to, duration=duration, status=status,
            approver_id=approvers[dept].id, reason=reason,
        ))

    add_request("Aarav Mehta", "Paid Time Off", date(2026, 8, 13), date(2026, 8, 14),
                TimeOffRequestStatus.approved, "Family trip to Goa")
    add_request("Sara Khan", "Sick Leave", date(2026, 8, 20), date(2026, 8, 20),
                TimeOffRequestStatus.approved, "Viral fever")
    add_request("Ravi Sharma", "Work From Home", date(2026, 8, 27), date(2026, 8, 27),
                TimeOffRequestStatus.approved, "Plumber at home")
    add_request("Priya Singh", "Paid Time Off", date(2026, 9, 21), date(2026, 9, 22),
                TimeOffRequestStatus.to_approve, "Weekend wedding")
    add_request("Kiran Joshi", "Paid Time Off", date(2026, 9, 15), date(2026, 9, 15),
                TimeOffRequestStatus.to_approve, "Personal work")
    add_request("Amit Verma", "Work From Home", date(2026, 8, 24), date(2026, 8, 24),
                TimeOffRequestStatus.refused, "Needs to be on floor for audit")
    add_request("Divya Nair", "Paid Time Off", date(2026, 8, 5), date(2026, 8, 5),
                TimeOffRequestStatus.cancelled, "Cancelled — project deadline")
    db.flush()

    # -- Attendance -------------------------------------------------------------
    days = _weekdays_between(date(2026, 8, 10), date(2026, 9, 4))

    for emp_name, emp in employees.items():
        if emp.status != EmployeeStatus.active:
            continue
        sched_type = (ScheduleType.part_time if emp.employee_type == EmployeeType.part_time
                      else ScheduleType.full_time)
        s_start, s_end, break_min = SCHEDULE_HOURS[sched_type]
        scheduled_hours = Decimal("4.00") if sched_type == ScheduleType.part_time else Decimal("8.00")

        for d in days:
            # Skip ~6% of days (absence is represented by a missing record).
            if rng.random() < 0.06:
                continue

            check_in = datetime.combine(d, s_start, tzinfo=IST) + timedelta(
                minutes=rng.randint(0, 20)
            )
            check_out = datetime.combine(d, s_end, tzinfo=IST) + timedelta(
                minutes=rng.randint(0, 40)
            )

            # Forced demo scenarios (one each).
            if emp_name == "Sara Khan" and d == date(2026, 8, 18):
                check_in = datetime.combine(d, time(9, 42), tzinfo=IST)  # late
            elif emp_name == "Aarav Mehta" and d == date(2026, 8, 21):
                check_out = datetime.combine(d, time(19, 30), tzinfo=IST)  # overtime
            elif emp_name == "Ravi Sharma" and d == date(2026, 8, 19):
                check_out = None  # missing_checkout

            if check_out is None:
                status = AttendanceStatus.missing_checkout
                worked = None
            else:
                worked = Decimal(str(round(
                    (check_out - check_in).total_seconds() / 3600 - break_min / 60, 2
                )))
                if check_in.time() > _add_time(s_start, 15):
                    status = AttendanceStatus.late
                elif worked > scheduled_hours + Decimal("0.50"):
                    status = AttendanceStatus.overtime
                else:
                    status = AttendanceStatus.present

            # Priya's late morning was corrected by HR (manual correction demo).
            if emp_name == "Priya Singh" and d == date(2026, 8, 25):
                check_in = datetime.combine(d, time(9, 35), tzinfo=IST)
                worked = Decimal(str(round(
                    (check_out - check_in).total_seconds() / 3600 - break_min / 60, 2
                )))
                db.add(Attendance(
                    employee_id=emp.id, check_in=check_in, check_out=check_out,
                    worked_hours=worked, status=AttendanceStatus.present,
                    is_manual_correction=True,
                    corrected_by_user_id=users["divya.nair@oxp.com"].id,
                    notes="Corrected by HR: traffic delay, manager approved.",
                ))
                continue

            db.add(Attendance(
                employee_id=emp.id, check_in=check_in, check_out=check_out,
                worked_hours=worked, status=status,
                is_manual_correction=False, corrected_by_user_id=None, notes=None,
            ))
    db.flush()

    # -- Payruns + payslips -----------------------------------------------------
    payroll_user = users["neha.patel@oxp.com"]
    payable = [emp for name, emp in employees.items()
               if name not in NO_CONTRACT and emp.status == EmployeeStatus.active
               and any(c.status == ContractStatus.running for c in emp.contracts)]

    # 1) Historical PAID payrun — August 2026.
    aug = Payrun(
        name="Payrun — August 2026",
        salary_structure_id=structure.id,
        period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
        status=PayrunStatus.paid, created_by_user_id=payroll_user.id,
    )
    db.add(aug)
    db.flush()
    for emp in payable:
        running = next(c for c in emp.contracts if c.status == ContractStatus.running)
        amts = _compute_payslip_amounts(running.wage_monthly)
        slip = Payslip(
            payrun_id=aug.id, employee_id=emp.id, contract_id=running.id,
            period_start=aug.period_start, period_end=aug.period_end,
            worked_days=Decimal("21.00"),
            gross_salary=amts["GROSS"], net_salary=amts["NET"],
            status=PayrunStatus.paid,
        )
        db.add(slip)
        db.flush()
        for (code, _name, _cat, _method, _amt, _pct, _base, _formula, seq) in SALARY_RULES:
            db.add(PayslipLine(
                payslip_id=slip.id, salary_rule_id=rules[code].id, sequence=seq,
                code=code, name=rules[code].name, category=rules[code].category,
                amount=amts[code],
            ))
        if emp.full_name in NO_BANK_DETAILS:
            db.add(PayslipWarning(
                payslip_id=slip.id,
                warning_type=PayslipWarningType.missing_bank_details,
                message="No bank details on file — payout will be blocked until added.",
            ))
        db.add(PayrunEmployee(payrun_id=aug.id, employee_id=emp.id))
    db.flush()

    # 2) DRAFT payrun — September 2026 (wizard selection done, NOT computed).
    sep = Payrun(
        name="Payrun — September 2026",
        salary_structure_id=structure.id,
        period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
        status=PayrunStatus.draft, created_by_user_id=payroll_user.id,
    )
    db.add(sep)
    db.flush()
    # Include everyone payable + Kiran (no contract) so Compute surfaces the
    # missing_contract / missing_bank_details warnings during the demo.
    for emp in payable + [employees["Kiran Joshi"]]:
        db.add(PayrunEmployee(payrun_id=sep.id, employee_id=emp.id))

    db.commit()
    print(f"[seed] Done. {len(employees)} employees, {len(users)} users, "
          f"{len(payable)} payable. Demo logins (password '{DEFAULT_PASSWORD}'):")
    for email, _r, _e in USERS:
        print(f"        {email}")


def main() -> None:
    with SessionLocal() as db:
        seed(db)


if __name__ == "__main__":
    main()