# ============================================================================
# FILE:       backend/app/services/transfer.py
# PURPOSE:    Core business logic for CSV and JSON import/export of tasks.
#             Implements CSV formula injection mitigation, flat CSV formatting,
#             JSON streaming, and single-transaction atomic batch import.
# HOW IT WORKS:
#   - Export formats tasks into sanitized CSV lines or indented JSON trees.
#   - Import parses CSV/JSON, validates against Pydantic schemas, detects duplicate
#     titles per user, and executes skip/overwrite/create policies in a single DB transaction.
#   - Protects spreadsheets against CSV Injection (Formula Injection) by prefixing
#     dangerous formula trigger characters (=, +, -, @) with a single quote.
# USED BY / DEPENDS ON: routers/transfer.py, models/task.py, models/subtask.py.
# KEY CONCEPTS: CSV formula injection defense, StreamingResponse generators,
#             atomic transaction rollback, duplicate reconciliation.
# ============================================================================

"""Service functions for task import and export (CSV and JSON)."""

# csv provides standard CSV parsing and formatting routines.
import csv

# datetime and date for date parsing and export timestamp formatting.
from datetime import date, datetime

# io.StringIO allows streaming in-memory string buffers for CSV generation.
import io

# json provides serialization and deserialization for JSON import/export.
import json

# typing annotations for generators, collections, and callables.
from typing import AsyncGenerator, Sequence

# AsyncSession is the transaction-managed database session.
from sqlalchemy.ext.asyncio import AsyncSession

# select, and_ for querying database tasks.
from sqlalchemy import select

# ORM models for Task and Subtask.
from app.models.task import Task
from app.models.subtask import Subtask
from app.models.user import User

# Pydantic schemas for transfer operations.
from app.schemas.transfer import (
    DuplicateStrategy,
    ImportOptions,
    ImportReport,
    ImportRow,
    ImportRowError,
)
from app.schemas.common import Priority, Status


def sanitize_csv_cell(value: str | None) -> str:
    """Protect against CSV Formula Injection (Formula Injection attacks).

    If a cell value begins with '=', '+', '-', or '@', spreadsheet applications
    like Microsoft Excel or LibreOffice Calc treat the string as an executable
    macro formula. We prefix dangerous characters with a single quote (') to force
    spreadsheets to treat the cell as raw static text.

    Parameters:
        value (str | None): The raw text to write into the cell.

    Returns:
        str: The sanitized text safe for spreadsheet export.

    SECURITY:
        Prevents Remote Code Execution (RCE) and data exfiltration vulnerabilities
        when users export data and open it in desktop spreadsheet applications.
    """
    # Return empty string if the value is None.
    if value is None:
        return ""

    # Ensure value is treated as a string.
    val_str = str(value)

    # Check if the string begins with any known formula injection trigger symbol.
    if val_str and val_str[0] in ("=", "+", "-", "@"):
        # Prefix with a single quote to escape formula interpretation.
        return f"'{val_str}"

    # Return safe value unmodified.
    return val_str


def generate_csv_template() -> str:
    """Generate a valid sample CSV template for download.

    Returns:
        str: CSV string containing header and example rows.
    """
    # Create in-memory string stream.
    output = io.StringIO()
    # Instantiate standard CSV writer.
    writer = csv.writer(output)

    # Write column headers.
    writer.writerow(["title", "description", "priority", "status", "due_date", "tags", "category", "subtasks"])

    # Write sample row 1.
    writer.writerow([
        "Complete Project Documentation",
        "Document architecture, database schema, and API endpoints",
        "high",
        "in_progress",
        "2026-10-30",
        "docs;backend",
        "Engineering",
        "Write architecture doc;Review database ER diagram",
    ])

    # Write sample row 2.
    writer.writerow([
        "Refactor Frontend Components",
        "Apply glassmorphic styling and accessibility focus rings",
        "medium",
        "pending",
        "2026-11-05",
        "ui;frontend",
        "Design",
        "Audit contrast ratios;Add keyboard listeners",
    ])

    # Return string output.
    return output.getvalue()


def generate_json_template() -> str:
    """Generate a valid sample JSON template for download.

    Returns:
        str: Formatted JSON string containing example task objects.
    """
    # Example task array.
    sample_data = [
        {
            "title": "Complete Project Documentation",
            "description": "Document architecture, database schema, and API endpoints",
            "priority": "high",
            "status": "in_progress",
            "due_date": "2026-10-30",
            "tags": ["docs", "backend"],
            "category": "Engineering",
            "subtasks": ["Write architecture doc", "Review database ER diagram"],
        },
        {
            "title": "Refactor Frontend Components",
            "description": "Apply glassmorphic styling and accessibility focus rings",
            "priority": "medium",
            "status": "pending",
            "due_date": "2026-11-05",
            "tags": ["ui", "frontend"],
            "category": "Design",
            "subtasks": ["Audit contrast ratios", "Add keyboard listeners"],
        },
    ]
    # Return formatted JSON string.
    return json.dumps(sample_data, indent=2)


async def stream_tasks_csv(tasks: Sequence[Task]) -> AsyncGenerator[str, None]:
    """Stream tasks formatted as CSV rows with formula sanitization.

    Parameters:
        tasks (Sequence[Task]): The sequence of Task ORM objects to export.

    Yields:
        str: Chunks of CSV lines for streaming HTTP responses.
    """
    # Buffer to hold rendered CSV text.
    buffer = io.StringIO()
    # Create CSV writer.
    writer = csv.writer(buffer)

    # Write header line.
    writer.writerow(["title", "description", "priority", "status", "due_date", "tags", "category", "subtasks"])
    # Yield header chunk.
    yield buffer.getvalue()
    # Reset buffer for next line.
    buffer.seek(0)
    buffer.truncate(0)

    # Iterate over each task.
    for task in tasks:
        # Join tags with semicolons.
        tags_str = ";".join(task.tags) if task.tags else ""

        # Join subtask titles with semicolons.
        subtasks_str = ";".join(st.title for st in task.subtasks) if task.subtasks else ""

        # Format due date as YYYY-MM-DD or empty string.
        due_date_str = task.due_date.isoformat() if task.due_date else ""

        # Construct sanitized row list.
        row = [
            sanitize_csv_cell(task.title),
            sanitize_csv_cell(task.description),
            sanitize_csv_cell(task.priority),
            sanitize_csv_cell(task.status),
            sanitize_csv_cell(due_date_str),
            sanitize_csv_cell(tags_str),
            sanitize_csv_cell(task.category),
            sanitize_csv_cell(subtasks_str),
        ]

        # Write row to buffer.
        writer.writerow(row)
        # Yield row chunk.
        yield buffer.getvalue()
        # Reset buffer.
        buffer.seek(0)
        buffer.truncate(0)


async def stream_tasks_json(tasks: Sequence[Task]) -> AsyncGenerator[str, None]:
    """Stream tasks formatted as a structured JSON array.

    Parameters:
        tasks (Sequence[Task]): The sequence of Task ORM objects to export.

    Yields:
        str: JSON chunks forming a complete valid JSON array.
    """
    # Yield opening bracket for JSON array.
    yield "[\n"

    # Count tasks for comma separation.
    total = len(tasks)

    # Iterate with index over tasks.
    for idx, task in enumerate(tasks):
        # Format subtasks list of objects.
        subtasks_data = [
            {
                "title": st.title,
                "is_done": st.is_done,
                "position": st.position,
            }
            for st in task.subtasks
        ]

        # Format comments list of objects.
        comments_data = [
            {
                "author_id": c.user_id,
                "author_name": c.user.name if c.user else "Unknown",
                "body": c.body,
                "is_edited": c.is_edited,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in task.comments
        ]

        # Build task dictionary.
        task_dict = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "status": task.status,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "tags": task.tags,
            "category": task.category,
            "position": task.position,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "subtasks": subtasks_data,
            "comments": comments_data,
        }

        # Serialize task object with indentation.
        serialized = json.dumps(task_dict, indent=2)

        # Indent each line by two spaces.
        indented = "\n".join("  " + line for line in serialized.splitlines())

        # Add comma if not the final item.
        if idx < total - 1:
            indented += ",\n"
        else:
            indented += "\n"

        # Yield task chunk.
        yield indented

    # Yield closing array bracket.
    yield "]\n"


def parse_csv_content(content_str: str) -> tuple[list[ImportRow], list[ImportRowError]]:
    """Parse raw CSV text into a list of validated ImportRow objects and errors.

    Parameters:
        content_str (str): Raw string content of the uploaded CSV file.

    Returns:
        tuple[list[ImportRow], list[ImportRowError]]: Valid rows and error reports.
    """
    # List to store parsed valid rows.
    valid_rows: list[ImportRow] = []
    # List to store errors encountered per row.
    errors: list[ImportRowError] = []

    # Read CSV via StringIO and DictReader.
    stream = io.StringIO(content_str)
    # Instantiate DictReader to map columns by name.
    reader = csv.DictReader(stream)

    # If header is missing or empty, record error.
    if not reader.fieldnames:
        errors.append(
            ImportRowError(
                row_number=1,
                title=None,
                reason="CSV file is empty or missing header row.",
            )
        )
        return valid_rows, errors

    # Check for required title column.
    normalized_fields = {fn.strip().lower(): fn for fn in reader.fieldnames if fn}
    if "title" not in normalized_fields:
        errors.append(
            ImportRowError(
                row_number=1,
                title=None,
                reason="CSV header missing required 'title' column.",
            )
        )
        return valid_rows, errors

    # Enumerate over each row in the CSV file (2-indexed accounting for header).
    for row_idx, raw_row in enumerate(reader, start=2):
        # Extract title field.
        raw_title = raw_row.get(normalized_fields["title"], "").strip()
        # If title starts with formula quote escape, remove it for importing.
        if raw_title.startswith("'"):
            raw_title = raw_title[1:]

        # Validate title presence.
        if not raw_title:
            errors.append(
                ImportRowError(
                    row_number=row_idx,
                    title=None,
                    reason="Row title is required and cannot be empty.",
                )
            )
            continue

        # Parse description.
        desc_key = normalized_fields.get("description")
        desc_val = raw_row.get(desc_key, "").strip() if desc_key else None
        if desc_val and desc_val.startswith("'"):
            desc_val = desc_val[1:]

        # Parse priority.
        prio_key = normalized_fields.get("priority")
        prio_val = raw_row.get(prio_key, "low").strip().lower() if prio_key else "low"
        if prio_val not in ("low", "medium", "high"):
            prio_val = "low"

        # Parse status.
        status_key = normalized_fields.get("status")
        status_val = raw_row.get(status_key, "pending").strip().lower() if status_key else "pending"
        if status_val not in ("pending", "in_progress", "completed"):
            status_val = "pending"

        # Parse due date.
        due_key = normalized_fields.get("due_date")
        parsed_due_date: date | None = None
        if due_key and raw_row.get(due_key):
            due_str = raw_row[due_key].strip()
            if due_str:
                try:
                    parsed_due_date = date.fromisoformat(due_str)
                except ValueError:
                    errors.append(
                        ImportRowError(
                            row_number=row_idx,
                            title=raw_title,
                            reason=f"Invalid due_date format '{due_str}'. Expected YYYY-MM-DD.",
                        )
                    )
                    continue

        # Parse tags (semicolon-separated).
        tags_key = normalized_fields.get("tags")
        parsed_tags: list[str] = []
        if tags_key and raw_row.get(tags_key):
            raw_tags = raw_row[tags_key].strip()
            if raw_tags:
                parsed_tags = [t.strip() for t in raw_tags.split(";") if t.strip()]

        # Parse category.
        cat_key = normalized_fields.get("category")
        cat_val = raw_row.get(cat_key, "").strip() if cat_key else None
        if cat_val == "":
            cat_val = None

        # Parse subtasks (semicolon-separated titles).
        sub_key = normalized_fields.get("subtasks")
        parsed_subtasks: list[str] = []
        if sub_key and raw_row.get(sub_key):
            raw_sub = raw_row[sub_key].strip()
            if raw_sub:
                parsed_subtasks = [s.strip() for s in raw_sub.split(";") if s.strip()]

        # Construct validated ImportRow object.
        row_obj = ImportRow(
            title=raw_title,
            description=desc_val if desc_val else None,
            priority=Priority(prio_val),
            status=Status(status_val),
            due_date=parsed_due_date,
            tags=parsed_tags,
            category=cat_val,
            subtasks=parsed_subtasks,
        )

        # Add to valid list.
        valid_rows.append(row_obj)

    # Return parsed results.
    return valid_rows, errors


def parse_json_content(content_str: str) -> tuple[list[ImportRow], list[ImportRowError]]:
    """Parse raw JSON text into a list of validated ImportRow objects and errors.

    Parameters:
        content_str (str): Raw string content of the uploaded JSON file.

    Returns:
        tuple[list[ImportRow], list[ImportRowError]]: Valid rows and error reports.
    """
    # Lists for valid rows and errors.
    valid_rows: list[ImportRow] = []
    errors: list[ImportRowError] = []

    # Attempt to decode JSON string.
    try:
        raw_data = json.loads(content_str)
    except json.JSONDecodeError as err:
        errors.append(
            ImportRowError(
                row_number=1,
                title=None,
                reason=f"Invalid JSON syntax: {err.msg} at line {err.lineno}.",
            )
        )
        return valid_rows, errors

    # Expect a JSON array of objects.
    if not isinstance(raw_data, list):
        errors.append(
            ImportRowError(
                row_number=1,
                title=None,
                reason="JSON root must be an array of task objects.",
            )
        )
        return valid_rows, errors

    # Iterate over items in the list.
    for idx, item in enumerate(raw_data, start=1):
        if not isinstance(item, dict):
            errors.append(
                ImportRowError(
                    row_number=idx,
                    title=None,
                    reason=f"Item at index {idx} must be a JSON object.",
                )
            )
            continue

        # Extract title.
        raw_title = str(item.get("title", "")).strip()
        if not raw_title:
            errors.append(
                ImportRowError(
                    row_number=idx,
                    title=None,
                    reason="Task title is required and cannot be empty.",
                )
            )
            continue

        # Extract description.
        desc_val = item.get("description")
        if desc_val is not None:
            desc_val = str(desc_val).strip()

        # Extract priority.
        prio_val = str(item.get("priority", "low")).strip().lower()
        if prio_val not in ("low", "medium", "high"):
            prio_val = "low"

        # Extract status.
        status_val = str(item.get("status", "pending")).strip().lower()
        if status_val not in ("pending", "in_progress", "completed"):
            status_val = "pending"

        # Extract due date.
        due_val = item.get("due_date")
        parsed_due_date: date | None = None
        if due_val:
            try:
                parsed_due_date = date.fromisoformat(str(due_val).strip())
            except ValueError:
                errors.append(
                    ImportRowError(
                        row_number=idx,
                        title=raw_title,
                        reason=f"Invalid due_date format '{due_val}'. Expected YYYY-MM-DD.",
                    )
                )
                continue

        # Extract tags.
        tags_raw = item.get("tags", [])
        parsed_tags: list[str] = []
        if isinstance(tags_raw, list):
            parsed_tags = [str(t).strip() for t in tags_raw if str(t).strip()]

        # Extract category.
        cat_val = item.get("category")
        if cat_val is not None:
            cat_val = str(cat_val).strip()
            if not cat_val:
                cat_val = None

        # Extract subtasks (can be list of strings or list of objects).
        subtasks_raw = item.get("subtasks", [])
        parsed_subtasks: list[str] = []
        if isinstance(subtasks_raw, list):
            for st in subtasks_raw:
                if isinstance(st, dict) and "title" in st:
                    st_title = str(st["title"]).strip()
                    if st_title:
                        parsed_subtasks.append(st_title)
                elif isinstance(st, str) and st.strip():
                    parsed_subtasks.append(st.strip())

        # Construct row object.
        row_obj = ImportRow(
            title=raw_title,
            description=desc_val,
            priority=Priority(prio_val),
            status=Status(status_val),
            due_date=parsed_due_date,
            tags=parsed_tags,
            category=cat_val,
            subtasks=parsed_subtasks,
        )

        # Append to valid rows.
        valid_rows.append(row_obj)

    # Return results.
    return valid_rows, errors


async def execute_task_import(
    db: AsyncSession,
    target_user_id: int,
    rows: list[ImportRow],
    options: ImportOptions,
) -> ImportReport:
    """Execute import within a single transaction with duplicate policy enforcement.

    Parameters:
        db (AsyncSession): Active database session.
        target_user_id (int): Target user who will own the imported tasks.
        rows (list[ImportRow]): Validated rows to import.
        options (ImportOptions): Import strategy and dry-run flag.

    Returns:
        ImportReport: Execution report summarizing created, updated, and skipped counts.
    """
    # Fetch all existing tasks for the target user to identify duplicates by title.
    stmt = select(Task).where(Task.user_id == target_user_id, Task.deleted_at.is_(None))
    res = await db.execute(stmt)
    existing_tasks = res.scalars().all()

    # Map existing active tasks by lowercased title for fast collision checks.
    existing_map: dict[str, Task] = {t.title.lower(): t for t in existing_tasks}

    # Tracking counters.
    total_rows = len(rows)
    imported_count = 0
    skipped_count = 0
    overwritten_count = 0

    # If dry_run is True, calculate counts without persisting modifications.
    if options.dry_run:
        for row in rows:
            lower_title = row.title.lower()
            if lower_title in existing_map:
                if options.on_duplicate == DuplicateStrategy.SKIP:
                    skipped_count += 1
                elif options.on_duplicate == DuplicateStrategy.OVERWRITE:
                    overwritten_count += 1
                else:  # CREATE
                    imported_count += 1
            else:
                imported_count += 1

        return ImportReport(
            total_rows=total_rows,
            valid_rows=total_rows,
            invalid_rows=0,
            imported_rows=imported_count,
            skipped_rows=skipped_count,
            overwritten_rows=overwritten_count,
            dry_run=True,
            errors=[],
            message=(
                f"Dry run complete: {total_rows} rows evaluated. "
                f"{imported_count} would be imported, {overwritten_count} overwritten, {skipped_count} skipped."
            ),
        )

    # Real import: execute changes.
    for row in rows:
        lower_title = row.title.lower()

        # Handle duplicate title collision.
        if lower_title in existing_map:
            if options.on_duplicate == DuplicateStrategy.SKIP:
                skipped_count += 1
                continue

            elif options.on_duplicate == DuplicateStrategy.OVERWRITE:
                existing_task = existing_map[lower_title]
                existing_task.description = row.description
                existing_task.priority = row.priority.value
                existing_task.status = row.status.value
                existing_task.due_date = row.due_date
                existing_task.tags = row.tags
                existing_task.category = row.category

                # If incoming row specifies subtasks, replace existing checklist.
                if row.subtasks:
                    existing_task.subtasks.clear()
                    for idx, st_title in enumerate(row.subtasks):
                        subtask = Subtask(
                            task_id=existing_task.id,
                            title=st_title,
                            is_done=False,
                            position=idx,
                        )
                        existing_task.subtasks.append(subtask)

                overwritten_count += 1
                continue

        # Create brand new task.
        new_task = Task(
            user_id=target_user_id,
            title=row.title,
            description=row.description,
            priority=row.priority.value,
            status=row.status.value,
            due_date=row.due_date,
            tags=row.tags,
            category=row.category,
            position=float(len(existing_map) + imported_count + 1),
        )
        db.add(new_task)
        await db.flush()

        # Add subtasks if provided.
        for idx, st_title in enumerate(row.subtasks):
            subtask = Subtask(
                task_id=new_task.id,
                title=st_title,
                is_done=False,
                position=idx,
            )
            db.add(subtask)

        # Update map to track newly inserted title in case of intra-batch duplicates.
        existing_map[lower_title] = new_task
        imported_count += 1

    # Flush all changes to database.
    await db.flush()

    # Construct final report.
    return ImportReport(
        total_rows=total_rows,
        valid_rows=total_rows,
        invalid_rows=0,
        imported_rows=imported_count,
        skipped_rows=skipped_count,
        overwritten_rows=overwritten_count,
        dry_run=False,
        errors=[],
        message=(
            f"Import successful: {imported_count} tasks imported, "
            f"{overwritten_count} overwritten, {skipped_count} skipped."
        ),
    )
