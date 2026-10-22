# ============================================================================
# FILE:       backend/app/routers/comments.py
# PURPOSE:    FastAPI router for task comment threads (/api/v1/tasks/{task_id}/comments).
#             Provides list, create, edit, and moderation delete endpoints.
# HOW IT WORKS: Returns threads ordered oldest-first (ascending) so the UI displays
#             the newest comment at the bottom. Enforces permissions: users can
#             edit or delete only their own comments; admins may delete any comment.
#             Edits flag is_edited=True. Actions are recorded in the activity log.
# USED BY / DEPENDS ON: app/main.py, app/dependencies.py, app/services/activity.py.
# KEY CONCEPTS: Discussion threads, author-only edits, admin moderation,
#             audit logging, optimistic update backend contract.
# ============================================================================

"""FastAPI router for task comments and thread discussions."""

# FastAPI routing, dependency injection, and exception tools.
from fastapi import APIRouter, Depends, HTTPException, status

# SQLAlchemy async session and query constructs.
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Database session dependency.
from app.database import get_db

# Role dependencies for current user resolution.
from app.dependencies import get_current_user

# ORM models.
from app.models.comment import Comment
from app.models.task import Task
from app.models.user import User

# Pydantic schemas.
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate
from app.schemas.common import Role

# Activity logging service.
from app.services.activity import log_activity

# Router instance with prefix for task comments.
router = APIRouter(prefix="/tasks/{task_id}/comments", tags=["Comments"])


def _map_comment_to_read(comment: Comment) -> CommentRead:
    """Helper to convert a Comment ORM model to CommentRead schema.

    Parameters:
        comment (Comment): The ORM Comment entity.

    Returns:
        CommentRead: Populated schema with author display name.
    """
    author_name = comment.user.name if comment.user else "Unknown"
    return CommentRead(
        id=comment.id,
        task_id=comment.task_id,
        user_id=comment.user_id,
        author_name=author_name,
        body=comment.body,
        is_edited=comment.is_edited,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.get(
    "",
    response_model=list[CommentRead],
    summary="List comments for a task",
    description="Returns all comments for the specified task in chronological order (oldest first).",
)
async def list_comments(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CommentRead]:
    """Retrieve all comments on a task.

    Parameters:
        task_id (int): ID of the task.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        list[CommentRead]: Ordered list of comments.

    Raises:
        HTTPException(404): If task not found.
        HTTPException(403): If user does not own task and is not admin.
    """
    # Verify task existence and access rights.
    task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(task_stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access comments for another user's task.",
        )

    # Query comments ordered oldest-first (newest last).
    query = (
        select(Comment)
        .where(Comment.task_id == task_id)
        .order_by(Comment.created_at.asc())
    )
    result = await db.execute(query)
    comments = result.scalars().all()

    return [_map_comment_to_read(c) for c in comments]


@router.post(
    "",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment",
    description="Adds a new comment to the task discussion thread.",
)
async def create_comment(
    task_id: int,
    payload: CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentRead:
    """Create a new comment on a task.

    Parameters:
        task_id (int): Parent task ID.
        payload (CommentCreate): Comment body schema (1-1000 chars).
        db (AsyncSession): Database session.
        current_user (User): Author of the comment.

    Returns:
        CommentRead: Newly created comment.

    Raises:
        HTTPException(404): If task not found.
        HTTPException(403): If non-admin posts to another user's task.
    """
    # Fetch parent task.
    task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(task_stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot comment on another user's task.",
        )

    # Instantiate comment model.
    new_comment = Comment(
        task_id=task_id,
        user_id=current_user.id,
        body=payload.body,
        is_edited=False,
    )

    db.add(new_comment)
    await db.commit()
    await db.refresh(new_comment)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="created",
        entity_type="comment",
        entity_id=new_comment.id,
        detail={"task_id": task_id, "preview": new_comment.body[:40]},
    )
    await db.commit()

    return _map_comment_to_read(new_comment)


@router.patch(
    "/{comment_id}",
    response_model=CommentRead,
    summary="Edit a comment",
    description="Updates a comment body and sets is_edited=True. Only the original author can edit.",
)
async def update_comment(
    task_id: int,
    comment_id: int,
    payload: CommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CommentRead:
    """Edit comment text.

    Parameters:
        task_id (int): Parent task ID.
        comment_id (int): Comment ID to edit.
        payload (CommentUpdate): Updated body text.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        CommentRead: Updated comment.

    Raises:
        HTTPException(404): If comment not found.
        HTTPException(403): If caller is not the author.
    """
    stmt = select(Comment).where(Comment.id == comment_id, Comment.task_id == task_id)
    comment = (await db.execute(stmt)).scalars().first()

    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found on task {task_id}.",
        )

    # Only original author may edit comment text (admins moderate by deletion).
    if comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit comments authored by yourself.",
        )

    comment.body = payload.body
    comment.is_edited = True

    await db.commit()
    await db.refresh(comment)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="updated",
        entity_type="comment",
        entity_id=comment.id,
        detail={"task_id": task_id},
    )
    await db.commit()

    return _map_comment_to_read(comment)


@router.delete(
    "/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment",
    description="Deletes a comment. The author or an administrator can delete comments.",
)
async def delete_comment(
    task_id: int,
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a comment.

    Parameters:
        task_id (int): Parent task ID.
        comment_id (int): Comment ID.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Raises:
        HTTPException(404): If not found.
        HTTPException(403): If unauthorized.
    """
    stmt = select(Comment).where(Comment.id == comment_id, Comment.task_id == task_id)
    comment = (await db.execute(stmt)).scalars().first()

    if not comment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Comment with ID {comment_id} not found on task {task_id}.",
        )

    # Author can delete their own; admin can delete any comment.
    if current_user.role != Role.ADMIN and comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete a comment authored by another user.",
        )

    await db.delete(comment)
    await db.commit()

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="deleted",
        entity_type="comment",
        entity_id=comment_id,
        detail={"task_id": task_id},
    )
    await db.commit()
