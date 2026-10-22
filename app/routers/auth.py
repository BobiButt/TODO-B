"""Authentication HTTP routes: register, login, OTP, Google OAuth, me, refresh."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import AuthProvider, Role
from app.schemas.user import (
    GoogleAuth,
    OTPResend,
    OTPVerify,
    Token,
    UserLogin,
    UserRead,
    UserRegister,
)
from app.services.auth import (
    authenticate_user,
    create_access_token,
    create_user_from_google,
    generate_otp,
    get_password_hash,
    get_user_by_email,
    get_user_by_google_id,
    send_otp_email,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@router.post(
    "/register",
    response_model=Token,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates an account with email + password and sends a 6-digit OTP for verification.",
)
async def register(
    payload: UserRegister,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Register a new user and send an OTP to their email."""
    existing = await get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    otp_code = generate_otp()
    otp_expires = _utc_now() + timedelta(minutes=settings.otp_expire_minutes)

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        provider=AuthProvider.EMAIL,
        is_verified=False,
        otp_code=otp_code,
        otp_expires_at=otp_expires,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await send_otp_email(payload.email, otp_code, db)

    return Token(
        access_token="",
        token_type="bearer",
        user=UserRead.model_validate(user),
    )


@router.post(
    "/login",
    response_model=Token,
    summary="Log in with email and password",
    description="Verifies credentials and returns a JWT access token if the email is verified.",
)
async def login(
    payload: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Authenticate a user and issue a JWT."""
    user = await authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password, or email not verified.",
        )

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token, token_type="bearer", user=UserRead.model_validate(user))


@router.post(
    "/verify-otp",
    response_model=Token,
    summary="Verify email with OTP code",
    description="Checks the 6-digit OTP code and, if valid, marks the email as verified and issues a JWT.",
)
async def verify_otp(
    payload: OTPVerify,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Verify an OTP code and issue a JWT on success."""
    user = await get_user_by_email(db, payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account with this email.",
        )
    if not user.otp_code or not user.otp_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending OTP for this account.",
        )
    if user.otp_code != payload.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP code.",
        )
    if user.otp_expires_at < _utc_now():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP code has expired.",
        )

    user.is_verified = True
    user.email_verified_at = _utc_now()
    user.otp_code = None
    user.otp_expires_at = None
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token, token_type="bearer", user=UserRead.model_validate(user))


@router.post(
    "/resend-otp",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Resend OTP code",
    description="Sends a new 6-digit OTP to an unverified email address.",
)
async def resend_otp(
    payload: OTPResend,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Resend a new OTP code to the user's email."""
    user = await get_user_by_email(db, payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account with this email.",
        )
    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified.",
        )

    otp_code = generate_otp()
    user.otp_code = otp_code
    user.otp_expires_at = _utc_now() + timedelta(minutes=settings.otp_expire_minutes)
    await db.commit()

    await send_otp_email(payload.email, otp_code, db)


@router.post(
    "/google",
    response_model=Token,
    summary="Sign in with Google OAuth",
    description="Verifies a Google ID token and creates or logs in the user.",
)
async def google_auth(
    payload: GoogleAuth,
    db: AsyncSession = Depends(get_db),
) -> Token:
    """Verify Google ID token and issue a JWT."""
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        idinfo = id_token.verify_oauth2_token(
            payload.credential, google_requests.Request(), settings.google_client_id
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google ID token.",
        )

    google_subject = idinfo.get("sub")
    email = idinfo.get("email")
    name = idinfo.get("name", email.split("@")[0] if email else "User")

    if not google_subject or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token missing required claims.",
        )

    user = await get_user_by_google_id(db, google_subject)
    if not user:
        user = await get_user_by_email(db, email)
        if user:
            # Link existing email account to Google
            user.google_id = google_subject
            user.provider = AuthProvider.GOOGLE
            user.is_verified = True
            user.email_verified_at = _utc_now()
            await db.commit()
            await db.refresh(user)
        else:
            user = await create_user_from_google(
                db, email=email, name=name, google_id=google_subject
            )

    token = create_access_token({"sub": str(user.id)})
    return Token(access_token=token, token_type="bearer", user=UserRead.model_validate(user))


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get current user profile",
    description="Returns the profile of the user identified by the JWT Bearer token.",
)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user's profile."""
    return current_user


@router.post(
    "/refresh",
    response_model=Token,
    summary="Refresh the access token",
    description="Issues a new JWT for the currently authenticated user.",
)
async def refresh_token(
    current_user: User = Depends(get_current_user),
) -> Token:
    """Issue a new access token for the authenticated user."""
    token = create_access_token({"sub": str(current_user.id)})
    return Token(access_token=token, token_type="bearer", user=UserRead.model_validate(current_user))
