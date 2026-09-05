"""Shared FastAPI dependencies — the import surface Ameen/Ambuj/Steve use.

- `get_current_user`   : decodes the JWT, loads the user (+ roles + linked
                         employee) or raises 401. Guards every protected route.
- `require_roles(...)` : dependency factory — OR across the user's roles.
- `require_permission(...)`: dependency factory — OR across the permissions
                         granted via role_permissions (stricter than roles
                         when the caller wants fine-grained checks).

Usage in a router:

    @router.get("/employees")
    def list_employees(
        current_user: User = Depends(require_roles("HR_MANAGER", "ADMIN")),
        db: Session = Depends(get_db),
    ):
        ...
"""

import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_token
from app.models.auth import User

# tokenUrl matches POST /api/v1/auth/login (OAuth2 password flow).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode the access token, load the user with roles, enforce is_active."""
    try:
        payload = decode_token(token, expected_type="access")
        user_id = int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError, ValueError):
        raise UnauthorizedException("Invalid or expired token.")

    user = db.scalar(
        select(User)
        .options(selectinload(User.roles), selectinload(User.employee))
        .where(User.id == user_id)
    )
    if user is None or not user.is_active:
        raise UnauthorizedException("User not found or account disabled.")
    return user


def require_roles(*roles: str):
    """Dependency factory: allow if the user holds ANY of the given roles.

    Example: Depends(require_roles("HR_MANAGER", "ADMIN"))
    """

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        user_role_names = {role.name for role in current_user.roles}
        if not user_role_names.intersection(roles):
            raise ForbiddenException(
                f"Requires one of the roles: {', '.join(roles)}."
            )
        return current_user

    return _checker


def require_permission(*codes: str):
    """Dependency factory: allow if the user's roles grant ANY permission code.

    Example: Depends(require_permission("payrun.write", "user.manage"))
    """

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        granted = {
            perm.code
            for role in current_user.roles
            for perm in role.permissions
        }
        if not granted.intersection(codes):
            raise ForbiddenException(
                f"Requires one of the permissions: {', '.join(codes)}."
            )
        return current_user

    return _checker
