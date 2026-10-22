# ============================================================================
# FILE:       backend/app/routers/tasks.py
# PURPOSE:    FastAPI router for task CRUD operations, checklist updates,
#             status toggles, drag-and-drop manual reordering, and soft-delete/restore.
# HOW IT WORKS: Validates incoming request parameters and JSON bodies with Pydantic.
#             Enforces ownership: regular users can only see, modify, or delete their
#             own tasks; administrators can access and reassign any task.
#             Implements rich filtering (search query, priority, status, tags via
#             ANY(tags), overdue, due today) and pagination.
# USED BY / DEPENDS ON: app/main.py, app/dependencies.py, app/services/activity.py.
# KEY CONCEPTS: Soft delete pattern (deleted_at IS NULL), array filtering ANY(tags),
#             optimistic undo restore, fractional reordering, pagination envelope.
# ============================================================================

"""FastAPI router for task management and operations."""

# Standard library date and datetime.
from datetime import date, datetime, timezone

# FastAPI classes for routing, validation, error handling.
from fastapi import APIRouter, Depends, HTTPException, Query, status

# SQLAlchemy async session and query builders.
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import selectinload

# Database session dependency.
from app.database import get_db

# Role dependencies for current user resolution.
from app.dependencies import get_current_user

# ORM models.
from app.models.comment import Comment
from app.models.subtask import Subtask
from app.models.task import Task
from app.models.user import User

# Pydantic schemas.
from app.schemas.common import Page, Priority, Role, Status
from app.schemas.task import (
    SubtaskCreate,
    SubtaskRead,
    SubtaskUpdate,
    TaskCreate,
    TaskRead,
    TaskReorder,
    TaskUpdate,
)

# Audit activity logging service.
from app.services.activity import log_activity

# Router instance mounted under /tasks.
router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _map_task_to_read(task: Task) -> TaskRead:
    """Helper to convert a Task ORM model to TaskRead schema with relations.

    Parameters:
        task (Task): The Task ORM entity.

    Returns:
        TaskRead: The serialized Pydantic task representation.
    """
    # Count comments safely.
    comment_count = len(task.comments) if task.comments else 0

    # Build subtask schemas.
    subtasks_read = [
        SubtaskRead(
            id=st.id,
            task_id=st.task_id,
            title=st.title,
            is_done=st.is_done,
            position=st.position,
            created_at=st.created_at,
        )
        for st in (task.subtasks or [])
    ]

    # Return populated TaskRead.
    return TaskRead(
        id=task.id,
        user_id=task.user_id,
        owner_name=task.user.name if task.user else None,
        title=task.title,
        description=task.description,
        priority=Priority(task.priority),
        status=Status(task.status),
        due_date=task.due_date,
        tags=task.tags or [],
        category=task.category,
        position=task.position,
        deleted_at=task.deleted_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        subtasks=subtasks_read,
        comments_count=comment_count,
    )


@router.get(
    "",
    response_model=Page[TaskRead],
    summary="List tasks with filtering and pagination",
    description="Returns active tasks filtered by search, status, priority, tag, overdue, or due date.",
)
async def list_tasks(
    # Search query string across title and description.
    q: str | None = Query(default=None, description="Search term for title and description"),
    # Filter by task status.
    status_filter: Status | None = Query(default=None, alias="status", description="Filter by status"),
    # Filter by priority level.
    priority: Priority | None = Query(default=None, description="Filter by priority"),
    # Filter by single tag using PostgreSQL array containment.
    tag: str | None = Query(default=None, description="Filter tasks containing this tag"),
    # Filter by overdue status (due_date < today and not completed).
    overdue: bool | None = Query(default=None, description="Filter overdue tasks"),
    # Filter by due today status (due_date == today and not completed).
    due_today: bool | None = Query(default=None, description="Filter tasks due today"),
    # Admin filter by specific user_id.
    user_id: int | None = Query(default=None, description="Admin: filter tasks by user ID"),
    # Sort column.
    sort: str = Query(default="position", description="Sort by: position, due_date, priority, created"),
    # Sort direction.
    order: str = Query(default="asc", description="Sort direction: asc or desc"),
    # 1-indexed page number.
    page: int = Query(default=1, ge=1, description="Page number"),
    # Page size limit.
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    # Database session.
    db: AsyncSession = Depends(get_db),
    # Authenticated user.
    current_user: User = Depends(get_current_user),
) -> Page[TaskRead]:
    """List paginated tasks according to filter criteria.

    Parameters:
        q: Search string.
        status_filter: Status filter.
        priority: Priority filter.
        tag: Tag filter.
        overdue: Boolean overdue filter.
        due_today: Boolean due today filter.
        user_id: Optional user filter for admins.
        sort: Sort column.
        order: Sort direction.
        page: Page number.
        page_size: Number of items per page.
        db: Database session.
        current_user: Active requesting user.

    Returns:
        Page[TaskRead]: Paginated envelope containing items and metadata.
    """
    # Base query for tasks that are not soft-deleted.
    query = select(Task).where(Task.deleted_at.is_(None))

    # Access control: regular users only see their own tasks; admins can filter or view all.
    if current_user.role != Role.ADMIN:
        query = query.where(Task.user_id == current_user.id)
    elif user_id is not None:
        query = query.where(Task.user_id == user_id)

    # Search filter across title and description.
    if q:
        search_pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Task.title.ilike(search_pattern),
                Task.description.ilike(search_pattern),
            )
        )

    # Status filter.
    if status_filter:
        query = query.where(Task.status == status_filter.value)

    # Priority filter.
    if priority:
        query = query.where(Task.priority == priority.value)

    # Tag filter using PostgreSQL ANY(tags) expression.
    if tag:
        clean_tag = tag.strip().lower()
        query = query.where(func.lower(clean_tag) == func.any(Task.tags))

    # Overdue filter (due_date < current_date and status != 'completed').
    today = date.today()
    if overdue is True:
        query = query.where(
            and_(
                Task.due_date.is_not(None),
                Task.due_date < today,
                Task.status != Status.COMPLETED.value,
            )
        )

    # Due today filter (due_date == today and status != 'completed').
    if due_today is True:
        query = query.where(
            and_(
                Task.due_date == today,
                Task.status != Status.COMPLETED.value,
            )
        )

    # Calculate total item count for pagination metadata.
    count_stmt = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Apply sorting.
    sort_column = Task.position
    if sort == "due_date":
        sort_column = Task.due_date
    elif sort == "priority":
        sort_column = Task.priority
    elif sort == "created":
        sort_column = Task.created_at

    if order.lower() == "desc":
        query = query.order_by(sort_column.desc().nullslast())
    else:
        query = query.order_by(sort_column.asc().nullslast())

    # Apply pagination offset and limit.
    offset_val = (page - 1) * page_size
    query = query.offset(offset_val).limit(page_size)

    # Execute query to load task entities.
    result = await db.execute(query)
    tasks = result.scalars().all()

    # Convert to TaskRead items.
    items = [_map_task_to_read(t) for t in tasks]

    # Return paginated envelope.
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return Page[TaskRead](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=total_pages,
    )


@router.get("/stats", summary="Get task totals for the active scope")
async def get_task_stats(
    user_id: int | None = Query(default=None, description="Admin: limit totals to one user"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    scope = [Task.deleted_at.is_(None)]
    if current_user.role != Role.ADMIN:
        scope.append(Task.user_id == current_user.id)
    elif user_id is not None:
        scope.append(Task.user_id == user_id)

    today = date.today()
    total = (await db.execute(select(func.count(Task.id)).where(*scope))).scalar() or 0
    pending = (await db.execute(select(func.count(Task.id)).where(*scope, Task.status == Status.PENDING.value))).scalar() or 0
    in_progress = (await db.execute(select(func.count(Task.id)).where(*scope, Task.status == Status.IN_PROGRESS.value))).scalar() or 0
    completed = (await db.execute(select(func.count(Task.id)).where(*scope, Task.status == Status.COMPLETED.value))).scalar() or 0
    overdue = (await db.execute(select(func.count(Task.id)).where(*scope, Task.due_date < today, Task.status != Status.COMPLETED.value))).scalar() or 0
    return {"total": total, "pending": pending, "in_progress": in_progress, "completed": completed, "overdue": overdue}


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new task",
    description="Creates a new task with optional checklist subtasks.",
)
async def create_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    """Create a task with checklist items for the current user.

    Parameters:
        payload (TaskCreate): Validated task creation schema.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        TaskRead: Newly created task.
    """
    # Calculate next position if default 0.0 provided.
    position_val = payload.position
    if position_val == 0.0:
        max_pos_stmt = select(func.max(Task.position)).where(
            Task.user_id == current_user.id, Task.deleted_at.is_(None)
        )
        max_pos = (await db.execute(max_pos_stmt)).scalar()
        position_val = (max_pos or 0.0) + 1.0

    # Instantiate Task model.
    new_task = Task(
        user_id=current_user.id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority.value,
        status=payload.status.value,
        due_date=payload.due_date,
        tags=payload.tags,
        category=payload.category,
        position=position_val,
    )

    db.add(new_task)
    await db.flush()

    # Add nested subtasks if any.
    for idx, st_create in enumerate(payload.subtasks):
        subtask = Subtask(
            task_id=new_task.id,
            title=st_create.title,
            is_done=st_create.is_done,
            position=idx,
        )
        db.add(subtask)

    await db.commit()
    await db.refresh(new_task)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="created",
        entity_type="task",
        entity_id=new_task.id,
        detail={"title": new_task.title, "priority": new_task.priority},
    )
    await db.commit()

    return _map_task_to_read(new_task)


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Get task details",
    description="Retrieves a single task with its checklist subtasks.",
)
async def get_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    """Fetch task details by ID.

    Parameters:
        task_id (int): Primary key ID.
        db (AsyncSession): Active session.
        current_user (User): Requesting user.

    Returns:
        TaskRead: Serialized task model.

    Raises:
        HTTPException(404): If not found.
        HTTPException(403): If access forbidden for non-admin non-owner.
    """
    stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    # Enforce ownership.
    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access tasks belonging to another user.",
        )

    return _map_task_to_read(task)


@router.patch(
    "/reorder",
    response_model=list[TaskRead],
    summary="Reorder tasks manually",
    description="Normalizes task positions according to the submitted list of ordered IDs.",
)
async def reorder_tasks(
    payload: TaskReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaskRead]:
    """Normalize task positions from 1.0 upwards based on ordered ID list.

    Parameters:
        payload (TaskReorder): List of ordered task IDs.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        list[TaskRead]: Updated list of tasks.
    """
    updated_tasks: list[Task] = []

    for new_index, task_id in enumerate(payload.ordered_ids, start=1):
        stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        task = (await db.execute(stmt)).scalars().first()

        if task:
            # Check ownership.
            if current_user.role != Role.ADMIN and task.user_id != current_user.id:
                continue
            task.position = float(new_index)
            updated_tasks.append(task)

    await db.commit()

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="reordered",
        entity_type="task",
        entity_id=None,
        detail={"count": len(updated_tasks)},
    )
    await db.commit()

    # Re-fetch updated tasks with preloaded relationships to prevent lazy loading MissingGreenlet error.
    fetch_stmt = (
        select(Task)
        .options(selectinload(Task.subtasks), selectinload(Task.comments))
        .where(Task.id.in_(payload.ordered_ids), Task.deleted_at.is_(None))
        .order_by(Task.position.asc())
    )
    fetch_res = await db.execute(fetch_stmt)
    final_tasks = fetch_res.scalars().all()

    return [_map_task_to_read(t) for t in final_tasks]


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    summary="Update a task",
    description="Partially updates task fields.",
)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    """Update task attributes.

    Parameters:
        task_id (int): Task ID to update.
        payload (TaskUpdate): Fields to modify.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        TaskRead: Updated task.

    Raises:
        HTTPException(404): If task not found.
        HTTPException(403): If non-owner non-admin.
    """
    stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify tasks belonging to another user.",
        )

    if payload.title is not None:
        task.title = payload.title
    if "description" in payload.model_fields_set:
        task.description = payload.description
    if payload.priority is not None:
        task.priority = payload.priority.value
    if payload.status is not None:
        task.status = payload.status.value
    if "due_date" in payload.model_fields_set:
        task.due_date = payload.due_date
    if payload.tags is not None:
        task.tags = payload.tags
    if "category" in payload.model_fields_set:
        task.category = payload.category
    if payload.position is not None:
        task.position = payload.position

    await db.commit()
    await db.refresh(task)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="updated",
        entity_type="task",
        entity_id=task.id,
        detail={"title": task.title, "status": task.status},
    )
    await db.commit()

    return _map_task_to_read(task)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a task",
    description="Marks a task as soft-deleted by stamping deleted_at. Allows a 5-second undo window.",
)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Soft delete task by updating deleted_at timestamp.

    Parameters:
        task_id (int): Task ID.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Raises:
        HTTPException(404): If not found.
        HTTPException(403): If unauthorized.
    """
    stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete tasks belonging to another user.",
        )

    task_title = task.title

    # Apply soft-delete timestamp in UTC.
    task.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="deleted",
        entity_type="task",
        entity_id=task_id,
        detail={"title": task_title, "soft_delete": True},
    )
    await db.commit()


@router.post(
    "/{task_id}/restore",
    response_model=TaskRead,
    summary="Restore a soft-deleted task",
    description="Clears the deleted_at timestamp to undo accidental deletion.",
)
async def restore_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    """Restore a soft-deleted task.

    Parameters:
        task_id (int): Task ID to restore.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        TaskRead: Restored task.

    Raises:
        HTTPException(404): If task not found in soft-deleted state.
        HTTPException(403): If unauthorized.
    """
    stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_not(None))
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Soft-deleted task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot restore tasks belonging to another user.",
        )

    task.deleted_at = None
    await db.commit()
    await db.refresh(task)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="restored",
        entity_type="task",
        entity_id=task.id,
        detail={"title": task.title},
    )
    await db.commit()

    return _map_task_to_read(task)


@router.patch(
    "/{task_id}/toggle",
    response_model=TaskRead,
    summary="Toggle task status",
    description="Toggles task status between pending and completed.",
)
async def toggle_task_status(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    """Toggle status between completed and pending.

    Parameters:
        task_id (int): Task ID.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        TaskRead: Updated task.
    """
    stmt = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    task = (await db.execute(stmt)).scalars().first()

    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task with ID {task_id} not found.",
        )

    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot toggle tasks belonging to another user.",
        )

    # Flip status.
    if task.status == Status.COMPLETED.value:
        task.status = Status.PENDING.value
    else:
        task.status = Status.COMPLETED.value

    await db.commit()
    await db.refresh(task)

    # Log activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="toggled",
        entity_type="task",
        entity_id=task.id,
        detail={"new_status": task.status},
    )
    await db.commit()

    return _map_task_to_read(task)




@router.patch(
    "/{task_id}/subtasks/{subtask_id}",
    response_model=SubtaskRead,
    summary="Toggle or update a subtask checklist item",
    description="Updates a subtask title or checked status.",
)
async def update_subtask(
    task_id: int,
    subtask_id: int,
    payload: SubtaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubtaskRead:
    """Update checklist subtask status or title.

    Parameters:
        task_id (int): Parent task ID.
        subtask_id (int): Checklist subtask ID.
        payload (SubtaskUpdate): Fields to update.
        db (AsyncSession): Database session.
        current_user (User): Requesting user.

    Returns:
        SubtaskRead: Updated subtask.
    """
    stmt = (
        select(Subtask)
        .join(Task, Subtask.task_id == Task.id)
        .where(
            Subtask.id == subtask_id,
            Subtask.task_id == task_id,
            Task.deleted_at.is_(None),
        )
    )
    subtask = (await db.execute(stmt)).scalars().first()

    if not subtask:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subtask with ID {subtask_id} not found on task {task_id}.",
        )

    # Check parent task owner.
    parent_stmt = select(Task).where(Task.id == task_id)
    parent_task = (await db.execute(parent_stmt)).scalars().first()
    if parent_task and current_user.role != Role.ADMIN and parent_task.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify checklist items belonging to another user.",
        )

    if payload.title is not None:
        subtask.title = payload.title
    if payload.is_done is not None:
        subtask.is_done = payload.is_done
    if payload.position is not None:
        subtask.position = payload.position

    await db.commit()
    await db.refresh(subtask)

    return SubtaskRead(
        id=subtask.id,
        task_id=subtask.task_id,
        title=subtask.title,
        is_done=subtask.is_done,
        position=subtask.position,
        created_at=subtask.created_at,
    )


@router.post(
    "/{task_id}/subtasks",
    response_model=SubtaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a checklist item",
)
async def create_subtask(
    task_id: int,
    payload: SubtaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubtaskRead:
    task = (await db.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found.")
    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot modify tasks belonging to another user.")

    max_position = (await db.execute(
        select(func.max(Subtask.position)).where(Subtask.task_id == task_id)
    )).scalar()
    subtask = Subtask(task_id=task_id, title=payload.title, is_done=payload.is_done, position=(max_position or 0) + 1)
    db.add(subtask)
    await db.flush()
    await log_activity(db, current_user, "updated", "task", task_id, {"subtask_added": payload.title})
    await db.commit()
    await db.refresh(subtask)
    return SubtaskRead.model_validate(subtask)


@router.delete(
    "/{task_id}/subtasks/{subtask_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a checklist item",
)
async def delete_subtask(
    task_id: int,
    subtask_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    task = (await db.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task with ID {task_id} not found.")
    if current_user.role != Role.ADMIN and task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot modify tasks belonging to another user.")

    subtask = (await db.execute(
        select(Subtask).where(Subtask.id == subtask_id, Subtask.task_id == task_id)
    )).scalars().first()
    if subtask is None:
        raise HTTPException(status_code=404, detail=f"Subtask with ID {subtask_id} not found.")
    await db.delete(subtask)
    await log_activity(db, current_user, "updated", "task", task_id, {"subtask_deleted": subtask_id})
    await db.commit()
