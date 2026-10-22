# ============================================================================
# FILE:       backend/app/routers/transfer.py
# PURPOSE:    FastAPI router for CSV/JSON import and export of tasks.
#             Implements streaming responses, file validation, dry-run simulation,
#             duplicate resolution strategies, and downloadable template generators.
# HOW IT WORKS:
#   - Export streams CSV lines with formula-injection escaping or formatted JSON trees.
#   - Import validates uploaded files (size <= 2 MB, extension, MIME type), parses rows,
#     checks for title collisions, and executes imports inside an atomic transaction.
# USED BY / DEPENDS ON: app/main.py, app/services/transfer.py, app/dependencies.py.
# KEY CONCEPTS: StreamingResponse, file upload handling, CSV Injection defense,
#             dry-run preview, atomic batch transactions.
# ============================================================================

"""FastAPI router for task import and export endpoints."""

# Date formatting for exported file names.
from datetime import date

# FastAPI tools for file uploads, query params, responses, and errors.
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse

# SQLAlchemy async session and query constructs.
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func, or_, select

# Application configuration settings.
from app.config import settings

# Database session dependency.
from app.database import get_db

# Current user resolution dependency.
from app.dependencies import get_current_user

# Models.
from app.models.task import Task
from app.models.user import User

# Transfer schemas and enums.
from app.schemas.common import Priority, Role, Status
from app.schemas.transfer import (
    DuplicateStrategy,
    ImportOptions,
    ImportReport,
)

# Business services for transfer logic and audit logging.
from app.services.activity import log_activity
from app.services.transfer import (
    execute_task_import,
    generate_csv_template,
    generate_json_template,
    parse_csv_content,
    parse_json_content,
    stream_tasks_csv,
    stream_tasks_json,
)

# Router mounted under /tasks.
router = APIRouter(prefix="/tasks", tags=["Import / Export"])


@router.get(
    "/export",
    summary="Export tasks as CSV or JSON",
    description="Streams task data formatted as CSV or JSON respecting active filters and CSV-injection safety.",
)
async def export_tasks(
    format: str = Query(default="csv", pattern="^(csv|json)$", description="Export format: 'csv' or 'json'"),
    q: str | None = Query(default=None, description="Filter by title/description search query"),
    status_filter: Status | None = Query(default=None, alias="status", description="Filter by status"),
    priority: Priority | None = Query(default=None, description="Filter by priority"),
    tag: str | None = Query(default=None, description="Filter by tag"),
    overdue: bool | None = Query(default=None, description="Filter overdue tasks"),
    due_today: bool | None = Query(default=None, description="Filter tasks due today"),
    sort: str = Query(default="position", description="Sort by position, due_date, priority, or created"),
    order: str = Query(default="asc", description="Sort direction"),
    target_user_id: int | None = Query(default=None, alias="user_id", description="Admin: target user ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream tasks exported in CSV or JSON format.

    Parameters:
        format (str): 'csv' or 'json'.
        q: Search filter.
        status_filter: Status filter.
        priority: Priority filter.
        tag: Tag filter.
        target_user_id: Admin filter for specific user.
        db: Database session.
        current_user: Requesting user.

    Returns:
        StreamingResponse: Streamed file content.
    """
    # Build query for tasks that are not soft-deleted.
    query = select(Task).where(Task.deleted_at.is_(None))

    # Access control: regular user exports only own tasks; admin can export all or target user.
    if current_user.role != Role.ADMIN:
        query = query.where(Task.user_id == current_user.id)
    elif target_user_id is not None:
        query = query.where(Task.user_id == target_user_id)

    # Apply filters.
    if q:
        search_pattern = f"%{q.strip()}%"
        query = query.where(or_(Task.title.ilike(search_pattern), Task.description.ilike(search_pattern)))
    if status_filter:
        query = query.where(Task.status == status_filter.value)
    if priority:
        query = query.where(Task.priority == priority.value)
    if tag:
        clean_tag = tag.strip().lower()
        query = query.where(func.lower(clean_tag) == func.any(Task.tags))

    today = date.today()
    if overdue is True:
        query = query.where(and_(Task.due_date.is_not(None), Task.due_date < today, Task.status != Status.COMPLETED.value))
    if due_today is True:
        query = query.where(Task.due_date == today, Task.status != Status.COMPLETED.value)

    sort_column = Task.position
    if sort == "due_date":
        sort_column = Task.due_date
    elif sort == "priority":
        sort_column = Task.priority
    elif sort == "created":
        sort_column = Task.created_at
    query = query.order_by(sort_column.desc().nullslast() if order.lower() == "desc" else sort_column.asc().nullslast())

    # Execute query to retrieve tasks.
    res = await db.execute(query)
    tasks = res.scalars().all()

    # Generate timestamped filename.
    today_str = date.today().isoformat()

    # Log export activity.
    await log_activity(
        db=db,
        actor=current_user,
        action="exported",
        entity_type="export",
        entity_id=None,
        detail={"format": format, "count": len(tasks)},
    )
    await db.commit()

    if format == "csv":
        filename = f"tasks_{today_str}.csv"
        return StreamingResponse(
            stream_tasks_csv(tasks),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        filename = f"tasks_{today_str}.json"
        return StreamingResponse(
            stream_tasks_json(tasks),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.post(
    "/import",
    response_model=ImportReport,
    summary="Import tasks from CSV or JSON file",
    description="Validates and imports tasks from an uploaded file in a single transaction with dry-run support.",
)
async def import_tasks(
    file: UploadFile = File(..., description="Uploaded CSV or JSON file (max 2 MB)"),
    on_duplicate: DuplicateStrategy = Form(default=DuplicateStrategy.SKIP, description="Duplicate title policy"),
    dry_run: bool = Form(default=False, description="Simulate import without committing"),
    target_user_id: int | None = Form(default=None, description="Admin: target user ID for imported tasks"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ImportReport:
    """Import tasks from uploaded file.

    Parameters:
        file (UploadFile): The file payload.
        on_duplicate (DuplicateStrategy): Policy for handling duplicates.
        dry_run (bool): Whether to perform a dry run.
        target_user_id (int | None): Admin-only target user override.
        db (AsyncSession): Active database session.
        current_user (User): Requesting user.

    Returns:
        ImportReport: Summary of import results and any row errors.

    Raises:
        HTTPException(413): If file exceeds max_import_bytes (2 MB).
        HTTPException(415): If file extension or MIME type is not CSV or JSON.
        HTTPException(422): If file parsing encounters unrecoverable structural syntax errors.
    """
    # Verify file name and extension.
    filename = file.filename or ""
    lower_filename = filename.lower()
    is_csv = lower_filename.endswith(".csv")
    is_json = lower_filename.endswith(".json")

    if not (is_csv or is_json):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file extension. Only .csv and .json files are allowed.",
        )

    # Read file content into memory with size guard.
    content_bytes = await file.read()
    if len(content_bytes) > settings.max_import_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {settings.max_import_bytes // (1024 * 1024)} MB.",
        )

    # Decode bytes to text.
    try:
        content_str = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File content is not valid UTF-8 text.",
        )

    # Parse rows according to format.
    if is_csv:
        valid_rows, parse_errors = parse_csv_content(content_str)
    else:
        valid_rows, parse_errors = parse_json_content(content_str)

    # If parsing produced fatal errors and no valid rows, return report with failures.
    if parse_errors and not valid_rows:
        return ImportReport(
            total_rows=len(parse_errors),
            valid_rows=0,
            invalid_rows=len(parse_errors),
            imported_rows=0,
            skipped_rows=0,
            overwritten_rows=0,
            dry_run=dry_run,
            errors=parse_errors,
            message="Import failed: file contains parsing errors.",
        )

    # Resolve target user.
    effective_user_id = current_user.id
    if current_user.role == Role.ADMIN and target_user_id is not None:
        # Verify target user exists.
        chk = await db.execute(select(User).where(User.id == target_user_id))
        target_usr = chk.scalars().first()
        if not target_usr:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target user with ID {target_user_id} not found.",
            )
        effective_user_id = target_user_id

    # Execute import options.
    options = ImportOptions(
        on_duplicate=on_duplicate,
        target_user_id=effective_user_id,
        dry_run=dry_run,
    )

    # Run atomic import.
    report = await execute_task_import(
        db=db,
        target_user_id=effective_user_id,
        rows=valid_rows,
        options=options,
    )

    # Append any initial parsing errors to the report.
    report.errors.extend(parse_errors)
    report.invalid_rows += len(parse_errors)
    report.total_rows += len(parse_errors)

    # Commit transaction if not a dry-run.
    if not dry_run:
        await db.commit()

        # Log import activity.
        await log_activity(
            db=db,
            actor=current_user,
            action="imported",
            entity_type="import",
            entity_id=None,
            detail={
                "imported": report.imported_rows,
                "overwritten": report.overwritten_rows,
                "skipped": report.skipped_rows,
            },
        )
        await db.commit()

    return report


@router.get(
    "/templates/csv",
    summary="Download CSV sample template",
    description="Returns a sample CSV template with valid column headers and rows.",
)
async def download_csv_template() -> Response:
    """Return downloadable CSV sample template.

    Returns:
        Response: CSV content with attachment header.
    """
    csv_text = generate_csv_template()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="taskflow_template.csv"'},
    )


@router.get(
    "/templates/json",
    summary="Download JSON sample template",
    description="Returns a sample JSON template with valid task objects.",
)
async def download_json_template() -> Response:
    """Return downloadable JSON sample template.

    Returns:
        Response: JSON content with attachment header.
    """
    json_text = generate_json_template()
    return Response(
        content=json_text,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="taskflow_template.json"'},
    )
