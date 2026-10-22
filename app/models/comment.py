# ============================================================================
# FILE:       backend/app/models/comment.py
# PURPOSE:    Define the `comments` table — the discussion thread attached to
#             a task. Each row has an author (user) and a body of 1-1000
#             characters, with an `is_edited` flag for the UI's "(edited)" tag.
# HOW IT WORKS: Two foreign keys (task + author) link the thread to its task
#             and its writer. A CHECK constraint repeats the Pydantic length
#             rule so even raw SQL cannot insert empty or oversized comments.
# USED BY / DEPENDS ON: Base from app.database; written/read by
#             routers/comments.py; included in the admin activity log.
# KEY CONCEPTS: join table with two FKs, CHECK on trimmed length, edited flag,
#             newest-last ordering.
# ============================================================================

"""SQLAlchemy model for the `comments` table."""

from sqlalchemy.orm import Mapped, mapped_column, relationship

# Types: Text = unbounded body; CheckConstraint = length rule in the database;
# DateTime(timezone=True) = UTC instants; func.now() = server clock.
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Integer, Text, func

from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:  # pragma: no cover - typing-only branch
    from app.models.task import Task
    from app.models.user import User


class Comment(Base):
    """One comment written by one user on one task.

    Real-world meaning: a chat message under a task. Authors can edit or
    delete only their own rows (enforced in the router), while admins may
    delete any comment (moderation). The thread is returned oldest-first so
    the UI shows the newest comment at the bottom next to the composer.

    Table: `comments`
    """

    __tablename__ = "comments"

    __table_args__ = (
        # Length rule duplicated from Pydantic: btrim removes leading/trailing
        # spaces first, so "   " fails too (empty after trim -> rejected).
        CheckConstraint(
            "length(btrim(body)) BETWEEN 1 AND 1000",
            name="ck_comments_body_len",
        ),
        {"comment": "Task discussion entries, 1-1000 chars, authored by a user."},
    )

    # id: identity primary key.
    id: Mapped[int] = mapped_column(
        Integer,
        Identity(always=True),
        primary_key=True,
    )

    # task_id: which task the comment belongs to. CASCADE removes the whole
    # thread when the task is (hard-)deleted; index serves the thread query.
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Thread parent FK -> tasks.id. Comments die with their task.",
    )

    # user_id: the author. CASCADE removes a user's comments when the user is
    # deleted (chosen over SET NULL so comments never become anonymous).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Author FK -> users.id. Deleting the user deletes their comments.",
    )

    # body: the message text. Pydantic validates on the way in; the CHECK
    # constraint above repeats the rule as the final safety net.
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Message text, trimmed, 1-1000 characters (ck_comments_body_len).",
    )

    # is_edited: set true by PATCH .../comments/{id}. A boolean is more
    # reliable than comparing timestamps (any update touches updated_at).
    is_edited: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="True after the author edits the body; UI shows '(edited)'.",
    )

    # created_at: publish time; also the thread's ordering key (ascending =
    # oldest first, newest last — exactly what the UI needs).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Publish instant (UTC); thread order is created_at ASC.",
    )

    # updated_at: refreshed automatically when the body is edited.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last edit instant (UTC), auto-refreshed on UPDATE.",
    )

    # --- Relationships ---------------------------------------------------
    # The task this comment hangs on (many comments -> one task).
    task: Mapped["Task"] = relationship(
        back_populates="comments",
        lazy="noload",  # never auto-load the whole task when reading comments
    )

    # The author. lazy="joined" loads name/email together with the comment so
    # the thread can render avatars without a second query per row.
    user: Mapped["User"] = relationship(
        back_populates="comments",
        lazy="joined",
    )

    def __repr__(self) -> str:
        """str: compact representation for logs and tracebacks."""
        return f"<Comment id={self.id} task_id={self.task_id} by={self.user_id}>"
