# ============================================================================
# FILE:       backend/app/schemas/transfer.py
# PURPOSE:    Pydantic schemas for task CSV/JSON import and export operations.
#             Defines row structure, duplicate handling strategies, and
#             import report summaries.
# HOW IT WORKS: Used during file upload validation, dry-run simulations, and
#             execution reports. Translates external flat row data into
#             validated domain models.
# USED BY / DEPENDS ON: routers/transfer.py, services/transfer.py, schemas/__init__.py.
# KEY CONCEPTS: Bulk data validation, dry-run reporting, duplicate resolution
#             policies (skip / overwrite / create).
# ============================================================================

"""Schemas for importing and exporting tasks (CSV and JSON)."""

# date handles task deadline dates in YYYY-MM-DD format.
from datetime import date

# StrEnum for duplicate resolution options.
from enum import StrEnum

# BaseModel and Field from Pydantic v2.
from pydantic import BaseModel, Field, field_validator

# Shared enums for priority and status validation.
from app.schemas.common import Priority, Status


class DuplicateStrategy(StrEnum):
    """How to resolve collisions when an imported task title matches an existing task.

    Members:
        SKIP: Ignore the incoming duplicate row; keep existing task unchanged.
        OVERWRITE: Update the existing task with fields from the imported row.
        CREATE: Create an additional task row with the same title.
    """

    # Skip incoming duplicate row.
    SKIP = "skip"

    # Overwrite existing task with incoming row data.
    OVERWRITE = "overwrite"

    # Create a new task anyway, allowing duplicate titles.
    CREATE = "create"


class ImportOptions(BaseModel):
    """Options submitted alongside the uploaded file during import.

    Attributes:
        on_duplicate (DuplicateStrategy): Policy for handling duplicate titles.
        target_user_id (int | None): Admin-only override for task ownership.
        dry_run (bool): If True, validates without committing changes to DB.
    """

    # on_duplicate: Strategy enum (skip, overwrite, create).
    on_duplicate: DuplicateStrategy = Field(
        default=DuplicateStrategy.SKIP,
        description="Duplicate handling mode ('skip', 'overwrite', or 'create').",
    )

    # target_user_id: Admin can import tasks on behalf of another user.
    target_user_id: int | None = Field(
        default=None,
        description="Target user ID (Admin only; ignored for regular users).",
    )

    # dry_run: If True, performs validation and duplicate count without DB writes.
    dry_run: bool = Field(
        default=False,
        description="If True, test validation and duplicate counts without saving.",
    )


class ImportRow(BaseModel):
    """Schema representing one validated row extracted from CSV or JSON.

    Attributes:
        title (str): Task title (1-200 characters).
        description (str | None): Task details.
        priority (Priority): Urgency level.
        status (Status): Current workflow status.
        due_date (date | None): Optional deadline.
        tags (list[str]): List of tags.
        category (str | None): Optional category string.
        subtasks (list[str]): List of subtask titles extracted from row.
    """

    # title: Required title string.
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Task title (1-200 characters).",
    )

    # description: Optional text.
    description: str | None = Field(
        default=None,
        description="Optional detailed description.",
    )

    # priority: Defaults to low.
    priority: Priority = Field(
        default=Priority.LOW,
        description="Priority: 'low', 'medium', or 'high'.",
    )

    # status: Defaults to pending.
    status: Status = Field(
        default=Status.PENDING,
        description="Status: 'pending', 'in_progress', or 'completed'.",
    )

    # due_date: Deadline.
    due_date: date | None = Field(
        default=None,
        description="Deadline in YYYY-MM-DD format.",
    )

    # tags: List of tag strings.
    tags: list[str] = Field(
        default_factory=list,
        description="List of tag strings.",
    )

    # category: Optional category.
    category: str | None = Field(
        default=None,
        max_length=60,
        description="Optional category name.",
    )

    # subtasks: Checklist item titles.
    subtasks: list[str] = Field(
        default_factory=list,
        description="List of checklist item titles.",
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        """Strip surrounding spaces and reject empty titles.

        Parameters:
            value (str): Raw title string.

        Returns:
            str: Trimmed string.

        Raises:
            ValueError: If empty after trimming.
        """
        # Trim whitespace.
        trimmed = value.strip()
        # Verify non-empty.
        if not trimmed:
            # Raise error.
            raise ValueError("Task title cannot be empty.")
        # Return clean title.
        return trimmed


class ImportRowError(BaseModel):
    """Represents an error encountered on a specific row during import.

    Attributes:
        row_number (int): 1-indexed line number in the source file.
        title (str | None): The title parsed from the row, if any.
        reason (str): Human-readable explanation of why the row failed.
    """

    # row_number: File line index (1-based for user readability).
    row_number: int = Field(..., description="1-indexed row number in the file.")

    # title: Title read from the row, if present.
    title: str | None = Field(default=None, description="Title from the row.")

    # reason: Description of the validation failure.
    reason: str = Field(..., description="Explanation of why this row failed.")


class ImportReport(BaseModel):
    """Complete summary report returned after an import or dry-run.

    Attributes:
        total_rows (int): Total records parsed from the file.
        valid_rows (int): Count of rows that passed validation.
        invalid_rows (int): Count of rows that failed validation.
        imported_rows (int): Rows successfully inserted or updated.
        skipped_rows (int): Rows skipped due to duplicate policy.
        overwritten_rows (int): Existing rows overwritten by duplicates.
        dry_run (bool): Whether this was a simulation.
        errors (list[ImportRowError]): Detailed list of row errors.
        message (str): Summary status message.
    """

    # total_rows: Number of rows in source file.
    total_rows: int = Field(..., description="Total rows parsed.")

    # valid_rows: Number of rows passing validation rules.
    valid_rows: int = Field(..., description="Number of valid rows.")

    # invalid_rows: Number of invalid rows.
    invalid_rows: int = Field(..., description="Number of invalid rows.")

    # imported_rows: Number of rows written to database.
    imported_rows: int = Field(..., description="Rows created in the database.")

    # skipped_rows: Rows skipped per duplicate policy.
    skipped_rows: int = Field(..., description="Duplicate rows skipped.")

    # overwritten_rows: Rows updated per duplicate policy.
    overwritten_rows: int = Field(..., description="Existing rows updated.")

    # dry_run: Echo of the dry_run parameter.
    dry_run: bool = Field(..., description="Whether this was a simulation.")

    # errors: List of validation failures.
    errors: list[ImportRowError] = Field(
        default_factory=list,
        description="Detailed list of failed rows.",
    )

    # message: Human-readable summary string.
    message: str = Field(..., description="Human-readable result summary.")
