# ============================================================================
# FILE:       backend/app/models/subtask.py
# PURPOSE:    Define the `subtasks` table — one checklist item inside a task
#             ("buy milk" inside "plan dinner"). Checking items drives the
#             task card's progress bar.
# HOW IT WORKS: A child table of `tasks`: every row points at exactly one task
#             via `task_id`. Deleting the task deletes its subtasks in SQL
#             (ON DELETE CASCADE), so orphan checklist rows cannot exist.
# USED BY / DEPENDS ON: Base from app.database; Task.subtasks relationship;
#             written by routers/tasks.py when a task carries checklist items.
# KEY CONCEPTS: child table, foreign key cascade, boolean with default,
#             checklist ordering.
# ============================================================================

"""SQLAlchemy model for the `subtasks` table."""

from sqlalchemy.orm import Mapped, mapped_column, relationship

# Types: Boolean for the done flag, Integer for ids/order, String for text,
# ForeignKey for the link to tasks, func.now() for the timestamp default.
from sqlalchemy import Boolean, DateTime, ForeignKey, Identity, Integer, String, func

from datetime import datetime
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:  # pragma: no cover - typing-only branch
    from app.models.task import Task


class Subtask(Base):
    """A single checklist item belonging to one task.

    Real-world meaning: the small checkbox lines under a task title. The UI
    computes `done / total` to render the animated progress bar, and toggling
    `is_done` updates the parent task's sense of completion without changing
    the task's own `status` (the user may want an in-progress parent with a
    half-finished checklist).

    Table: `subtasks`
    """

    __tablename__ = "subtasks"

    __table_args__ = (
        # Table comment visible via \d+ subtasks.
        {"comment": "Checklist items of a task; removed automatically with their task."},
    )

    # id: identity primary key (same scheme as every other table).
    id: Mapped[int] = mapped_column(
        Integer,
        Identity(always=True),
        primary_key=True,
    )

    # task_id: FK to tasks.id. CASCADE deletes the checklist with the task;
    # index=True makes "fetch checklist of task 42" an index lookup.
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent task FK -> tasks.id. Deleting the task deletes its checklist.",
    )

    # title: the checklist text; same 1-200 rule as task titles (bounded type).
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Checklist item text (1-200 chars).",
    )

    # is_done: the checkbox state. False by default so creating an item needs
    # only a title. Drives the parent's progress bar (done/total).
    is_done: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",  # SQL-side default must match the Python one
        comment="Checked state; false = pending. Feeds the task progress bar.",
    )

    # position: order inside the checklist (1, 2, 3, ...). Integer is enough —
    # subtasks are not dragged between other subtasks as often as cards are.
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Display order within the checklist (ascending).",
    )

    # created_at: when the item was added (useful for import reports).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation instant (UTC).",
    )

    # The parent task; mirrors Task.subtasks (back_populates keeps both sides
    # in sync automatically when you append to either list).
    task: Mapped["Task"] = relationship(
        back_populates="subtasks",
        lazy="selectin",  # load with the task in one extra batched query
    )

    def __repr__(self) -> str:
        """str: compact representation for logs and tracebacks."""
        return f"<Subtask id={self.id} title={self.title!r} done={self.is_done}>"
