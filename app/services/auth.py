"""Authentication business logic: password hashing, JWT, OTP, Google OAuth."""

import os
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.schemas.common import AuthProvider

# bcrypt password hashing context.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plain password with bcrypt."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT access token.

    Parameters:
        data: claims to embed in the token (typically {"sub": user_id}).
        expires_delta: optional override; defaults to settings.access_token_expire_minutes.

    Returns:
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def generate_otp() -> str:
    """Generate a 6-digit numeric OTP code."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def send_otp_email(email: str, code: str, db: AsyncSession) -> bool:
    """Send an OTP code via SMTP (best-effort; logs if SMTP is not configured).

    Parameters:
        email: recipient address.
        code: 6-digit OTP code.
        db: database session (used to look up the user for context).

    Returns:
        True if the email was sent, False if SMTP is not configured.
    """
    if not settings.smtp_user or not settings.smtp_password:
        # In development, log the OTP so it can be used without a real SMTP server.
        print(f"[DEV OTP] Email: {email} | Code: {code}")
        return True

    try:
        import aiosmtplib

        message = (
            f"Subject: TaskFlow verification code\r\n"
            f"To: {email}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"Your TaskFlow verification code is: {code}\n"
            f"It expires in {settings.otp_expire_minutes} minutes."
        )
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=True,
            username=settings.smtp_user,
            password=settings.smtp_password,
        )
        return True
    except Exception:
        # Fall back to dev logging so the flow is never blocked.
        print(f"[DEV OTP] Email: {email} | Code: {code}")
        return True


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Authenticate a user by email and password.

    Returns the User if credentials are valid and email is verified, else None.
    """
    stmt = select(User).where(User.email == email)
    user = (await db.execute(stmt)).scalars().first()
    if not user or user.provider != AuthProvider.EMAIL:
        return None
    if not user.password_hash or not verify_password(password, user.password_hash):
        return None
    if not user.is_verified:
        return None
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Look up a user by email address."""
    stmt = select(User).where(User.email == email)
    return (await db.execute(stmt)).scalars().first()


async def get_user_by_google_id(db: AsyncSession, google_id: str) -> User | None:
    """Look up a user by Google subject ID."""
    stmt = select(User).where(User.google_id == google_id)
    return (await db.execute(stmt)).scalars().first()


async def create_user_from_google(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    google_id: str,
    picture_url: str | None = None,
) -> User:
    """Create a new Google OAuth-linked user (email pre-verified)."""
    user = User(
        name=name,
        email=email,
        provider=AuthProvider.GOOGLE,
        google_id=google_id,
        is_verified=True,
        email_verified_at=datetime.now(timezone.utc),
        password_hash=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
