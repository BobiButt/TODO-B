# ============================================================================
# FILE:       backend/app/schemas/user.py
# PURPOSE:    Define the JSON contract for the /users endpoints: what the API
#             accepts when creating or editing a user, and what it returns.
#             Pydantic schemas are deliberately SEPARATE from SQLAlchemy models
#             (Section 1 of the brief) so a database change never silently
#             changes the public API shape, and vice versa.
# HOW IT WORKS: FastAPI reads each route's annotation (`user: UserCreate`),
#             validates the incoming JSON body against the model, and serializes
#             the returned ORM object through `UserRead`. `from_attributes=True`
#             lets Pydantic copy values off an ORM object's attributes instead of
#             a dict, which is what makes `return orm_user` work directly.
# USED BY / DEPENDS ON: routers/users.py (Phase 3) and seed.py (Phase 3);
#             re-exported by app/schemas/__init__.py.
# KEY CONCEPTS: request vs response schema, field validation (min/max length,
#             EmailStr), computed read-only fields, enum echo.
# ============================================================================

"""Pydantic schemas for user create / update / read."""

# BaseModel is the Pydantic v2 base class every schema inherits from.
from pydantic import BaseModel, EmailStr, Field

# datetime types the read-only timestamp fields of a user row.
from datetime import datetime, timedelta, timezone

# The Role enum comes from common.py, so the API and the DB column (which uses
# the same enum as its default) can never disagree about allowed values.
from app.schemas.common import Role, AuthProvider


class UserBase(BaseModel):
    """Fields shared by the create and update schemas.

    A base class avoids declaring `name`/`email`/`role` twice. The `...`
    (Ellipsis) default on a Field means "required" in Pydantic v2.

    Attributes:
        name (str): display name, 1-80 characters after trimming.
        email (EmailStr): unique address; EmailStr validates the format.
        role (Role): "admin" or "user" — determines admin-panel access.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Display name shown in the UI (1-80 characters).",
        examples=["Ada Lovelace"],
    )
    email: EmailStr = Field(
        ...,
        max_length=255,
        description="Unique email address (validated format).",
        examples=["ada@example.com"],
    )
    role: Role = Field(
        default=Role.USER,
        description="Permission tier: 'admin' or 'user'.",
    )


class UserCreate(UserBase):
    """Request body for POST /users (admin creates a new user).

    Attributes: inherited from UserBase (name, email, role).

    Raises:
        422: when name is blank/too long, the email is malformed, or role is not
            one of the two enum values.
        409: (raised by the router, not here) when the email already exists.

    Example body:
        {"name": "Grace Hopper", "email": "grace@example.com", "role": "user"}
    """


class UserUpdate(BaseModel):
    """Request body for PATCH /users/{id} — every field optional (partial update).

    Why not reuse UserCreate? A PATCH must let the client change only the name,
    so required fields would break it. `None` here means "not supplied, leave
    unchanged" — the router therefore only assigns attributes that are not None.

    Attributes:
        name (str | None): new display name (omit to keep the current one).
        email (EmailStr | None): new unique email (omit to keep).
        role (Role | None): new role (omit to keep).
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="New display name, or omit to leave unchanged.",
    )
    email: EmailStr | None = Field(
        default=None,
        max_length=255,
        description="New email address, or omit to leave unchanged.",
    )
    role: Role | None = Field(
        default=None,
        description="New permission tier, or omit to leave unchanged.",
    )


class UserRead(BaseModel):
    """Response body for every /users endpoint.

    Attributes:
        id (int): database primary key — never writable by a client.
        name (str): display name.
        email (EmailStr): unique address.
        role (Role): current permission tier.
        created_at (datetime): account creation instant (UTC).
        task_count (int): derived helper for the admin users table — computed by
            the router with a grouped COUNT query, not a stored column. Defaults
            to 0 so a response that did not compute it still validates.

    NOTE: from_attributes=True (the v2 name for orm_mode) lets Pydantic read
    these fields straight off a User ORM instance; `task_count` is injected by
    setting the attribute on a transient object or using a serialization dict.
    """

    model_config = {"from_attributes": True}  # allow ORM objects as input

    id: int
    name: str
    email: EmailStr
    role: Role
    created_at: datetime
    task_count: int = Field(
        default=0,
        ge=0,
        description="Number of tasks owned by this user (computed per request).",
    )


# ---------------------------------------------------------------------------
# Authentication schemas
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    """Request body for POST /auth/register.

    Attributes:
        name (str): display name, 1-80 characters.
        email (EmailStr): unique email address.
        password (str): at least 8 characters.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Display name shown in the UI.",
    )
    email: EmailStr = Field(
        ...,
        description="Unique email address. An OTP will be sent here for verification.",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Account password (8-128 characters).",
    )


class UserLogin(BaseModel):
    """Request body for POST /auth/login.

    Attributes:
        email (EmailStr): registered email address.
        password (str): account password.
    """

    email: EmailStr = Field(..., description="Registered email address.")
    password: str = Field(..., description="Account password.")


class Token(BaseModel):
    """JWT token response body.

    Attributes:
        access_token (str): JWT access token to send as `Authorization: Bearer <token>`.
        token_type (str): always "bearer".
        user (UserRead): the authenticated user's profile.
    """

    access_token: str
    token_type: str = "bearer"
    user: UserRead


class OTPVerify(BaseModel):
    """Request body for POST /auth/verify-otp.

    Attributes:
        email (EmailStr): email address that received the OTP.
        code (str): 6-digit verification code.
    """

    email: EmailStr = Field(..., description="Email address that received the OTP.")
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit verification code.",
    )


class GoogleAuth(BaseModel):
    """Request body for POST /auth/google.

    Attributes:
        credential (str): Google ID token (credential) from Sign-In with Google.
    """

    credential: str = Field(..., description="Google ID token credential.")


class OTPResend(BaseModel):
    """Request body for POST /auth/resend-otp.

    Attributes:
        email (EmailStr): email address to resend OTP to.
    """

    email: EmailStr = Field(..., description="Email address to resend OTP to.")
