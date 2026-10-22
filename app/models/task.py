# ============================================================================
# FILE:       backend/app/models/task.py
# PURPOSE:    Define the `tasks` table — the heart of the app. One row is one
#             todo item with priority, status, due date, tags, category,
#             drag-and-drop position and a soft-delete timestamp.
# HOW IT WORKS: SQLAlchemy 2.0 declarative model. PostgreSQL-specific types
#             (ARRAY) store tags natively; CHECK constraints keep values legal
#             even if buggy code slips past Pydantic; `deleted_at` implements
#             soft delete (row stays, queries filter it out).
# USED BY / DEPENDS ON: Base from app.database, Priority/Status from
#             schemas/common (shared enums). Referenced by Subtask + Comment.
# KEY CONCEPTS: ARRAY column, fractional position, soft delete, indexes on
#             hot filter columns, cascade + selectin loading.
# ============================================================================

"""SQLAlchemy model for the `tasks` table."""

# Mapped/mapped_column declare and configure columns; relationship links tables.
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Types & constraints: ARRAY is the PostgreSQL array type (tags); CheckConstraint
# enforces allowed values; Date = calendar date; Float = fractional positions.
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    func,
)

# Python types used by the annotations:
from datetime import date, datetime

# TYPE_CHECKING-only imports avoid circular imports (task <-> subtask/comment).
from typing import TYPE_CHECKING

# The shared Base and the sibling models we point foreign keys at.
from app.database import Base

# Shared enum vocabulary so DB defaults and API validation never disagree.
from app.schemas.common import Priority, Status

if TYPE_CHECKING:  # pragma: no cover - typing-only branch
    from app.models.user import User
    from app.models.subtask import Subtask
    from app.models.comment import Comment


class Task(Base):
    """One todo item belonging to exactly one user.

    Real-world meaning: the card you see in the UI. It can be filtered,
    sorted, dragged into order, completed, commented on, exported, and — when
    deleted — restored within 5 seconds because deletion only *stamps*
    `deleted_at` instead of removing the row.

    Table: `tasks`
    """

    __tablename__ = "tasks"

    # Table-level constraints and documentation. Indexes live on the columns
    # themselves (index=True) so SQLAlchemy auto-names them ix_tasks_<column>.
    __table_args__ = (
        # Allowed priorities — mirror of the Pydantic enum, defence in depth.
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_tasks_priority",
        ),
        # Allowed statuses — mirror of the Pydantic Status enum.
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed')",
            name="ck_tasks_status",
        ),
        # Title must contain 1..200 non-space characters AFTER trimming, so a
        # title of only spaces is rejected by the database too (btrim = trim).
        CheckConstraint(
            "length(btrim(title)) BETWEEN 1 AND 200",
            name="ck_tasks_title_len",
        ),
        # COMMENT ON TABLE tasks — readable later with \d+ tasks in psql.
        {"comment": "Todo items owned by one user; soft-deleted via deleted_at."},
    )

    # id: primary key, PostgreSQL-generated identity (auto-increment).
    id: Mapped[int] = mapped_column(
        Integer,
        Identity(always=True),
        primary_key=True,
    )

    # user_id: FK to users.id. ondelete="CASCADE" means deleting the user also
    # deletes this task *in SQL* (no orphans). index=True creates
    # ix_tasks_user_id — the most-used index (the "my tasks" panel).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owner FK -> users.id. Deleting the user deletes their tasks (CASCADE).",
    )

    # title: short card text; length enforced here (String) and by ck_tasks_title_len.
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Short task summary shown on the card (1-200 chars, trimmed).",
    )

    # description: optional long details; TEXT has no length limit, nullable
    # because most tasks do not need one (NULL = 'no description').
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional long-form details. NULL means 'no description'.",
    )

    # priority: low/medium/high driving badge colour and sorting.
    # Stored as plain text (not a native PG enum) so adding a level later
    # needs only a CHECK update — no ALTER TYPE migration.
    priority: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=Priority.LOW,
        server_default=Priority.LOW,
        comment="Importance: 'low'|'medium'|'high'. Checked by ck_tasks_priority.",
    )

    # status: pending/in_progress/completed; the toggle endpoint flips
    # pending <-> completed. index=True serves the status filter dropdown.
    status: Mapped[str] = mapped_column(
        String(15),
        nullable=False,
        default=Status.PENDING,
        server_default=Status.PENDING,
        index=True,
        comment="Workflow state 'pending'|'in_progress'|'completed'. Indexed for filters.",
    )

    # due_date: plain calendar day (time-of-day is irrelevant for deadlines).
    # Nullable = "no deadline". Indexed because overdue/due-today filters
    # compare this column on every list request.
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
        comment="Deadline (YYYY-MM-DD). NULL = no deadline. Indexed for overdue filters.",
    )

    # tags: a REAL PostgreSQL array of short strings (not a joined string).
    # Filtering by tag becomes `work = ANY(tags)` — exact and fast; export
    # joins the values with ';' for CSV. server_default '{}' = empty array.
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)),
        nullable=False,
        default=list,          # Python-side default: [] (fresh list per row)
        server_default="{}",    # SQL-side default: empty array literal
        comment="Free-form labels stored as text[]; CSV export joins them with ';'.",
    )

    # category: a single optional grouping label ("Work", "Home") — separate
    # from tags because a task has at most one category but many tags.
    category: Mapped[str | None] = mapped_column(
        String(60),
        nullable=True,
        comment="Optional single grouping label (e.g. 'Work'); NULL = uncategorized.",
    )

    # position: manual drag-and-drop order. DOUBLE PRECISION allows fractional
    # gaps (1.0, 1.5, 2.0), so dropping between two cards needs no mass
    # renumbering; the reorder endpoint normalizes to integers afterwards.
    position: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0",
        comment="Sort order for manual drag-and-drop; fractional values allowed.",
    )

    # deleted_at: SOFT DELETE marker. NULL = visible; any timestamp = deleted
    # (list queries add WHERE deleted_at IS NULL). Keeps Undo possible and
    # preserves comments/audit history after a delete.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft delete: NULL=visible, set=deleted (Undo clears it back to NULL).",
    )

    # created_at: creation instant; default sort key (sort=created).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation instant (UTC).",
    )

    # updated_at: auto-refreshed on every ORM UPDATE via onupdate=func.now().
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last modification instant (UTC), auto-refreshed on UPDATE.",
    )

    # --- Relationships ---------------------------------------------------
    # The owner (many tasks -> one user); mirrors User.tasks.
    user: Mapped["User"] = relationship(
        back_populates="tasks",
        lazy="joined",  # fetch owner name in the same query as the task
    )

    # Checklist items, always loaded in position order. lazy="selectin" runs a
    # second SELECT (IN (...)) instead of one query per task — avoids the N+1
    # problem while still batching. DB-level cascade removes them with the task.
    subtasks: Mapped[list["Subtask"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Subtask.position",
        lazy="selectin",
    )

    # Comment thread, oldest-first (newest last, per the UI spec). Same N+1
    # avoidance and cascade rules as subtasks.
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Comment.created_at",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """str: compact representation for logs and tracebacks."""
        return f"<Task id={self.id} title={self.title!r} status={self.status!r}>"

