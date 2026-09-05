"""Pydantic v2 request/response models for Auth/RBAC (Eldo's slice)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class EmployeeRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    work_email: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    employee_id: int | None = None
    is_active: bool
    last_login_at: datetime | None = None
    roles: list[RoleOut] = []
    employee: EmployeeRef | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role_names: list[str] = Field(default_factory=list, description="Role names, e.g. ['EMPLOYEE']")
    employee_id: int | None = Field(
        default=None,
        description="Link to an employee. Fails with 409 if that employee already has an account.",
    )
    is_active: bool = True


class UserRoleUpdate(BaseModel):
    role_names: list[str] = Field(description="Full replacement set of role names.")


class UserUpdate(BaseModel):
    """PATCH /auth/users/{id} — partial update of an existing account (ADMIN).

    - `employee_id` absent -> unchanged; `null` -> unlink; a value -> link to
      that employee (one-account-per-employee still enforced, 409).
    - `is_active` absent -> unchanged; a value -> enable/disable login.
    """

    employee_id: int | None = None
    is_active: bool | None = None