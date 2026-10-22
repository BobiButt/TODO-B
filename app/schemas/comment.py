# ============================================================================
# FILE:       backend/app/schemas/comment.py
# PURPOSE:    Pydantic schemas for task comments (create, update, read).
#             Validates comment lengths (1-1000 chars), trims whitespace,
#             and provides serialized representation for discussion threads.
# HOW IT WORKS: Inherits from BaseModel. Uses field_validator to strip leading
#             and trailing spaces so whitespace-only comments are rejected.
#             CommentRead uses from_attributes=True for ORM conversion.
# USED BY / DEPENDS ON: routers/comments.py, schemas/__init__.py, tests.
# KEY CONCEPTS: Input validation, string sanitization, ORM serialization mode,
#             response models.
# ============================================================================

"""Pydantic schemas for task comments (create, update, read)."""

# datetime represents UTC timestamps for creation and updates.
from datetime import datetime

# BaseModel is the foundation for all Pydantic v2 schemas; ConfigDict configures options.
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommentBase(BaseModel):
    """Base schema holding the common fields for comments.

    Attributes:
        body (str): The markdown or plain text of the comment (1-1000 chars).
    """

    # body: The core message text. We enforce 1 to 1000 characters.
    body: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="The message text of the comment (1-1000 characters).",
        examples=["This task depends on the design approval."],
    )

    @field_validator("body")
    @classmethod
    def _strip_whitespace(cls, value: str) -> str:
        """Strip surrounding whitespace and ensure the comment is not empty.

        Parameters:
            value (str): The incoming raw string.

        Returns:
            str: The trimmed string.

        Raises:
            ValueError: If the string is empty after stripping whitespace.
        """
        # Strip leading and trailing whitespace characters.
        trimmed = value.strip()
        # Verify that at least one non-whitespace character remains.
        if not trimmed:
            # Raise descriptive validation error.
            raise ValueError("Comment cannot consist only of whitespace.")
        # Return the clean trimmed string.
        return trimmed


class CommentCreate(CommentBase):
    """Schema for creating a new comment.

    Inherits:
        CommentBase: Contains the validated body field.
    """

    # Inherits body from CommentBase without requiring additional fields.
    pass


class CommentUpdate(CommentBase):
    """Schema for updating an existing comment.

    Inherits:
        CommentBase: Contains the validated body field.
    """

    # Inherits body from CommentBase. Updating marks is_edited=True on the model.
    pass


class CommentRead(CommentBase):
    """Schema returned to the client when reading a comment.

    Attributes:
        id (int): Unique identifier of the comment.
        task_id (int): Parent task ID this comment is attached to.
        user_id (int): ID of the user who authored the comment.
        author_name (str | None): Display name of the author (from relation).
        is_edited (bool): Whether this comment has been modified after creation.
        created_at (datetime): Creation timestamp in UTC.
        updated_at (datetime): Last modification timestamp in UTC.
    """

    # id: Primary key identifier of the comment.
    id: int = Field(..., description="Unique comment identifier.")

    # task_id: Identifier of the parent task.
    task_id: int = Field(..., description="ID of the task this comment belongs to.")

    # user_id: Identifier of the author.
    user_id: int = Field(..., description="ID of the user who authored this comment.")

    # author_name: Human-friendly name displayed on the comment card.
    author_name: str | None = Field(
        default=None,
        description="Display name of the author (populated from user relation).",
    )

    # is_edited: Flag indicating whether an author edit took place.
    is_edited: bool = Field(
        default=False,
        description="Flag showing if the comment text was modified after creation.",
    )

    # created_at: Creation instant in UTC.
    created_at: datetime = Field(..., description="UTC creation timestamp.")

    # updated_at: Last update instant in UTC.
    updated_at: datetime = Field(..., description="UTC last update timestamp.")

    # model_config: Allows Pydantic to read ORM attributes directly.
    model_config = ConfigDict(from_attributes=True)
