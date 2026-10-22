# ============================================================================
# FILE:       backend/alembic/versions/0001_initial_schema.py
# PURPOSE:    Initial database migration creating the five core tables:
#             users, tasks, subtasks, comments, and activity_log.
# HOW IT WORKS: alembic upgrade applies DDL statements inside a transaction:
#             creates tables, adds indexes, foreign keys with appropriate
#             CASCADE / SET NULL rules, and CHECK constraints.
#             alembic downgrade drops the tables in reverse dependency order.
# USED BY / DEPENDS ON: alembic upgrade head, alembic downgrade -1.
# KEY CONCEPTS: DDL migrations, PostgreSQL native arrays, JSONB, CHECK constraints,
#             referential integrity and cascades.
# ============================================================================

"""Initial schema: users, tasks, subtasks, comments, activity_log.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-14 19:30:00.000000
"""

# Revision identifiers used by Alembic to build the DAG of migrations.
revision: str = "0001_initial_schema"

# down_revision is None because this is the root migration.
down_revision: str | None = None

# branch_labels and depends_on for multi-branch migrations (unused here).
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

# Alembic's operation harness providing DDL methods like create_table and drop_table.
from alembic import op

# SQLAlchemy types and constraints used to define schema structures.
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    """Apply the migration: create users, tasks, subtasks, comments, activity_log."""
    
    # 1. Create the `users` table.
    op.create_table(
        "users",
        # id: Primary key auto-incrementing identity column.
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False, primary_key=True),
        # email: Unique user email address.
        sa.Column("email", sa.String(length=255), nullable=False, unique=True, comment="Unique email address."),
        # name: Display name of the user.
        sa.Column("name", sa.String(length=80), nullable=False, comment="Display name (1-80 chars)."),
        # role: Role tier ('admin' or 'user').
        sa.Column("role", sa.String(length=10), server_default="user", nullable=False, comment="Permission tier: 'admin' or 'user'."),
        # created_at: UTC timestamp when the user registered/was seeded.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Account creation timestamp (UTC)."),
        # updated_at: UTC timestamp when the user was last edited.
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Account last modification timestamp (UTC)."),
        # Table-level CHECK constraints.
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 80", name="ck_users_name_len"),
        comment="People who can own tasks; role determines admin permissions.",
    )

    # 2. Create the `tasks` table.
    op.create_table(
        "tasks",
        # id: Primary key identity.
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False, primary_key=True),
        # user_id: Foreign key to users.id with CASCADE deletion.
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="Owner FK -> users.id."),
        # title: Short card title.
        sa.Column("title", sa.String(length=200), nullable=False, comment="Task card title (1-200 chars)."),
        # description: Optional long-form markdown/plain text details.
        sa.Column("description", sa.Text(), nullable=True, comment="Detailed task description."),
        # priority: Urgency level ('low', 'medium', 'high').
        sa.Column("priority", sa.String(length=10), server_default="low", nullable=False, comment="Urgency: 'low', 'medium', 'high'."),
        # status: Workflow state ('pending', 'in_progress', 'completed').
        sa.Column("status", sa.String(length=15), server_default="pending", nullable=False, comment="Workflow state: 'pending', 'in_progress', 'completed'."),
        # due_date: Optional calendar deadline.
        sa.Column("due_date", sa.Date(), nullable=True, comment="Calendar deadline (YYYY-MM-DD)."),
        # tags: PostgreSQL native text[] array of tags.
        sa.Column("tags", postgresql.ARRAY(sa.String(length=60)), server_default="{}", nullable=False, comment="Array of labels stored natively."),
        # category: Optional single category grouping.
        sa.Column("category", sa.String(length=60), nullable=True, comment="Single category grouping name."),
        # position: Double precision fractional position for drag-and-drop.
        sa.Column("position", sa.Float(), server_default="0", nullable=False, comment="Drag-and-drop sort position."),
        # deleted_at: Soft delete timestamp (NULL if active).
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Soft delete timestamp; NULL = active."),
        # created_at: Creation instant in UTC.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Creation timestamp (UTC)."),
        # updated_at: Last modification instant in UTC.
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Last modification instant (UTC)."),
        # Table-level CHECK constraints.
        sa.CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_tasks_priority"),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="ck_tasks_status"),
        sa.CheckConstraint("length(btrim(title)) BETWEEN 1 AND 200", name="ck_tasks_title_len"),
        comment="Todo items owned by one user; soft-deleted via deleted_at.",
    )
    # Indexes on high-traffic task filter columns.
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_due_date", "tasks", ["due_date"])

    # 3. Create the `subtasks` table.
    op.create_table(
        "subtasks",
        # id: Primary key identity.
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False, primary_key=True),
        # task_id: Foreign key to parent task with CASCADE deletion.
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, comment="Parent task FK -> tasks.id."),
        # title: Checklist item summary text.
        sa.Column("title", sa.String(length=200), nullable=False, comment="Checklist item text."),
        # is_done: Checkbox boolean state.
        sa.Column("is_done", sa.Boolean(), server_default="false", nullable=False, comment="Checklist checked status."),
        # position: Integer position inside the parent task's checklist.
        sa.Column("position", sa.Integer(), server_default="0", nullable=False, comment="Checklist display position."),
        # created_at: Creation instant in UTC.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Creation timestamp (UTC)."),
        comment="Checklist items of a task; removed automatically with their task.",
    )
    # Index on task_id to quickly load checklist items for any task.
    op.create_index("ix_subtasks_task_id", "subtasks", ["task_id"])

    # 4. Create the `comments` table.
    op.create_table(
        "comments",
        # id: Primary key identity.
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False, primary_key=True),
        # task_id: Foreign key to parent task with CASCADE deletion.
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, comment="Parent task FK -> tasks.id."),
        # user_id: Foreign key to author with CASCADE deletion.
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="Author FK -> users.id."),
        # body: Text of the comment (1-1000 characters).
        sa.Column("body", sa.Text(), nullable=False, comment="Comment body text."),
        # is_edited: True if the author edited this comment.
        sa.Column("is_edited", sa.Boolean(), server_default="false", nullable=False, comment="Whether the comment was modified after posting."),
        # created_at: Creation instant in UTC.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Creation timestamp (UTC)."),
        # updated_at: Last update instant in UTC.
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Last modification timestamp (UTC)."),
        # Table-level CHECK constraint enforcing 1 to 1000 trimmed characters.
        sa.CheckConstraint("length(btrim(body)) BETWEEN 1 AND 1000", name="ck_comments_body_len"),
        comment="Task discussion entries, 1-1000 chars, authored by a user.",
    )
    # Indexes on task_id and user_id for fast thread retrieval.
    op.create_index("ix_comments_task_id", "comments", ["task_id"])
    op.create_index("ix_comments_user_id", "comments", ["user_id"])

    # 5. Create the `activity_log` table.
    op.create_table(
        "activity_log",
        # id: Primary key identity.
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False, primary_key=True),
        # user_id: Foreign key to actor with SET NULL on delete so history survives.
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, comment="Actor FK -> users.id (SET NULL)."),
        # actor_name: Snapshot of the actor's display name at time of action.
        sa.Column("actor_name", sa.String(length=80), nullable=False, comment="Snapshot of user name at time of write."),
        # action: Action verb from closed vocabulary.
        sa.Column("action", sa.String(length=20), nullable=False, comment="Action verb."),
        # entity_type: Type of resource affected ('task', 'comment', 'user', 'export', 'import').
        sa.Column("entity_type", sa.String(length=20), nullable=False, comment="Resource category affected."),
        # entity_id: ID of the specific resource, or NULL for bulk operations.
        sa.Column("entity_id", sa.Integer(), nullable=True, comment="Affected entity primary key ID."),
        # detail: PostgreSQL JSONB structure holding extra contextual metadata.
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False, comment="Contextual JSON metadata."),
        # created_at: Instant the action occurred.
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False, comment="Action instant (UTC)."),
        # Table-level CHECK constraints.
        sa.CheckConstraint(
            "action IN ('created','updated','deleted','restored','toggled','reordered','imported','exported','login_as')",
            name="ck_activity_action",
        ),
        sa.CheckConstraint(
            "entity_type IN ('task','comment','user','export','import')",
            name="ck_activity_entity_type",
        ),
        comment="Append-only audit trail: actor, action, entity, when, detail.",
    )
    # Index on created_at for fast descending chronological audit queries.
    op.create_index("ix_activity_log_created_at", "activity_log", ["created_at"])


def downgrade() -> None:
    """Revert the migration: drop tables in reverse dependency order."""
    
    # 1. Drop activity_log table and its indexes.
    op.drop_index("ix_activity_log_created_at", table_name="activity_log")
    op.drop_table("activity_log")

    # 2. Drop comments table and its indexes.
    op.drop_index("ix_comments_user_id", table_name="comments")
    op.drop_index("ix_comments_task_id", table_name="comments")
    op.drop_table("comments")

    # 3. Drop subtasks table and its indexes.
    op.drop_index("ix_subtasks_task_id", table_name="subtasks")
    op.drop_table("subtasks")

    # 4. Drop tasks table and its indexes.
    op.drop_index("ix_tasks_due_date", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")

    # 5. Drop users table.
    op.drop_table("users")
