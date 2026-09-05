"""Shared exception hierarchy + global FastAPI handlers.

Services raise `AppException` subclasses; a single global handler converts
them to the JSON shape  {"detail": ..., "error_code": ...}  (architecture
doc §4.2). Services must NEVER raise raw HTTPException — that keeps them
unit-testable without FastAPI.

The handler also maps low-level DB errors that escape the service layer to
clean HTTP responses instead of leaking stack traces:
- IntegrityError            -> 409 Conflict (duplicate / FK violation backstop)
- SQLAlchemyError (others)  -> 500 Internal Server Error
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

logger = logging.getLogger(__name__)


class AppException(Exception):
    """Base class. Subclasses set status_code + error_code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    error_code: str = "bad_request"

    def __init__(self, detail: str = "Something went wrong."):
        self.detail = detail
        super().__init__(detail)


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ConflictException(AppException):
    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationException(AppException):
    # 422 literal: newer Starlette renamed it HTTP_422_UNPROCESSABLE_CONTENT
    # (older versions only had HTTP_422_UNPROCESSABLE_ENTITY) — using the
    # literal keeps this warning-free across both.
    status_code = 422
    error_code = "validation_error"


class ForbiddenException(AppException):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden"


class UnauthorizedException(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "unauthorized"


def _app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": exc.error_code},
    )


def _integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
    # Backstop: the service layer should translate known constraint violations
    # into friendly ConflictException messages. If one slips through, tell the
    # client it was a conflict rather than a 500.
    logger.warning("Unhandled IntegrityError: %s", exc.orig)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "The operation violates a uniqueness or referential "
            "constraint. Check for duplicates or referenced records.",
            "error_code": "conflict",
        },
    )


def _sqlalchemy_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Unhandled SQLAlchemyError: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal database error.",
            "error_code": "internal_error",
        },
    )


def _validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "error_code": "validation_error",
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers to the FastAPI app (called once from main.py)."""
    app.add_exception_handler(AppException, _app_exception_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, _sqlalchemy_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)