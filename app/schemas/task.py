# ============================================================================
# FILE:       backend/app/schemas/task.py
# PURPOSE:    Define Pydantic v2 schemas for tasks and nested subtasks.
#             Covers creation, updating, reading, filtering, and reordering.
# HOW IT WORKS: Validates incoming payloads (title length, date types, enums)
#             and structures JSON responses. From_attributes=True enables
#             direct serialization from SQLAlchemy models.
# USED BY / DEPENDS ON: routers/tasks.py, seed.py, schemas/__init__.py, tests.
# KEY CONCEPTS: Nested models, schema inheritance, optional updates,
#             drag-and-drop reorder payload, computed properties.
# ============================================================================

"""Pydantic schemas for tasks and checklist subtasks."""

# date and datetime represent calendar deadlines and UTC audit timestamps.
from datetime import date, datetime

# BaseModel is the foundation of Pydantic v2; Field configures constraints.
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared enums for priority and workflow status.
from app.schemas.common import Priority, Status


class SubtaskBase(BaseModel):
    """Base schema holding common fields for checklist subtasks.

    Attributes:
        title (str): Title of the subtask (1-200 characters).
        is_done (bool): Whether the subtask checklist item is completed.
        position (int): Relative display position in the checklist.
    """

    # title: Subtask text (1 to 200 characters).
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Checklist item text (1-200 characters).",
        examples=["Write unit tests"],
    )

    # is_done: Checked state (default False).
    is_done: bool = Field(
        default=False,
        description="Whether this checklist item is completed.",
    )

    # position: Integer position index for ordering checklist items.
    position: int = Field(
        default=0,
        description="Display order within the checklist.",
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        """Trim whitespace and verify the title is not empty.

        Parameters:
            value (str): Raw string.

        Returns:
            str: Trimmed string.

        Raises:
            ValueError: If empty after trimming.
        """
        # Trim leading and trailing spaces.
        trimmed = value.strip()
        # Verify non-empty content.
        if not trimmed:
            # Reject blank titles.
            raise ValueError("Subtask title cannot be blank.")
        # Return sanitized title.
        return trimmed


class SubtaskCreate(SubtaskBase):
    """Schema for creating a new subtask checklist item."""

    # Inherits title, is_done, position.
    pass


class SubtaskUpdate(BaseModel):
    """Schema for updating an existing subtask.

    Attributes:
        title (str | None): Optional updated text.
        is_done (bool | None): Optional updated completion state.
        position (int | None): Optional updated ordering position.
    """

    # title: Optional updated text.
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Updated checklist text.",
    )

    # is_done: Optional updated completion status.
    is_done: bool | None = Field(
        default=None,
        description="Updated completion state.",
    )

    # position: Optional updated order index.
    position: int | None = Field(
        default=None,
        description="Updated display order.",
    )


class SubtaskRead(SubtaskBase):
    """Schema returned when reading a subtask.

    Attributes:
        id (int): Primary key ID.
        task_id (int): Foreign key to the parent task.
        created_at (datetime): UTC creation timestamp.
    """

    # id: Subtask unique identifier.
    id: int = Field(..., description="Unique subtask identifier.")

    # task_id: Identifier of the parent task.
    task_id: int = Field(..., description="Parent task identifier.")

    # created_at: Creation timestamp in UTC.
    created_at: datetime = Field(..., description="UTC creation timestamp.")

    # model_config: Enables ORM attribute mapping.
    model_config = ConfigDict(from_attributes=True)


class TaskBase(BaseModel):
    """Base schema holding common fields for a task.

    Attributes:
        title (str): Short summary (1-200 characters).
        description (str | None): Optional markdown or text details.
        priority (Priority): Importance level (low, medium, high).
        status (Status): Workflow status (pending, in_progress, completed).
        due_date (date | None): Optional deadline date (YYYY-MM-DD).
        tags (list[str]): List of tags/labels.
        category (str | None): Single optional category name (e.g. 'Work').
    """

    # title: Card header text.
    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Short task title (1-200 characters).",
        examples=["Implement role-based authorization"],
    )

    # description: Long markdown or plain text details.
    description: str | None = Field(
        default=None,
        description="Optional detailed description of the task.",
    )

    # priority: Low, medium, or high urgency.
    priority: Priority = Field(
        default=Priority.LOW,
        description="Task priority level ('low', 'medium', 'high').",
    )

    # status: Workflow state.
    status: Status = Field(
        default=Status.PENDING,
        description="Workflow state ('pending', 'in_progress', 'completed').",
    )

    # due_date: Deadline date without time component.
    due_date: date | None = Field(
        default=None,
        description="Optional deadline date in YYYY-MM-DD format.",
    )

    # tags: Array of label strings.
    tags: list[str] = Field(
        default_factory=list,
        description="Array of tags associated with the task.",
        examples=[["frontend", "ui"]],
    )

    # category: Single grouping identifier.
    category: str | None = Field(
        default=None,
        max_length=60,
        description="Optional category name.",
        examples=["Development"],
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        """Trim surrounding whitespace and verify non-empty content.

        Parameters:
            value (str): Raw task title.

        Returns:
            str: Trimmed title.

        Raises:
            ValueError: If empty after trimming.
        """
        # Strip leading and trailing whitespace.
        trimmed = value.strip()
        # Verify non-empty string.
        if not trimmed:
            # Reject empty title.
            raise ValueError("Task title cannot be empty or purely whitespace.")
        # Return cleaned title.
        return trimmed

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        """Normalize tags by stripping whitespace and removing duplicates.

        Parameters:
            value (list[str]): Raw list of tags.

        Returns:
            list[str]: Cleaned unique list of tags.
        """
        # Set to track seen tags and preserve insertion order.
        seen: set[str] = set()
        # Output list for clean tags.
        cleaned: list[str] = []
        # Iterate over each raw tag item.
        for tag in value:
            # Strip whitespace and convert to lowercase for consistency.
            t = tag.strip().lower()
            # If non-empty and not seen yet, add to result.
            if t and t not in seen:
                # Mark as seen.
                seen.add(t)
                # Append to cleaned list.
                cleaned.append(t)
        # Return normalized list.
        return cleaned


class TaskCreate(TaskBase):
    """Schema for creating a new task.

    Attributes:
        position (float): Optional position index (defaults to 0.0).
        subtasks (list[SubtaskCreate]): Optional initial checklist items.
    """

    # position: Fractional position index for manual ordering.
    position: float = Field(
        default=0.0,
        description="Position value for manual drag-and-drop order.",
    )

    # subtasks: Nested list of subtask items to create atomically.
    subtasks: list[SubtaskCreate] = Field(
        default_factory=list,
        description="Optional list of checklist subtasks to create with the task.",
    )


class TaskUpdate(BaseModel):
    """Schema for partially updating a task (all fields optional).

    Attributes:
        title (str | None): Updated title.
        description (str | None): Updated description.
        priority (Priority | None): Updated priority.
        status (Status | None): Updated status.
        due_date (date | None): Updated deadline date.
        tags (list[str] | None): Updated tags.
        category (str | None): Updated category.
        position (float | None): Updated position.
    """

    # title: Optional new title.
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Updated task title.",
    )

    # description: Optional new description.
    description: str | None = Field(
        default=None,
        description="Updated description text.",
    )

    # priority: Optional new priority.
    priority: Priority | None = Field(
        default=None,
        description="Updated priority level.",
    )

    # status: Optional new status.
    status: Status | None = Field(
        default=None,
        description="Updated workflow state.",
    )

    # due_date: Optional new deadline.
    due_date: date | None = Field(
        default=None,
        description="Updated deadline date.",
    )

    # tags: Optional new tag list.
    tags: list[str] | None = Field(
        default=None,
        description="Updated list of tags.",
    )

    # category: Optional new category.
    category: str | None = Field(
        default=None,
        max_length=60,
        description="Updated category label.",
    )

    # position: Optional new drag-and-drop position.
    position: float | None = Field(
        default=None,
        description="Updated drag-and-drop position.",
    )

    @field_validator("title")
    @classmethod
    def _strip_title_if_set(cls, value: str | None) -> str | None:
        """Trim title if provided.

        Parameters:
            value (str | None): Title string or None.

        Returns:
            str | None: Trimmed title or None.
        """
        # If value is provided, strip it.
        if value is not None:
            # Strip whitespace.
            trimmed = value.strip()
            # If empty after trimming, raise error.
            if not trimmed:
                # Reject blank string.
                raise ValueError("Task title cannot be blank.")
            # Return trimmed value.
            return trimmed
        # Return None if not provided.
        return None


class TaskRead(TaskBase):
    """Schema returned when reading a task from the API.

    Attributes:
        id (int): Primary key ID.
        user_id (int): Owner user ID.
        owner_name (str | None): Display name of owner.
        position (float): Current position in user list.
        deleted_at (datetime | None): Soft delete timestamp (NULL if active).
        created_at (datetime): UTC creation instant.
        updated_at (datetime): UTC last update instant.
        subtasks (list[SubtaskRead]): Nested checklist items.
        comments_count (int): Number of comments on this task.
    """

    # id: Unique task identifier.
    id: int = Field(..., description="Unique task identifier.")

    # user_id: Owner foreign key.
    user_id: int = Field(..., description="ID of the user who owns this task.")

    # owner_name: Display name of task owner.
    owner_name: str | None = Field(
        default=None,
        description="Name of the task owner.",
    )

    # position: Fractional ordering index.
    position: float = Field(..., description="Position value for manual sorting.")

    # deleted_at: Soft delete timestamp if marked for deletion.
    deleted_at: datetime | None = Field(
        default=None,
        description="Soft delete timestamp (None when active).",
    )

    # created_at: Creation timestamp in UTC.
    created_at: datetime = Field(..., description="Creation instant in UTC.")

    # updated_at: Last update timestamp in UTC.
    updated_at: datetime = Field(..., description="Last modification instant in UTC.")

    # subtasks: Nested checklist subtasks.
    subtasks: list[SubtaskRead] = Field(
        default_factory=list,
        description="List of checklist subtasks attached to this task.",
    )

    # comments_count: Number of comments attached to this task.
    comments_count: int = Field(
        default=0,
        description="Total number of comments posted on this task.",
    )

    # model_config: Enables ORM attribute reading.
    model_config = ConfigDict(from_attributes=True)


class TaskReorder(BaseModel):
    """Schema for reordering tasks.

    Attributes:
        ordered_ids (list[int]): Task IDs in their new desired order.
    """

    # ordered_ids: List of integer task IDs from first to last.
    ordered_ids: list[int] = Field(
        ...,
        min_length=1,
        description="Ordered list of task IDs from top to bottom.",
        examples=[[3, 1, 4, 2]],
    )
