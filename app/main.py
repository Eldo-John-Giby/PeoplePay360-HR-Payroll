"""PeoplePay360 — FastAPI entry point.

Written once by Eldo (Hour 0-1) and FROZEN: all five routers are imported
statically so the team never touches this file again. Each module owner
replaces their empty `router = APIRouter()` stub with real endpoints.
"""

from fastapi import FastAPI

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.modules.attendance_timeoff.router import router as attendance_timeoff_router
from app.modules.auth.router import router as auth_router
from app.modules.employees.router import router as employees_router
from app.modules.payroll.dashboard_router import router as dashboard_router
from app.modules.payroll.router import router as payroll_router

app = FastAPI(
    title=settings.APP_NAME,
    description="HR & Payroll platform — FastAPI + PostgreSQL + SQLAlchemy 2.0",
    version="0.1.0",
)

register_exception_handlers(app)

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(employees_router, prefix="/api/v1", tags=["Employees"])
app.include_router(
    attendance_timeoff_router, prefix="/api/v1", tags=["Attendance & Time Off"]
)
app.include_router(payroll_router, prefix="/api/v1/payroll", tags=["Payroll"])
app.include_router(dashboard_router, prefix="/api/v1/dashboard", tags=["Dashboard"])


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok"}