# ============================================================================
# FILE:       backend/app/schemas/common.py
# PURPOSE:    Hold the vocabulary shared by the WHOLE API: the Role/Priority/
#             Status enums, the paginated-response envelope, and the standard
#             error shape. One definition = no drift between endpoints.
# HOW IT WORKS: StrEnum members are real strings (they serialize to JSON as
#             "admin", "high", ...) while still validating input: Pydantic
#             rejects any value outside the enum with a 422. The models import
#             the SAME enums for their DB defaults — this module must never
#             import models (that would create a circular import).
# USED BY / DEPENDS ON: imported by models/*, every router, and frontend
#             mirrors these values in types/index.ts.
# KEY CONCEPTS: StrEnum as single source of truth, generic pagination envelope,
#             consistent error shape for clients.
# ============================================================================

"""Shared enums, pagination envelope and error schema (no model imports!)."""

# StrEnum: an enum whose members ARE strings — perfect for JSON APIs and for
# use as SQLAlchemy column defaults (Python 3.11+, so fine on 3.12).
from enum import StrEnum

# Generic (TypeVar) lets Page[T] wrap ANY item schema: Page[TaskRead] etc.
from typing import Generic, TypeVar

# BaseModel is Pydantic v2's base class; Field adds validation metadata.
from pydantic import BaseModel, Field


class Role(StrEnum):
    """Who a request claims to be — the permission tier of a user.

    Members:
        ADMIN: may use /admin/* endpoints and moderate comments.
        USER: may only touch their own tasks and comments.

    NOTE: models/user.py stores these exact strings in users.role, and
    dependencies.get_current_user compares the X-Role header against the DB
    value — so this enum is the contract between header, database and API.
    """

    ADMIN = "admin"
    USER = "user"


class AuthProvider(StrEnum):
    """Authentication provider used to create the account.

    Members:
        EMAIL: email + password registration with OTP verification.
        GOOGLE: Google OAuth single sign-on.
    """

    EMAIL = "email"
    GOOGLE = "google"


class Priority(StrEnum):
    """Task importance levels, ordered from least to most urgent.

    Members: LOW ("low"), MEDIUM ("medium"), HIGH ("high").

    NOTE: the database CHECK constraint ck_tasks_priority allows exactly
    these three strings.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(StrEnum):
    """Task workflow states.

    Members:
        PENDING: not started (default for new tasks).
        IN_PROGRESS: actively being worked on.
        COMPLETED: finished (the toggle endpoint flips between this and PENDING).

    NOTE: ck_tasks_status in the database allows exactly these three strings.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# TypeVar is a placeholder type: Page[TaskRead] makes T = TaskRead everywhere
# inside that instantiation, so `items` is typed as list[TaskRead] not list[Any].
T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Standard paginated-list response envelope.

    Fields:
        items (list[T]): the page's rows (T = the row schema, e.g. TaskRead).
        total (int): how many rows match the filter across ALL pages.
        page (int): current page number, 1-based.
        page_size (int): how many rows per page were requested.
        pages (int): total number of pages (ceil(total / page_size)).

    Example (client side):
        const { items, pages }: Page<Task> = await api.get('/tasks?page=2')
    """

    items: list[T]  # the actual payload rows
    total: int = Field(ge=0, description="Total matching rows across pages")
    page: int = Field(ge=1, description="Current page, 1-based")
    page_size: int = Field(ge=1, le=100, description="Rows per page (max 100)")
    pages: int = Field(ge=0, description="Total page count")


class ErrorDetail(BaseModel):
    """Documented shape of an error response (for OpenAPI readability).

    FastAPI's built-in handlers emit `{"detail": ...}` — this schema describes
    that shape so /docs shows clients what failures look like. Routers raise
    HTTPException(detail=...) which produces exactly this JSON.

    Fields:
        detail (str | list[dict]): human message, or Pydantic's field-level
            error list for 422 validation failures.

    Example:
        {"detail": "Task 42 not found"}
    """

    detail: str | list[dict]
