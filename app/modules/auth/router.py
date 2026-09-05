"""Auth & RBAC endpoints (Eldo's slice).

Routers stay thin: parse request -> call service -> return (arch doc §4.3).
All business rules live in app/modules/auth/service.py.
"""

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.models.auth import User
from app.schemas.auth import (
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserRoleUpdate,
    UserUpdate,
)

from . import service

router = APIRouter()


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="OAuth2 password flow — returns access + refresh tokens",
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """`username` field carries the email (OAuth2 convention)."""
    user = service.authenticate_user(db, form_data.username, form_data.password)
    return TokenResponse(**service.issue_tokens(user))


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest, db: Session = Depends(get_db)
) -> TokenResponse:
    return TokenResponse(**service.refresh_access_token(db, payload.refresh_token))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    """Current user + roles + linked employee. Guards require_roles tests."""
    return current_user


# ---------------------------------------------------------------------------
# Admin-only user management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserOut])
def list_users(
    _: User = Depends(require_roles("ADMIN")),
    db: Session = Depends(get_db),
) -> list[User]:
    return service.list_users(db)


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    payload: UserCreate,
    _: User = Depends(require_roles("ADMIN")),
    db: Session = Depends(get_db),
) -> User:
    service.validate_password_strength(payload.password)
    return service.create_user(db, payload)


@router.patch("/users/{user_id}/roles", response_model=UserOut)
def update_user_roles(
    user_id: int,
    payload: UserRoleUpdate,
    current_user: User = Depends(require_roles("ADMIN")),
    db: Session = Depends(get_db),
) -> User:
    return service.replace_user_roles(db, user_id, payload, current_user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_roles("ADMIN")),
    db: Session = Depends(get_db),
) -> User:
    """Link/unlink an employee or toggle is_active on an existing account
    (how an unlinked EMPLOYEE account gets fixed after creation)."""
    return service.update_user_account(db, user_id, payload, current_user)