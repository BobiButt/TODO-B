# ============================================================================
# FILE:       backend/app/routers/admin.py
# PURPOSE:    FastAPI router for administrator panel endpoints: KPI dashboard stats,
#             tasks-per-user aggregation, global task reassignment, bulk operations,
#             comment moderation overview, and audit activity log browsing.
# HOW IT WORKS: Every endpoint is protected with Depends(require_admin), returning
#             HTTP 403 Forbidden to non-administrators. Uses SQL aggregations
#             (COUNT, SUM, GROUP BY) for high-performance reporting.
# USED BY / DEPENDS ON: app/main.py, app/dependencies.py, app/models/*.
# KEY CONCEPTS: RBAC enforcement, SQL aggregation metrics, bulk data mutations,
#             audit logging, moderation overview.
# ============================================================================

"""FastAPI router for administrator panel metrics, moderation, and bulk actions."""

# Standard library date, datetime, and timezone.
from datetime import date, datetime, timezone

# FastAPI tools.
from fastapi import APIRouter, Depends, HTTPException, Query, status

# Pydantic models for admin data contracts.
from pydantic import BaseModel, ConfigDict, Field

# SQLAlchemy async session and query constructs.
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update

# Database session dependency.
from app.database import get_db

# Admin role dependency.
from app.dependencies import require_admin

# ORM models.
from app.models.activity import ActivityLog
from app.models.comment import Comment
from app.models.task import Task
from app.models.user import User

# Common enums.
from app.schemas.common import Page, Status

# Activity logging service.
from app.services.activity import log_activity

# Router instance with prefix /admin.
router = APIRouter(prefix="/admin", tags=["Admin"])


class UserTaskStat(BaseModel):
    """Aggregate statistics for a single user's tasks.

    Attributes:
        user_id (int): User identifier.
        user_name (str): User display name.
        task_count (int): Total active tasks owned.
        completed_count (int): Completed tasks count.
    """

    user_id: int = Field(..., description="User identifier.")
    user_name: str = Field(..., description="Display name.")
    task_count: int = Field(..., description="Total active tasks.")
    completed_count: int = Field(..., description="Completed tasks.")


class AdminStats(BaseModel):
    """System-wide KPI metrics and aggregates for the admin dashboard.

    Attributes:
        total_users (int): Total registered accounts.
        total_tasks (int): Total active non-deleted tasks.
        completion_rate (float): Percentage of completed tasks (0.0 to 100.0).
        overdue_tasks (int): Count of active past-deadline tasks.
        total_comments (int): Count of all task comments.
        tasks_per_user (list[UserTaskStat]): Breakdown of task counts per user.
    """

    total_users: int = Field(..., description="Total registered users.")
    total_tasks: int = Field(..., description="Total active tasks across system.")
    completion_rate: float = Field(..., description="Overall completion rate (0-100%).")
    overdue_tasks: int = Field(..., description="Active tasks past deadline.")
    total_comments: int = Field(..., description="Total comments across all tasks.")
    tasks_per_user: list[UserTaskStat] = Field(default_factory=list, description="Per-user task metrics.")


class BulkActionPayload(BaseModel):
    """Payload for performing batch actions on multiple tasks.

    Attributes:
        task_ids (list[int]): List of target task IDs.
    """

    task_ids: list[int] = Field(..., min_length=1, description="List of task IDs to modify.")


class ReassignPayload(BaseModel):
    """Payload for reassigning a task to a different user.

    Attributes:
        user_id (int): Destination user ID.
    """

    user_id: int = Field(..., description="ID of the new owner user.")


class ActivityLogRead(BaseModel):
    """Schema for returning activity log audit entries.

    Attributes:
        id (int): Log entry primary key.
        user_id (int | None): Actor user ID.
        actor_name (str): Snapshot of actor display name.
        action (str): Action verb.
        entity_type (str): Affected entity type.
        entity_id (int | None): ID of affected row.
        detail (dict): Context JSON data.
        created_at (datetime): UTC instant.
    """

    id: int
    user_id: int | None
    actor_name: str
    action: str
    entity_type: str
    entity_id: int | None
    detail: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CommentModerationRead(BaseModel):
    """Schema for comments moderation view.

    Attributes:
        id (int): Comment ID.
        task_id (int): Task ID.
        task_title (str): Parent task title.
        user_id (int): Author user ID.
        author_name (str): Author display name.
        body (str): Message text.
        created_at (datetime): Creation timestamp.
    """

    id: int
    task_id: int
    task_title: str
    user_id: int
    author_name: str
    body: str
    created_at: datetime


@router.get(
    "/stats",
    response_model=AdminStats,
    summary="Get system-wide admin dashboard statistics",
    description="Returns aggregate KPI metrics including completion rate, overdue tasks, and per-user counts.",
)
async def get_admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminStats:
    """Calculate and return system KPI statistics.

    Parameters:
        db (AsyncSession): Database session.
        admin (User): Verified admin user.

    Returns:
        AdminStats: Aggregated statistics.
    """
    # Total users count.
    user_count_stmt = select(func.count(User.id))
    total_users = (await db.execute(user_count_stmt)).scalar() or 0

    # Total active tasks count.
    task_count_stmt = select(func.count(Task.id)).where(Task.deleted_at.is_(None))
    total_tasks = (await db.execute(task_count_stmt)).scalar() or 0

    # Completed tasks count.
    completed_stmt = select(func.count(Task.id)).where(
        Task.deleted_at.is_(None), Task.status == Status.COMPLETED.value
    )
    completed_tasks = (await db.execute(completed_stmt)).scalar() or 0

    # Calculate completion percentage.
    completion_rate = 0.0
    if total_tasks > 0:
        completion_rate = round((completed_tasks / total_tasks) * 100.0, 1)

    # Overdue tasks count (due_date < today and status != completed).
    today = date.today()
    overdue_stmt = select(func.count(Task.id)).where(
        Task.deleted_at.is_(None),
        Task.due_date.is_not(None),
        Task.due_date < today,
        Task.status != Status.COMPLETED.value,
    )
    overdue_count = (await db.execute(overdue_stmt)).scalar() or 0

    # Total comments count.
    comment_count_stmt = select(func.count(Comment.id))
    total_comments = (await db.execute(comment_count_stmt)).scalar() or 0

    # Tasks per user breakdown.
    users_stmt = select(User).order_by(User.id.asc())
    all_users = (await db.execute(users_stmt)).scalars().all()

    per_user_stats: list[UserTaskStat] = []
    for u in all_users:
        u_total_stmt = select(func.count(Task.id)).where(Task.user_id == u.id, Task.deleted_at.is_(None))
        u_total = (await db.execute(u_total_stmt)).scalar() or 0

        u_comp_stmt = select(func.count(Task.id)).where(
            Task.user_id == u.id, Task.deleted_at.is_(None), Task.status == Status.COMPLETED.value
        )
        u_comp = (await db.execute(u_comp_stmt)).scalar() or 0

        per_user_stats.append(
            UserTaskStat(
                user_id=u.id,
                user_name=u.name,
                task_count=u_total,
                completed_count=u_comp,
            )
        )

    return AdminStats(
        total_users=total_users,
        total_tasks=total_tasks,
        completion_rate=completion_rate,
        overdue_tasks=overdue_count,
        total_comments=total_comments,
        tasks_per_user=per_user_stats,
    )


@router.get(
    "/activity",
    response_model=Page[ActivityLogRead],
    summary="Get audit activity log",
    description="Returns paginated audit trail entries sorted newest first.",
)
async def get_activity_log(
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    action: str | None = Query(default=None, description="Filter by action verb"),
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> Page[ActivityLogRead]:
    """Retrieve paginated activity log entries.

    Parameters:
        page (int): Page number.
        page_size (int): Items per page.
        action (str | None): Optional action filter.
        entity_type (str | None): Optional entity filter.
        db (AsyncSession): Database session.
        admin (User): Verified admin.

    Returns:
        Page[ActivityLogRead]: Paginated activity records.
    """
    query = select(ActivityLog)

    if action:
        query = query.where(ActivityLog.action == action)
    if entity_type:
        query = query.where(ActivityLog.entity_type == entity_type)

    # Count total entries.
    count_stmt = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Order newest first.
    query = query.order_by(ActivityLog.created_at.desc())

    # Offset and limit.
    offset_val = (page - 1) * page_size
    query = query.offset(offset_val).limit(page_size)

    result = await db.execute(query)
    entries = result.scalars().all()

    items = [ActivityLogRead.model_validate(e) for e in entries]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return Page[ActivityLogRead](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages,
    )


@router.get(
    "/comments",
    response_model=list[CommentModerationRead],
    summary="List all comments for moderation",
    description="Returns recent comments across all tasks with parent task titles.",
)
async def list_all_comments_for_moderation(
    limit: int = Query(default=50, ge=1, le=200, description="Max comments to return"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> list[CommentModerationRead]:
    """Retrieve comments across all tasks for moderation.

    Parameters:
        limit (int): Max records.
        db (AsyncSession): Database session.
        admin (User): Verified admin.

    Returns:
        list[CommentModerationRead]: Comments for moderation.
    """
    query = (
        select(Comment, Task.title, User.name)
        .join(Task, Comment.task_id == Task.id)
        .join(User, Comment.user_id == User.id)
        .order_by(Comment.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    output: list[CommentModerationRead] = []
    for c, task_title, user_name in rows:
        output.append(
            CommentModerationRead(
                id=c.id,
                task_id=c.task_id,
                task_title=task_title,
                user_id=c.user_id,
                author_name=user_name,
                body=c.body,
                created_at=c.created_at,
            )
        )

    return output


@router.post(
    "/tasks/bulk-complete",
    summary="Mark multiple tasks as complete",
    description="Bulk action to mark multiple tasks completed simultaneously.",
)
async def bulk_complete_tasks(
    payload: BulkActionPayload,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Bulk complete tasks.

    Parameters:
        payload (BulkActionPayload): Task IDs to mark completed.
        db (AsyncSession): Database session.
        admin (User): Verified admin.

    Returns:
        dict: Count of updated tasks.
    """
    stmt = (
        update(Task)
        .where(Task.id.in_(payload.task_ids), Task.deleted_at.is_(None))
        .values(status=Status.COMPLETED.value)
    )
    result = await db.execute(stmt)
    await db.commit()

    count = result.rowcount

    # Log activity.
    await log_activity(
        db=db,
        actor=admin,
        action="updated",
        entity_type="task",
        entity_id=None,
        detail={"bulk_action": "complete", "count": count, "ids": payload.task_ids},
    )
    await db.commit()

    return {"message": f"Successfully completed {count} tasks.", "count": count}


@router.post(
    "/tasks/bulk-delete",
    summary="Soft-delete multiple tasks",
    description="Bulk action to soft-delete multiple tasks simultaneously.",
)
async def bulk_delete_tasks(
    payload: BulkActionPayload,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Bulk soft-delete tasks.

    Parameters:
        payload (BulkActionPayload): Task IDs to delete.
        db (AsyncSession): Database session.
        admin (User): Verified admin.

    Returns:
        dict: Count of deleted tasks.
    """
    stmt = (
        update(Task)
        .where(Task.id.in_(payload.task_ids), Task.deleted_at.is_(None))
        .values(deleted_at=datetime.now(timezone.utc))
    )
    result = await db.execute(stmt)
    await db.commit()

    count = result.rowcount

    # Log activity.
    await log_activity(
        db=db,
        actor=admin,
        action="deleted",
        entity_type="task",
        entity_id=None,
        detail={"bulk_action": "delete", "count": count, "ids": payload.task_ids},
    )
    await db.commit()

    return {"message": f"Successfully deleted {count} tasks.", "count": count}


@router.patch(
    "/tasks/{task_id}/reassign",
    summary="Reassign task to another user",
    description="Changes task owner to another user ID.",
)
async def reassign_task(
    task_id: int,
    payload: ReassignPayload,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Reassign task ownership.

    Parameters:
        task_id (int): Task ID to reassign.
        payload (ReassignPayload): Target user ID.
        db (AsyncSession): Database session.
        admin (User): Verified admin.

    Returns:
        dict: Success message.

    Raises:
        HTTPException(404): If task or destination user not found.
    """
    # Verify destination user exists.
    dest_stmt = select(User).where(User.id == payload.user_id)
    dest_user = (await db.execute(dest_stmt)).scalars().first()
    if not dest_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target user with ID {payload.user_id} not found.",
        )

    # Fetch task.
    task_stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(task_stmt)).scalars().first()
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    old_user_id = task.user_id
    task.user_id = payload.user_id

    await db.commit()

    # Log activity.
    await log_activity(
        db=db,
        actor=admin,
        action="updated",
        entity_type="task",
        entity_id=task_id,
        detail={
            "reassigned": True,
            "old_user_id": old_user_id,
            "new_user_id": payload.user_id,
        },
    )
    await db.commit()

    return {
        "message": f"Task {task_id} reassigned to {dest_user.name}.",
        "task_id": task_id,
        "new_user_id": payload.user_id,
    }
