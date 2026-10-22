# ============================================================================
# FILE:       backend/app/schemas/__init__.py
# PURPOSE:    Make `app/schemas/` a package and re-export the schemas most
#             often needed, so routers can write one import line instead of
#             five.
# HOW IT WORKS: Executes each schema module (registering its classes) and
#             lists the public names in __all__. Kept small on purpose: files
#             that need something specific import the submodule directly.
# USED BY / DEPENDS ON: imported by routers/* and seed.py.
# KEY CONCEPTS: package marker, re-export, Pydantic schemas as API contracts.
# ============================================================================

"""Pydantic v2 request/response schemas (the API's data contracts)."""

# Shared vocabulary + envelope shapes:
from app.schemas.common import (
    ErrorDetail,
    Page,
    Priority,
    Role,
    Status,
)

# User endpoints:
from app.schemas.user import (
    UserCreate,
    UserRead,
    UserUpdate,
    UserRegister,
    UserLogin,
    Token,
    OTPVerify,
    GoogleAuth,
    OTPResend,
)

# Task endpoints (including nested subtask schemas):
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.task import (
    SubtaskCreate,
    SubtaskRead,
    SubtaskUpdate,
    TaskCreate,
    TaskRead,
    TaskReorder,
    TaskUpdate,
)

# Import/export contracts:
from app.schemas.transfer import (
    ImportOptions,
    ImportReport,
    ImportRow,
    ImportRowError,
)

# __all__ documents the public surface of this package.
__all__ = [
    # common
    "ErrorDetail",
    "Page",
    "Priority",
    "Role",
    "Status",
    "AuthProvider",
    # user
    "UserCreate",
    "UserRead",
    "UserUpdate",
    "UserRegister",
    "UserLogin",
    "Token",
    "OTPVerify",
    "GoogleAuth",
    "OTPResend",
    # task
    "SubtaskCreate",
    "SubtaskRead",
    "SubtaskUpdate",
    "TaskCreate",
    "TaskRead",
    "TaskReorder",
    "TaskUpdate",
    # comment
    "CommentCreate",
    "CommentRead",
    "CommentUpdate",
    # transfer
    "ImportOptions",
    "ImportReport",
    "ImportRow",
    "ImportRowError",
]
