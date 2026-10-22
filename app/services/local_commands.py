"""Local command processor for the TaskFlow assistant.

Handles common task operations directly against the database without calling
an external AI provider. Falls back to the AI provider only for requests that
don't match a known built-in command pattern.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.subtask import Subtask
from app.models.task import Task
from app.models.user import User
from app.schemas.common import Priority, Role, Status
from app.schemas.task import SubtaskCreate, TaskCreate, TaskUpdate
from app.services.activity import log_activity
from app.services.assistant_log import log_local_command


class CommandMatch:
    """Result of matching a user message to a built-in command."""

    def __init__(
        self,
        intent: str,
        confidence: float,
        args: dict[str, Any],
        response: str | None = None,
    ):
        self.intent = intent
        self.confidence = confidence
        self.args = args
        self.response = response


class LocalCommandProcessor:
    """Matches user input to built-in TaskFlow commands and executes them."""

    # ------------------------------------------------------------------
    # Intent matching
    # ------------------------------------------------------------------

    def match(self, text: str) -> CommandMatch | None:
        """Return a CommandMatch if the text looks like a built-in command."""
        original = text.strip()
        lowered = original.lower()
        if not lowered:
            return None

        # Extended commands run first so specifics beat the generic
        # list/create/delete/complete matchers below (e.g. "show task 3"
        # must not fall into the generic "show tasks" list handler).
        extended = self._match_extended(original, lowered)
        if extended is not None:
            return extended

        # --- List tasks -------------------------------------------------
        if self._is_list_tasks(lowered):
            owner_only = not self._is_admin_query(lowered)
            return CommandMatch(
                intent="list_tasks",
                confidence=0.9,
                args={"owner_only": owner_only, "search": self._extract_search(lowered)},
            )

        # --- Add / create task ------------------------------------------
        if self._is_create_task(lowered):
            due_date = self._extract_due_date(lowered)
            title = self._extract_title(original)
            if not title:
                return CommandMatch(
                    intent="create_task",
                    confidence=0.6,
                    args={},
                    response="What would you like to name the task?",
                )
            return CommandMatch(
                intent="create_task",
                confidence=0.85,
                args={"title": title, **({"due_date": due_date} if due_date is not None else {})},
            )

        # --- Delete / remove task ---------------------------------------
        if self._is_delete_task(lowered):
            task_id = self._extract_task_id(lowered)
            if task_id is None:
                return CommandMatch(
                    intent="delete_task",
                    confidence=0.7,
                    args={},
                    response="Which task number should I delete?",
                )
            return CommandMatch(
                intent="delete_task",
                confidence=0.85,
                args={"task_id": task_id},
            )

        # --- Mark complete / toggle -------------------------------------
        if self._is_complete_task(lowered):
            task_id = self._extract_task_id(lowered)
            if task_id is None:
                return CommandMatch(
                    intent="complete_task",
                    confidence=0.7,
                    args={},
                    response="Which task number should I mark as complete?",
                )
            return CommandMatch(
                intent="complete_task",
                confidence=0.85,
                args={"task_id": task_id},
            )

        # --- Show help --------------------------------------------------
        if lowered in {"help", "commands", "what can you do", "/help"}:
            return CommandMatch(
                intent="help",
                confidence=1.0,
                args={},
                response=self._help_text(),
            )

        return None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        match: CommandMatch,
        db: AsyncSession,
        actor: User,
    ) -> dict[str, Any]:
        """Execute a matched command and return a result dict."""
        intent = match.intent
        args = match.args
        is_admin = actor.role == Role.ADMIN

        # A stored response is a clarification or help text — it IS the answer
        # (e.g. "Which task number should I delete?"), so return it directly.
        if match.response:
            result = {"reply": match.response, "actions": []}
            log_local_command(actor, intent, match.confidence, result)
            return result

        if intent == "list_tasks":
            result = await self._cmd_list_tasks(db, actor, args)
        elif intent == "search_tasks":
            result = await self._cmd_search_tasks(db, actor, args, is_admin)
        elif intent == "create_task":
            result = await self._cmd_create_task(db, actor, args, is_admin)
        elif intent == "delete_task":
            result = await self._cmd_delete_task(db, actor, args, is_admin)
        elif intent == "complete_task":
            result = await self._cmd_complete_task(db, actor, args, is_admin)
        elif intent == "complete_all":
            result = await self._cmd_complete_all(db, actor, args, is_admin)
        elif intent == "update_task":
            result = await self._cmd_update_task(db, actor, args, is_admin)
        elif intent == "task_details":
            result = await self._cmd_task_details(db, actor, args, is_admin)
        elif intent == "stats":
            result = await self._cmd_stats(db, actor, args, is_admin)
        elif intent == "overdue":
            result = await self._cmd_overdue(db, actor, args, is_admin)
        elif intent == "add_subtask":
            result = await self._cmd_add_subtask(db, actor, args, is_admin)
        elif intent == "add_comment":
            result = await self._cmd_add_comment(db, actor, args, is_admin)
        elif intent == "list_users":
            result = await self._cmd_list_users(db, actor)
        elif intent == "help":
            result = {"reply": self._help_text(), "actions": []}
        else:
            result = {"reply": "I'm not sure how to handle that.", "actions": []}

        log_local_command(actor, intent, match.confidence, result)
        return result

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    async def _cmd_list_tasks(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        query = select(Task).where(Task.deleted_at.is_(None))
        if not actor.role == Role.ADMIN or args.get("owner_only"):
            query = query.where(Task.user_id == actor.id)
        search = args.get("search")
        if search:
            pattern = f"%{search}%"
            query = query.where(Task.title.ilike(pattern))
        result = await db.execute(
            query.order_by(Task.position.asc(), Task.id.desc()).limit(50)
        )
        tasks = result.scalars().all()
        if not tasks:
            return {"reply": "You don't have any tasks.", "actions": []}
        lines = [
            f"#{t.id} [{t.status}] {t.title}" + (f" (due {t.due_date})" if t.due_date else "")
            for t in tasks
        ]
        return {
            "reply": "Your tasks:\n" + "\n".join(lines),
            "actions": [{"name": "list_tasks", "ok": True, "result": {"count": len(tasks)}}],
        }

    async def _cmd_create_task(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        title = args.get("title")
        if not title:
            return {"reply": "Please provide a task title.", "actions": []}

        # Next drag-and-drop position = current max + 1 (one aggregate query).
        max_position = (
            await db.execute(
                select(func.max(Task.position)).where(Task.user_id == actor.id)
            )
        ).scalar()
        next_pos = (max_position or 0.0) + 1.0

        task = Task(
            user_id=actor.id,
            title=title,
            description=None,
            priority=Priority.LOW.value,
            status=Status.PENDING.value,
            due_date=args.get("due_date"),
            tags=[],
            category=None,
            position=next_pos,
        )
        db.add(task)
        # Flush first so task.id exists when the audit entry is created.
        await db.flush()
        await log_activity(db, actor, "created", "task", task.id, {"title": title, "source": "local-command"})
        await db.commit()
        await db.refresh(task)
        due_note = f" (due {task.due_date})" if task.due_date else ""
        return {
            "reply": f"Created task #{task.id}: {task.title}{due_note}",
            "actions": [
                {
                    "name": "create_task",
                    "ok": True,
                    "result": {
                        "id": task.id,
                        "title": task.title,
                        "due_date": task.due_date.isoformat() if task.due_date else None,
                    },
                }
            ],
        }

    async def _cmd_delete_task(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        query = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        if not is_admin:
            query = query.where(Task.user_id == actor.id)
        task = (await db.execute(query)).scalars().first()
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}
        task.deleted_at = datetime.now()
        await log_activity(db, actor, "deleted", "task", task.id, {"title": task.title, "source": "local-command"})
        await db.commit()
        return {
            "reply": f"Deleted task #{task.id}: {task.title}",
            "actions": [{"name": "delete_task", "ok": True, "result": {"id": task_id}}],
        }

    async def _cmd_complete_task(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        query = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        if not is_admin:
            query = query.where(Task.user_id == actor.id)
        task = (await db.execute(query)).scalars().first()
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}
        task.status = Status.COMPLETED.value
        await log_activity(db, actor, "updated", "task", task.id, {"status": "completed", "source": "local-command"})
        await db.commit()
        return {
            "reply": f"Marked task #{task.id} as complete: {task.title}",
            "actions": [{"name": "complete_task", "ok": True, "result": {"id": task_id}}],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cmd_search_tasks(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        term = (args.get("search") or "").strip()
        if not term:
            return {"reply": "What should I search for in your tasks?", "actions": []}
        query = select(Task).where(Task.deleted_at.is_(None))
        if not is_admin or args.get("owner_only"):
            query = query.where(Task.user_id == actor.id)
        pattern = f"%{term}%"
        query = query.where(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
        result = await db.execute(query.order_by(Task.position.asc(), Task.id.desc()).limit(50))
        tasks = result.scalars().all()
        if not tasks:
            return {"reply": f"No tasks match '{term}'.", "actions": []}
        lines = [f"#{t.id} [{t.status}] {t.title}" for t in tasks]
        return {
            "reply": f"{len(tasks)} task(s) matching '{term}':\n" + "\n".join(lines),
            "actions": [
                {"name": "search_tasks", "ok": True, "result": {"count": len(tasks), "search": term}}
            ],
        }

    async def _cmd_complete_all(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        scope_system = bool(args.get("system")) and is_admin
        statement = (
            update(Task)
            .where(Task.deleted_at.is_(None), Task.status != Status.COMPLETED.value)
            .values(status=Status.COMPLETED.value)
            .execution_options(synchronize_session=False)
        )
        if not scope_system:
            statement = statement.where(Task.user_id == actor.id)
        result = await db.execute(statement)
        count = result.rowcount or 0
        await log_activity(
            db,
            actor,
            "updated",
            "task",
            None,
            {"completed_count": count, "source": "local-command", "system": scope_system},
        )
        await db.commit()
        if count == 0:
            return {"reply": "There were no unfinished tasks to complete.", "actions": []}
        scope_note = "across all users" if scope_system else "in your list"
        return {
            "reply": f"Marked {count} task(s) {scope_note} as complete.",
            "actions": [{"name": "complete_all", "ok": True, "result": {"completed": count}}],
        }

    async def _cmd_update_task(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        fields: dict[str, Any] = args.get("fields") or {}
        task = await self._fetch_task(db, actor, task_id, is_admin)
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}
        if not fields:
            return {"reply": "What should I change — priority, status, due date, or title?", "actions": []}

        changes: dict[str, Any] = {}
        if "priority" in fields:
            task.priority = fields["priority"]
            changes["priority"] = fields["priority"]
        if "status" in fields:
            task.status = fields["status"]
            changes["status"] = fields["status"]
        if "due_date" in fields:
            task.due_date = fields["due_date"]
            changes["due_date"] = fields["due_date"].isoformat() if fields["due_date"] else None
        if "title" in fields and fields["title"]:
            task.title = str(fields["title"])[:200]
            changes["title"] = task.title

        await log_activity(db, actor, "updated", "task", task.id, {**changes, "source": "local-command"})
        await db.commit()
        await db.refresh(task)
        summary = ", ".join(f"{key}={value}" for key, value in changes.items())
        return {
            "reply": f"Updated task #{task.id}: {summary}",
            "actions": [{"name": "update_task", "ok": True, "result": {"id": task.id, **changes}}],
        }

    async def _cmd_task_details(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        task = await self._fetch_task(db, actor, task_id, is_admin)
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}

        subtasks = (
            await db.execute(select(Subtask).where(Subtask.task_id == task.id).order_by(Subtask.position))
        ).scalars().all()
        comments = (
            await db.execute(
                select(Comment, User.name)
                .join(User, Comment.user_id == User.id)
                .where(Comment.task_id == task.id)
                .order_by(Comment.created_at.desc())
                .limit(5)
            )
        ).all()

        lines = [
            f"Task #{task.id}: {task.title}",
            f"Status: {task.status} | Priority: {task.priority}",
            f"Due: {task.due_date.isoformat() if task.due_date else 'no due date'} | Category: {task.category or '—'}",
            f"Tags: {', '.join(task.tags) if task.tags else '—'}",
            f"Description: {task.description or '—'}",
        ]
        if subtasks:
            lines.append("Checklist:")
            lines.extend(f"  [{'x' if s.is_done else ' '}] {s.title}" for s in subtasks)
        if comments:
            lines.append("Recent comments:")
            lines.extend(f"  • {author}: {comment.body}" for comment, author in comments)

        return {
            "reply": "\n".join(lines),
            "actions": [
                {
                    "name": "task_details",
                    "ok": True,
                    "result": {"id": task.id, "subtasks": len(subtasks), "comments": len(comments)},
                }
            ],
        }

    @staticmethod
    def _is_admin_query(text: str) -> bool:
        return any(w in text for w in ("all users", "all tasks", "system", "admin", "everyone", "every user"))

    @staticmethod
    def _is_list_users(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "list users", "show users", "show me users", "list all users",
                "show all users", "who are the users", "list the users", "user list",
            )
        )

    @staticmethod
    def _is_stats(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "stats", "summary", "how many tasks", "task count", "task stats",
                "progress report", "overview", "how am i doing",
            )
        )

    @staticmethod
    def _is_overdue(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "overdue", "due today", "due tomorrow", "tasks due",
                "what's due", "whats due",
            )
        )

    @staticmethod
    def _is_search(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "search tasks", "search task", "find task", "find tasks",
                "tasks containing", "task containing", "looking for task",
                "find me task",
            )
        )

    @staticmethod
    def _is_complete_all(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "complete all tasks", "mark all tasks", "mark all as",
                "finish all tasks", "complete everything", "complete every task",
                "done with everything",
            )
        )

    @staticmethod
    def _is_list_tasks(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "list tasks", "show tasks", "my tasks", "view tasks",
                "what tasks", "tasks do i have", "show my tasks",
            )
        )

    @staticmethod
    def _is_create_task(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "add task", "create task", "new task", "make a task",
                "i need a task", "add a task",
            )
        )

    async def _cmd_stats(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        scope_system = bool(args.get("system")) and is_admin
        filters: list[Any] = [Task.deleted_at.is_(None)]
        if not scope_system:
            filters.append(Task.user_id == actor.id)

        status_rows = (
            await db.execute(
                select(Task.status, func.count(Task.id)).where(*filters).group_by(Task.status)
            )
        ).all()
        counts = {status: count for status, count in status_rows}
        total = sum(counts.values())
        if total == 0:
            return {"reply": "No tasks here yet — nothing to report.", "actions": []}

        pending = counts.get(Status.PENDING.value, 0)
        in_progress = counts.get(Status.IN_PROGRESS.value, 0)
        completed = counts.get(Status.COMPLETED.value, 0)
        today = date.today()
        overdue = (
            await db.execute(
                select(func.count(Task.id)).where(
                    *filters,
                    Task.due_date.is_not(None),
                    Task.due_date < today,
                    Task.status != Status.COMPLETED.value,
                )
            )
        ).scalar() or 0
        due_today = (
            await db.execute(
                select(func.count(Task.id)).where(
                    *filters,
                    Task.due_date == today,
                    Task.status != Status.COMPLETED.value,
                )
            )
        ).scalar() or 0
        pct = round((completed / total) * 100)
        scope_note = "system-wide" if scope_system else "yours"
        reply = (
            f"Task stats ({scope_note}):\n"
            f"Total: {total} | Pending: {pending} | In progress: {in_progress} "
            f"| Completed: {completed} ({pct}%)\n"
            f"Overdue: {overdue} | Due today: {due_today}"
        )
        return {
            "reply": reply,
            "actions": [
                {
                    "name": "stats",
                    "ok": True,
                    "result": {
                        "total": total,
                        "pending": pending,
                        "in_progress": in_progress,
                        "completed": completed,
                        "overdue": overdue,
                        "due_today": due_today,
                    },
                }
            ],
        }

    async def _cmd_overdue(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        cutoff: date = args.get("cutoff") or date.today()
        inclusive = bool(args.get("inclusive"))
        scope_system = bool(args.get("system")) and is_admin

        comparison = Task.due_date <= cutoff if inclusive else Task.due_date < cutoff
        filters: list[Any] = [
            Task.deleted_at.is_(None),
            Task.due_date.is_not(None),
            comparison,
            Task.status != Status.COMPLETED.value,
        ]
        if not scope_system:
            filters.append(Task.user_id == actor.id)
        tasks = (
            await db.execute(
                select(Task).where(*filters).order_by(Task.due_date.asc(), Task.position.asc()).limit(50)
            )
        ).scalars().all()

        if inclusive and cutoff == date.today():
            header = "Tasks due today or earlier"
        elif inclusive:
            header = f"Tasks due on or before {cutoff.isoformat()}"
        else:
            header = "Overdue tasks"
        if not tasks:
            return {"reply": f"{header}: none — you're all caught up.", "actions": []}

        lines = [f"#{t.id} [{t.priority}] {t.title} (due {t.due_date})" for t in tasks]
        return {
            "reply": f"{header} ({len(tasks)}):\n" + "\n".join(lines),
            "actions": [
                {"name": "overdue", "ok": True, "result": {"count": len(tasks), "cutoff": cutoff.isoformat()}}
            ],
        }

    @staticmethod
    def _is_delete_task(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "delete task", "remove task", "drop task",
            )
        )

    async def _cmd_add_subtask(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        title = (args.get("title") or "").strip()
        task = await self._fetch_task(db, actor, task_id, is_admin)
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}
        if not title:
            return {"reply": "What should the subtask be called?", "actions": []}
        max_position = (
            await db.execute(select(func.max(Subtask.position)).where(Subtask.task_id == task.id))
        ).scalar()
        subtask = Subtask(task_id=task.id, title=title[:200], position=(max_position or 0) + 1)
        db.add(subtask)
        await log_activity(db, actor, "updated", "task", task.id, {"subtask_added": subtask.title, "source": "local-command"})
        await db.commit()
        await db.refresh(subtask)
        return {
            "reply": f"Added checklist item to task #{task.id}: {subtask.title}",
            "actions": [
                {
                    "name": "add_subtask",
                    "ok": True,
                    "result": {"id": subtask.id, "task_id": task.id, "title": subtask.title},
                }
            ],
        }

    async def _cmd_add_comment(
        self,
        db: AsyncSession,
        actor: User,
        args: dict[str, Any],
        is_admin: bool,
    ) -> dict[str, Any]:
        task_id = args.get("task_id")
        body = (args.get("body") or "").strip()
        task = await self._fetch_task(db, actor, task_id, is_admin)
        if task is None:
            return {"reply": f"Task #{task_id} not found or not in your scope.", "actions": []}
        if not body:
            return {"reply": "What should the comment say?", "actions": []}
        comment = Comment(task_id=task.id, user_id=actor.id, body=body[:2000])
        db.add(comment)
        await db.flush()
        await log_activity(db, actor, "created", "comment", comment.id, {"task_id": task.id, "source": "local-command"})
        await db.commit()
        await db.refresh(comment)
        return {
            "reply": f"Comment added to task #{task.id}: {comment.body}",
            "actions": [
                {"name": "add_comment", "ok": True, "result": {"id": comment.id, "task_id": task.id}}
            ],
        }

    async def _cmd_list_users(self, db: AsyncSession, actor: User) -> dict[str, Any]:
        if actor.role != Role.ADMIN:
            return {
                "reply": "Only administrators can list users.",
                "actions": [{"name": "list_users", "ok": False, "error": "admin only"}],
            }
        users = (await db.execute(select(User).order_by(User.id.asc()))).scalars().all()
        if not users:
            return {"reply": "No users found.", "actions": []}
        lines = [f"#{u.id} {u.name} <{u.email}> ({u.role})" for u in users]
        return {
            "reply": f"Users ({len(users)}):\n" + "\n".join(lines),
            "actions": [{"name": "list_users", "ok": True, "result": {"count": len(users)}}],
        }

    @staticmethod
    async def _fetch_task(
        db: AsyncSession,
        actor: User,
        task_id: int | None,
        is_admin: bool,
    ) -> Task | None:
        """Load a non-deleted task visible to the actor (admins see all)."""
        query = select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
        if not is_admin:
            query = query.where(Task.user_id == actor.id)
        return (await db.execute(query)).scalars().first()

    @staticmethod
    def _is_complete_task(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "mark complete", "mark as complete", "complete task",
                "finish task", "done task", "as complete", "as done",
                "mark done", "mark as done",
            )
        )

    @staticmethod
    def _is_task_detail(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "show task", "task details", "details of task", "details for task",
                "info on task", "info about task", "tell me about task",
                "describe task", "what is task", "what's task", "view task",
                "read task", "breakdown of task", "find task",
            )
        )

    @staticmethod
    def _is_update_task(text: str) -> bool:
        if any(
            phrase in text
            for phrase in (
                "set task", "change task", "update task", "rename task", "retitle task",
                "set priority", "change priority", "set status", "change status",
                "set due", "change due", "due date of", "priority of task",
                "status of task", "set the title", "change the title",
            )
        ):
            return True
        return "task" in text and any(
            word in text for word in ("priority", "due date", "rename", "retitle")
        )

    @staticmethod
    def _is_add_subtask(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "add subtask", "add a subtask", "new subtask", "add checklist",
                "add a checklist", "checklist item", "add to checklist",
            )
        )

    @staticmethod
    def _is_add_comment(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "add comment", "add a comment", "comment on", "comment to",
                "note on task", "leave a comment", "write a comment",
            )
        )

    @staticmethod
    def _strip_keywords(text: str, words: set[str], strip_numbers: bool = True) -> str:
        """Remove whole words from `text` (never partial substrings).

        Plain str.replace would also strip letters inside other words
        (e.g. removing "i" mangled "milk" -> "mlk"), so whole-word regex
        boundaries are used instead.
        """
        if words:
            pattern = r"\b(" + "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True)) + r")\b"
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        if strip_numbers:
            text = re.sub(r"\b\d+\b", " ", text)
        return " ".join(text.split())

    @staticmethod
    def _extract_search(text: str) -> str:
        # Remove command words to leave the search term (whole words only).
        return LocalCommandProcessor._strip_keywords(
            text,
            {"list", "show", "view", "my", "tasks", "what", "do", "i", "have", "task"},
            strip_numbers=False,
        )

    @staticmethod
    def _extract_search_term(text: str) -> str:
        # For 'search tasks for ...' / 'find task with ...' style queries.
        return LocalCommandProcessor._strip_keywords(
            text,
            {
                "search", "find", "looking", "for", "tasks", "task", "containing",
                "that", "match", "matching", "me", "my", "with", "about",
            },
            strip_numbers=False,
        )

    @staticmethod
    def _extract_title(text: str) -> str | None:
        # Drop an optional trailing "due ..." clause (it sets the due date).
        text = re.sub(r"\bdue\s+(?:today|tomorrow|\d{4}-\d{2}-\d{2})\b", " ", text, flags=re.IGNORECASE)
        title = LocalCommandProcessor._strip_keywords(
            text,
            {"add", "create", "new", "make", "i", "need", "a", "task", "to", "please"},
            strip_numbers=False,
        )
        return title or None

    @staticmethod
    def _extract_due_date(text: str) -> date | None:
        match = re.search(r"\bdue\s+(today|tomorrow|\d{4}-\d{2}-\d{2})\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        raw = match.group(1).lower()
        if raw == "today":
            return date.today()
        if raw == "tomorrow":
            return date.today() + timedelta(days=1)
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _extract_task_id(text: str) -> int | None:
        match = re.search(r"(?:task|#)?\s*(\d+)\b", text)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _extract_marker_body(text: str) -> str | None:
        """Text after a 'task <n>:' marker (used by comments/subtasks)."""
        match = re.search(r"task\s*#?\d+\s*[:\-–]\s*(.+)$", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'").strip() or None
        return None

    def _extract_subtask_title(self, text: str) -> str | None:
        body = self._extract_marker_body(text)
        if body:
            return body
        match = re.search(
            r"(?:subtask|checklist item)\s*[:\-–]?\s*(?:called\s+|named\s+)?(.+?)\s+to\s+task\s*#?\d+$",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            title = match.group(1).strip().strip("\"'").strip()
            if title:
                return title
        return self._strip_keywords(
            text,
            {
                "add", "a", "an", "new", "subtask", "checklist", "item", "to",
                "task", "the", "my", "on", "for", "called", "named",
            },
        ) or None

    def _extract_comment_body(self, text: str) -> str | None:
        body = self._extract_marker_body(text)
        if body:
            return body
        return self._strip_keywords(
            text,
            {
                "add", "a", "an", "comment", "note", "on", "to", "task", "the",
                "my", "for", "leave", "write", "please",
            },
        ) or None

    def _parse_update_fields(self, lowered: str, original: str | None = None) -> dict[str, Any]:
        """Extract priority/status/due-date/title changes from free text.

        Parameters:
            lowered (str): Lowercased text used for value detection.
            original (str | None): Original-casing text, so renamed titles
                keep their capitalization.
        """
        fields: dict[str, Any] = {}

        match = re.search(r"\bpriority\s*(?:to|=|:)?\s*(low|medium|high)\b", lowered)
        if match:
            fields["priority"] = match.group(1)

        match = re.search(
            r"\bstatus\s*(?:to|=|:)?\s*(pending|in[\s_]+progress|completed|complete|done)\b",
            lowered,
        )
        if match:
            raw = match.group(1).replace(" ", "_").replace("-", "_")
            fields["status"] = {
                "complete": Status.COMPLETED.value,
                "done": Status.COMPLETED.value,
            }.get(raw, raw)

        match = re.search(
            r"\bdue\s*(?:date\s*)?(?:to|=|:)?\s*(\d{4}-\d{2}-\d{2}|today|tomorrow|none|clear)\b",
            lowered,
        )
        if match:
            raw = match.group(1)
            if raw in {"none", "clear"}:
                fields["due_date"] = None
            elif raw == "today":
                fields["due_date"] = date.today()
            elif raw == "tomorrow":
                fields["due_date"] = date.today() + timedelta(days=1)
            else:
                try:
                    fields["due_date"] = date.fromisoformat(raw)
                except ValueError:
                    pass

        source = original if original is not None else lowered
        match = (
            re.search(r"\brename\s+task\s*#?\d+\s+(?:to|as)\s+(.+)$", source, flags=re.IGNORECASE)
            or re.search(r"\btitle\s+of\s+task\s*#?\d+\s+(?:to|=|:)\s+(.+)$", source, flags=re.IGNORECASE)
            or re.search(r"\bset\s+(?:the\s+)?title\s+(?:to|=|:)\s+(.+)$", source, flags=re.IGNORECASE)
        )
        if match:
            title = match.group(1).strip().strip("\"'").strip()
            if title:
                fields["title"] = title

        return fields

    def _parse_overdue_args(self, lowered: str) -> dict[str, Any]:
        """Turn 'overdue' / 'due today' / 'due tomorrow' into query args."""
        args: dict[str, Any] = {"system": self._is_admin_query(lowered)}
        if "tomorrow" in lowered:
            args["cutoff"] = date.today() + timedelta(days=1)
            args["inclusive"] = True
        elif "today" in lowered:
            args["cutoff"] = date.today()
            args["inclusive"] = True
        else:
            args["cutoff"] = date.today()
            args["inclusive"] = False
        return args

    def _match_extended(self, original: str, lowered: str) -> CommandMatch | None:
        """Match newer, specific commands first; None falls through to `match()`."""
        # --- Task details -------------------------------------------------
        if self._is_task_detail(lowered):
            task_id = self._extract_task_id(lowered)
            if task_id is not None:
                return CommandMatch(
                    intent="task_details",
                    confidence=0.9,
                    args={"task_id": task_id},
                )
            # No task number ("show tasks") — let the generic list matcher run.

        # --- Add subtask --------------------------------------------------
        if self._is_add_subtask(lowered):
            task_id = self._extract_task_id(lowered)
            title = self._extract_subtask_title(original)
            if task_id is None:
                return CommandMatch(
                    intent="add_subtask",
                    confidence=0.7,
                    args={},
                    response="Which task number should I add the subtask to?",
                )
            if not title:
                return CommandMatch(
                    intent="add_subtask",
                    confidence=0.7,
                    args={"task_id": task_id},
                    response="What should the subtask be called?",
                )
            return CommandMatch(
                intent="add_subtask",
                confidence=0.9,
                args={"task_id": task_id, "title": title},
            )

        # --- Add comment --------------------------------------------------
        if self._is_add_comment(lowered):
            task_id = self._extract_task_id(lowered)
            body = self._extract_comment_body(original)
            if task_id is None:
                return CommandMatch(
                    intent="add_comment",
                    confidence=0.7,
                    args={},
                    response="Which task number should I comment on?",
                )
            if not body:
                return CommandMatch(
                    intent="add_comment",
                    confidence=0.7,
                    args={"task_id": task_id},
                    response="What should the comment say?",
                )
            return CommandMatch(
                intent="add_comment",
                confidence=0.9,
                args={"task_id": task_id, "body": body},
            )

        # --- List users (admin) --------------------------------------------
        if self._is_list_users(lowered):
            return CommandMatch(intent="list_users", confidence=0.9, args={})

        # --- Stats / summary ------------------------------------------------
        if self._is_stats(lowered):
            return CommandMatch(
                intent="stats",
                confidence=0.9,
                args={"system": self._is_admin_query(lowered)},
            )

        # --- Overdue / due today / due tomorrow (not "add task ... due ...") ---
        if self._is_overdue(lowered) and not self._is_create_task(lowered):
            return CommandMatch(
                intent="overdue",
                confidence=0.9,
                args=self._parse_overdue_args(lowered),
            )

        # --- Free-text search ------------------------------------------------
        if self._is_search(lowered):
            term = self._extract_search_term(original)
            if not term:
                return CommandMatch(
                    intent="search_tasks",
                    confidence=0.7,
                    args={},
                    response="What should I search for in your tasks?",
                )
            return CommandMatch(
                intent="search_tasks",
                confidence=0.9,
                args={"search": term, "owner_only": not self._is_admin_query(lowered)},
            )

        # --- Complete ALL tasks ----------------------------------------------
        if self._is_complete_all(lowered):
            return CommandMatch(
                intent="complete_all",
                confidence=0.9,
                # System-wide only on explicit multi-user wording — an admin
                # saying "complete all tasks" usually means their own list.
                args={
                    "system": any(
                        word in lowered for word in ("all users", "everyone", "system", "across users")
                    )
                },
            )

        # --- Update task. Guarded so generic create/list/delete/complete
        #     matchers in match() always win for their own phrases. ---------
        if (
            self._is_update_task(lowered)
            and not self._is_create_task(lowered)
            and not self._is_list_tasks(lowered)
            and not self._is_delete_task(lowered)
            and not self._is_complete_task(lowered)
        ):
            task_id = self._extract_task_id(lowered)
            fields = self._parse_update_fields(lowered, original)
            if task_id is None:
                return CommandMatch(
                    intent="update_task",
                    confidence=0.7,
                    args={},
                    response="Which task number should I update?",
                )
            if not fields:
                return CommandMatch(
                    intent="update_task",
                    confidence=0.7,
                    args={"task_id": task_id},
                    response="What should I change — priority, status, due date, or title?",
                )
            return CommandMatch(
                intent="update_task",
                confidence=0.9,
                args={"task_id": task_id, "fields": fields},
            )

        return None

    @staticmethod
    def _help_text() -> str:
        return (
            "Built-in commands (no API key needed):\n"
            "• 'list tasks' / 'search tasks for <text>' — find your tasks\n"
            "• 'show task <number>' — task details, checklist & comments\n"
            "• 'add task <title> [due today|tomorrow|YYYY-MM-DD]' — create a task\n"
            "• 'set task <number> priority to high|medium|low' — change priority\n"
            "• 'set task <number> status to in progress|completed' — change status\n"
            "• 'rename task <number> to <new title>' — retitle a task\n"
            "• 'add subtask <text> to task <number>' — add a checklist item\n"
            "• 'comment on task <number>: <text>' — add a comment\n"
            "• 'complete task <number>' / 'complete all tasks' — mark done\n"
            "• 'delete task <number>' — remove a task\n"
            "• 'overdue tasks' / 'tasks due today' — what needs attention\n"
            "• 'stats' / 'summary' — counts, progress and overdue numbers\n"
            "• 'list users' (admin) — user directory\n"
            "• 'help' — show this message\n\n"
            "For complex requests, the AI provider (OpenRouter / Gemini / Groq) is used."
        )


# Singleton instance
local_commands = LocalCommandProcessor()
