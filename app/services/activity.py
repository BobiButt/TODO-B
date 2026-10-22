# ============================================================================
# FILE:       backend/app/services/activity.py
# PURPOSE:    Audit logging helper function to record administrative and user
#             actions in the append-only `activity_log` table.
# HOW IT WORKS: Instantiates an ActivityLog model object and adds it to the
#             current SQLAlchemy AsyncSession so it is committed atomically
#             alongside the triggering database modification.
# USED BY / DEPENDS ON: routers/tasks.py, routers/comments.py, routers/users.py,
#             routers/transfer.py, routers/admin.py.
# KEY CONCEPTS: Audit trail, atomic transaction participation, actor snapshot.
# ============================================================================

"""Audit logging service for writing append-only activity trail records."""

# typing imports for optional parameters and dictionary structures.
from typing import Any

# AsyncSession is the active transaction-bound database session.
from sqlalchemy.ext.asyncio import AsyncSession

# ActivityLog is the ORM model for the activity_log table.
from app.models.activity import ActivityLog

# User model representing the actor performing the operation.
from app.models.user import User


async def log_activity(
    # db is the active asynchronous database session.
    db: AsyncSession,
    # actor is the User performing the action, or None for automated actions.
    actor: User | None,
    # action is the verb ('created', 'updated', 'deleted', 'toggled', etc.).
    action: str,
    # entity_type is the resource kind ('task', 'comment', 'user', 'export', 'import').
    entity_type: str,
    # entity_id is the primary key of the modified row, or None for bulk ops.
    entity_id: int | None = None,
    # detail contains structured JSON metadata such as title or row count.
    detail: dict[str, Any] | None = None,
) -> ActivityLog:
    """Record an audit trail event in the activity_log table.

    Parameters:
        db (AsyncSession): The active database session.
        actor (User | None): The user performing the action.
        action (str): The action verb from the closed vocabulary.
        entity_type (str): The entity kind from the closed vocabulary.
        entity_id (int | None): Target entity ID if applicable.
        detail (dict[str, Any] | None): Additional JSON context dictionary.

    Returns:
        ActivityLog: The newly created ORM log entry attached to the session.

    NOTE:
        We do NOT commit here. By simply calling `db.add()`, the audit log entry
        commits or rolls back in the exact same transaction as the parent action.
    """
    # Resolve the actor ID if an actor user instance was provided.
    user_id = actor.id if actor else None

    # Snapshot the actor's display name so history remains human-readable if deleted.
    actor_name = actor.name if actor else "System"

    # Default detail to an empty dictionary if None was supplied.
    safe_detail = detail if detail is not None else {}

    # Instantiate the ActivityLog ORM record.
    entry = ActivityLog(
        user_id=user_id,
        actor_name=actor_name,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=safe_detail,
    )

    # Attach the audit entry to the active session.
    db.add(entry)

    # Flush so that the entry receives its database-generated timestamp and ID.
    await db.flush()

    # Return the flushed entry.
    return entry
