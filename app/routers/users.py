# ============================================================================
# FILE:       backend/app/routers/users.py
# PURPOSE:    HTTP route handlers for user management (/api/v1/users).
#             Provides list, read, create, update, and cascade delete endpoints.
# HOW IT WORKS: Validates incoming request payloads via Pydantic schemas.
#             Uses SQLAlchemy AsyncSession to query and persist User entities.
#             Enforces role permissions via dependencies and records audit logs.
# USED BY / DEPENDS ON: app/main.py, app/dependencies.py, app/services/activity.py.
# KEY CONCEPTS: RESTful CRUD, permission checks, cascade deletion, unique constraint
#             handling (409 Conflict), audit logging.
# ============================================================================

"""FastAPI router for user CRUD operations."""

# APIRouter groups related endpoints; Depends injects dependencies; HTTPException reports errors.
from fastapi import APIRouter, Depends, HTTPException, Query, status

# AsyncSession is the database session interface.
from sqlalchemy.ext.asyncio import AsyncSession

# select, func, and delete from SQLAlchemy.
from sqlalchemy import func, select

# get_db provides a session per request.
from app.database import get_db

# Role-based dependency functions.
from app.dependencies import get_current_user, require_admin

# Models for User and Task.
from app.models.task import Task
from app.models.user import User

# Pydantic schemas for request and response data contracts.
from app.schemas.common import Role
from app.schemas.user import UserCreate, UserRead, UserUpdate

# Audit logging service.
from app.services.activity import log_activity

# Instantiate the router with appropriate prefix and tags.
router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "",
    response_model=list[UserRead],
    summary="List all users",
    description="Returns all registered users. Used by the role switcher and admin user panel.",
)
async def list_users(
    # db is the injected database session.
    db: AsyncSession = Depends(get_db),
    # current_user ensures caller is identified.
    current_user: User = Depends(get_current_user),
) -> list[User]:
    """Retrieve all users ordered by creation date ascending.

    Parameters:
        db (AsyncSession): Active database session.
        current_user (User): Verified requesting user.

    Returns:
        list[User]: List of User model instances.
    """
    # Regular users can resolve only their own account; admins manage the directory.
    query = select(User).order_by(User.id.asc())
    if current_user.role != Role.ADMIN:
        query = query.where(User.id == current_user.id)

    # Execute query asynchronously.
    result = await db.execute(query)

    # Return list of user records.
    return list(result.scalars().all())


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    description="Creates a new user identity. Requires admin privileges.",
)
async def create_user(
    # payload contains validated user input.
    payload: UserCreate,
    # db is the active session.
    db: AsyncSession = Depends(get_db),
    # admin verifies administrator rights.
    admin: User = Depends(require_admin),
) -> User:
    """Create a new user.

    Parameters:
        payload (UserCreate): Input data (name, email, role).
        db (AsyncSession): Active database session.
        admin (User): Verified admin user.

    Returns:
        User: Newly created user entity.

    Raises:
        HTTPException(409): If the email is already registered.
    """
    # Check if a user with this email already exists.
    stmt = select(User).where(User.email == payload.email)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{payload.email}' already exists.",
        )

    # Create new User model instance.
    new_user = User(
        name=payload.name,
        email=payload.email,
        role=payload.role.value,
    )

    # Add to database session.
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Record activity log.
    await log_activity(
        db=db,
        actor=admin,
        action="created",
        entity_type="user",
        entity_id=new_user.id,
        detail={"email": new_user.email, "role": new_user.role},
    )
    await db.commit()

    # Return created user.
    return new_user


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Get user by ID",
    description="Retrieves a single user by primary key ID.",
)
async def get_user(
    # user_id from path.
    user_id: int,
    # db session.
    db: AsyncSession = Depends(get_db),
    # current_user ensures caller identity.
    current_user: User = Depends(get_current_user),
) -> User:
    """Fetch user by ID.

    Parameters:
        user_id (int): Target user ID.
        db (AsyncSession): Active database session.
        current_user (User): Requesting user.

    Returns:
        User: User ORM entity.

    Raises:
        HTTPException(404): If user is not found.
    """
    if current_user.role != Role.ADMIN and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access another user's profile.",
        )

    # Query database for user.
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )
    # Return user entity.
    return user


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user",
    description="Updates user attributes. Regular users can only update their own name/email; admins can update anyone.",
)
async def update_user(
    # user_id to update.
    user_id: int,
    # payload with updated fields.
    payload: UserUpdate,
    # db session.
    db: AsyncSession = Depends(get_db),
    # current_user performing the update.
    current_user: User = Depends(get_current_user),
) -> User:
    """Partially update user attributes.

    Parameters:
        user_id (int): Target user ID.
        payload (UserUpdate): Changes to apply.
        db (AsyncSession): Database session.
        current_user (User): Verified requesting user.

    Returns:
        User: Updated user model.

    Raises:
        HTTPException(403): If non-admin attempts to edit another user or change role.
        HTTPException(404): If user not found.
        HTTPException(409): If new email conflicts with another user.
    """
    # Non-admins cannot edit other users.
    if current_user.role != Role.ADMIN and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update another user's profile.",
        )

    # Non-admins cannot elevate their own role to admin.
    if current_user.role != Role.ADMIN and payload.role is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can modify user roles.",
        )

    # Fetch target user.
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )

    # If email changed, check for conflict.
    if payload.email and payload.email != user.email:
        chk_stmt = select(User).where(User.email == payload.email)
        existing = (await db.execute(chk_stmt)).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{payload.email}' is already taken.",
            )
        user.email = payload.email

    # Apply name update.
    if payload.name is not None:
        user.name = payload.name

    # Apply role update if authorized.
    if payload.role is not None:
        if user.role == Role.ADMIN and payload.role == Role.USER:
            admin_count = (await db.execute(
                select(func.count(User.id)).where(User.role == Role.ADMIN.value)
            )).scalar() or 0
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote the last administrator.",
                )
        user.role = payload.role.value

    # Commit changes.
    await db.commit()
    await db.refresh(user)

    # Record activity log.
    await log_activity(
        db=db,
        actor=current_user,
        action="updated",
        entity_type="user",
        entity_id=user.id,
        detail={"name": user.name, "role": user.role},
    )
    await db.commit()

    # Return updated user.
    return user


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
    description="Deletes a user and cascades deletion to their tasks and comments. Admin only.",
)
async def delete_user(
    # user_id to delete.
    user_id: int,
    # db session.
    db: AsyncSession = Depends(get_db),
    # admin verification.
    admin: User = Depends(require_admin),
) -> None:
    """Delete a user.

    Parameters:
        user_id (int): Target user ID.
        db (AsyncSession): Database session.
        admin (User): Verified admin user.

    Raises:
        HTTPException(404): If user not found.
        HTTPException(400): If admin attempts to delete themselves.
    """
    # Prevent admin from deleting their own active account.
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own admin account.",
        )

    # Fetch target user.
    stmt = select(User).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} not found.",
        )

    # Save details for audit log before deletion.
    user_name = user.name
    user_email = user.email

    # Delete user from session (cascades in PostgreSQL).
    await db.delete(user)
    await db.commit()

    # Record activity log (actor_name persists even though user row is deleted).
    await log_activity(
        db=db,
        actor=admin,
        action="deleted",
        entity_type="user",
        entity_id=user_id,
        detail={"name": user_name, "email": user_email},
    )
    await db.commit()
