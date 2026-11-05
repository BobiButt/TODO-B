"""Expand activity_log constraints for chat/system failure logging.

Revision ID: 0004_expand_activity_log
Revises: 0003_add_chat_messages
Create Date: 2026-11-05 00:00:01.000000
"""

from alembic import op

revision = "0004_expand_activity_log"
down_revision = "0003_add_chat_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace check constraints to allow chat/system failures.
    op.drop_constraint("ck_activity_action", "activity_log", type_="check")
    op.drop_constraint("ck_activity_entity_type", "activity_log", type_="check")
    op.create_check_constraint(
        "ck_activity_action",
        "activity_log",
        "action IN ('created','updated','deleted','restored','toggled','reordered','imported','exported','login_as','failed')",
    )
    op.create_check_constraint(
        "ck_activity_entity_type",
        "activity_log",
        "entity_type IN ('task','comment','user','export','import','chat','system')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_activity_action", "activity_log", type_="check")
    op.drop_constraint("ck_activity_entity_type", "activity_log", type_="check")
    op.create_check_constraint(
        "ck_activity_action",
        "activity_log",
        "action IN ('created','updated','deleted','restored','toggled','reordered','imported','exported','login_as')",
    )
    op.create_check_constraint(
        "ck_activity_entity_type",
        "activity_log",
        "entity_type IN ('task','comment','user','export','import')",
    )
