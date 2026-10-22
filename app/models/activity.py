# ============================================================================
# FILE:       backend/app/models/activity.py
# PURPOSE:    Define the `activity_log` table — the admin audit trail that
#             records who did what (created/updated/deleted/imported/exported)
#             to which entity, and when.
# HOW IT WORKS: Append-only rows written by services/activity.py inside the
#             SAME transaction as the action itself, so the log can never
#             disagree with reality. The author FK is ON DELETE SET NULL: the
#             history survives even after the user row is deleted, and
#             `actor_name` keeps showing WHO did it.
# USED BY / DEPENDS ON: Base from app.database; written by services/activity.py,
#             read by routers/admin.py (GET /admin/activity).
# KEY CONCEPTS: audit log, append-only data, SET NULL FK, JSONB context,
#             denormalized name snapshot.
# ============================================================================

"""SQLAlchemy model for the `activity_log` table."""

from sqlalchemy.orm import Mapped, mapped_column

# JSONB = PostgreSQL's indexed binary JSON — perfect for flexible detail blobs.
# CheckConstraint keeps the action vocabulary closed; Index marks the hot
# sort column explicitly (descending index for the newest-first page).
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
)

# JSONB lives in the PostgreSQL dialect module (it is PostgreSQL-only, which
# is fine: TaskFlow targets PostgreSQL exclusively).
from sqlalchemy.dialects.postgresql import JSONB

from datetime import datetime

from app.database import Base


class ActivityLog(Base):
    """One audit entry: an actor performed an action on an entity at a time.

    Real-world meaning: the admin Activity page's timeline. Rows are never
    updated or deleted by the application (append-only) — an audit trail that
    rewrites history is worthless. `detail` carries JSON context such as
    `{"format": "csv", "rows": 120}` for exports or `{"title": "..."}` for
    deletions, so the log stays human-readable.

    Table: `activity_log`
    """

    __tablename__ = "activity_log"

    __table_args__ = (
        # Closed vocabulary of actions — typos in code become DB errors instead
        # of silently un-filterable log entries.
        CheckConstraint(
            "action IN ('created','updated','deleted','restored','toggled',"
            "'reordered','imported','exported','login_as')",
            name="ck_activity_action",
        ),
        # Same idea for the entity being acted upon:
        CheckConstraint(
            "entity_type IN ('task','comment','user','export','import')",
            name="ck_activity_entity_type",
        ),
        # Newest-first sorting is THE access pattern of the activity page, so
        # a dedicated index (string column name -> SQLAlchemy creates it)
        # makes that page a pure index scan.
        Index("ix_activity_log_created_at", "created_at"),
        {"comment": "Append-only audit trail: actor, action, entity, when, detail."},
    )

    # id: identity primary key.
    id: Mapped[int] = mapped_column(
        Integer,
        Identity(always=True),
        primary_key=True,
    )

    # user_id: WHO did it. SET NULL on delete = the log row survives when the
    # user is removed; actor_name below keeps the display name available.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Actor FK -> users.id. ON DELETE SET NULL so history survives.",
    )

    # actor_name: snapshot of user.name at action time (denormalized on
    # purpose — logs must stay readable after the user row is gone).
    actor_name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="Actor's name copied at write time; survives user deletion.",
    )

    # action: what kind of thing happened (matches ck_activity_action).
    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Verb: created|updated|deleted|restored|toggled|reordered|imported|exported|login_as.",
    )

    # entity_type: which table was affected (matches ck_activity_entity_type).
    # Always required — every log row must name a target kind so the admin
    # page can filter by it.
    entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Target kind: task|comment|user|export|import.",
    )

    # entity_id: the specific row's id. Nullable because a single export/
    # import action covers MANY rows (their count lives in `detail`). It may
    # also reference a row deleted later — normal for an append-only log.
    entity_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Target row id (NULL for bulk actions like export/import).",
    )

    # detail: flexible JSON context. JSONB on PostgreSQL = stored binary,
    # indexable and fast to query with containment operators.
    detail: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,          # ORM inserts get {}
        server_default="{}",    # raw SQL inserts get {} too
        comment="Extra JSON context, e.g. {\"format\":\"csv\",\"rows\":120}.",
    )

    # created_at: when the action happened; indexed DESC for the activity page.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Action instant (UTC). Page sorts by this, newest first.",
    )

    def __repr__(self) -> str:
        """str: compact representation for logs and tracebacks."""
        return (
            f"<ActivityLog id={self.id} actor={self.actor_name!r} "
            f"action={self.action!r} entity={self.entity_type}/{self.entity_id}>"
        )
