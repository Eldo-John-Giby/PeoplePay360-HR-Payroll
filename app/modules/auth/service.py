"""Auth/RBAC service layer (Eldo's slice).

All business rules live here, NOT in the router:
- login verifies credentials + is_active, stamps last_login_at
- create_user enforces email uniqueness + one-account-per-employee (409s)
- replace_user_roles rejects self-elevation (architecture doc §4.7)

Services raise AppException subclasses only — never HTTPException.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.auth import Role, User
from app.schemas.auth import UserCreate, UserRoleUpdate, UserUpdate


# ---------------------------------------------------------------------------
# Login / tokens
# ---------------------------------------------------------------------------

def authenticate_user(db: Session, email: str, password: str) -> User:
    """Verify credentials. Raises 401 on any failure (never reveals which)."""
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles), selectinload(User.employee))
        .where(User.email == email.strip().lower())
    )
    if user is None or not verify_password(password, user.hashed_password):
        raise UnauthorizedException("Incorrect email or password.")
    if not user.is_active:
        raise UnauthorizedException("Account is disabled.")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


def issue_tokens(user: User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
    }


def refresh_access_token(db: Session, refresh_token: str) -> dict[str, str]:
    """Exchange a valid refresh token for a fresh access token pair."""
    from app.core.security import decode_token

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = int(payload["sub"])
    except Exception:
        raise UnauthorizedException("Invalid or expired refresh token.")

    user = db.scalar(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    if user is None:
        raise UnauthorizedException("User not found or account disabled.")
    return issue_tokens(user)


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.scalar(
        select(User)
        .options(selectinload(User.roles), selectinload(User.employee))
        .where(User.id == user_id)
    )
    if user is None:
        raise NotFoundException(f"User {user_id} not found.")
    return user


def _get_roles_by_name(db: Session, role_names: list[str]) -> list[Role]:
    if not role_names:
        return []
    roles = db.scalars(select(Role).where(Role.name.in_(role_names))).all()
    found = {r.name for r in roles}
    missing = set(role_names) - found
    if missing:
        raise NotFoundException(
            f"Unknown role(s): {', '.join(sorted(missing))}."
        )
    return list(roles)


# ---------------------------------------------------------------------------
# User CRUD (Admin only — enforced in the router via require_roles("ADMIN"))
# ---------------------------------------------------------------------------

def create_user(db: Session, payload: UserCreate) -> User:
    email = payload.email.strip().lower()

    # Friendly 409s instead of raw IntegrityError leaks.
    if db.scalar(select(User).where(User.email == email)):
        raise ConflictException(f"A user with email '{email}' already exists.")

    if payload.employee_id is not None:
        from app.models.employee import Employee

        employee = db.get(Employee, payload.employee_id)
        if employee is None:
            raise NotFoundException(
                f"Employee {payload.employee_id} not found."
            )
        linked = db.scalar(
            select(User).where(User.employee_id == payload.employee_id)
        )
        if linked is not None:
            raise ConflictException(
                f"Employee {payload.employee_id} already has a user account "
                f"(user id {linked.id}). One account per employee."
            )

    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        employee_id=payload.employee_id,
        is_active=payload.is_active,
    )
    user.roles = _get_roles_by_name(db, payload.role_names)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def replace_user_roles(
    db: Session, target_user_id: int, payload: UserRoleUpdate, actor: User
) -> User:
    """Replace a user's role set. Rejects self-elevation (arch doc §4.7)."""
    target = get_user_or_404(db, target_user_id)

    new_names = set(payload.role_names)
    old_names = {r.name for r in target.roles}

    if actor.id == target.id and new_names != old_names:
        raise ForbiddenException(
            "You cannot change your own roles (self-elevation is not allowed)."
        )

    target.roles = _get_roles_by_name(db, payload.role_names)
    db.commit()
    db.refresh(target)
    return target


def list_users(db: Session) -> list[User]:
    return list(
        db.scalars(
            select(User).options(
                selectinload(User.roles), selectinload(User.employee)
            )
        ).all()
    )


def update_user_account(
    db: Session, user_id: int, payload: UserUpdate, actor: User
) -> User:
    """Link/unlink an employee and/or toggle is_active on an existing account
    (ADMIN only — enforced in the router).

    This is how "HR must link it" actually happens after creation: e.g. an
    EMPLOYEE account created without an employee profile gets linked here and
    attendance self-service starts working immediately.
    """
    target = get_user_or_404(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return target

    if "employee_id" in changes:
        new_employee_id = changes["employee_id"]
        if new_employee_id is None:
            target.employee_id = None
        else:
            from app.models.employee import Employee

            if db.get(Employee, new_employee_id) is None:
                raise NotFoundException(
                    f"Employee {new_employee_id} not found."
                )
            linked = db.scalar(
                select(User).where(User.employee_id == new_employee_id)
            )
            if linked is not None and linked.id != target.id:
                raise ConflictException(
                    f"Employee {new_employee_id} already has a user account "
                    f"(user id {linked.id}). One account per employee."
                )
            target.employee_id = new_employee_id

    if "is_active" in changes:
        if actor.id == target.id and changes["is_active"] is False:
            raise ForbiddenException(
                "You cannot disable your own account."
            )
        target.is_active = bool(changes["is_active"])

    db.commit()
    db.refresh(target)
    return target


def validate_password_strength(password: str) -> None:
    """Minimal password policy (extend freely)."""
    if len(password) < 8:
        raise ValidationException("Password must be at least 8 characters.")