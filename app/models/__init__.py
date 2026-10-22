# ============================================================================
# FILE:       backend/app/models/__init__.py
# PURPOSE:    Import every ORM model into one place so that
#             `Base.metadata` always contains ALL tables. Alembic (autogenerate)
#             and tests import *this package*, not individual files — if a
#             model is missing here, its table silently disappears from
#             migrations.
# HOW IT WORKS: Each `from app.models.x import X` executes that module, which
#             registers the class on Base.metadata. The explicit
#             __all__ list documents what the package exports.
# USED BY / DEPENDS ON: imported by alembic/env.py, seed.py, tests, and any
#             module that needs several models at once.
# KEY CONCEPTS: import side effects, metadata registration, circular-import
#             avoidance (modules import Base from app.database, never from here).
# ============================================================================

"""All ORM models — import this package to register every table."""

# The shared base class (defined in database.py, imported here for convenience)
from app.database import Base

# Individual model classes, one per table:
from app.models.user import User            # users table
from app.models.task import Task            # tasks table
from app.models.subtask import Subtask      # subtasks (checklist items)
from app.models.comment import Comment      # task comments
from app.models.activity import ActivityLog  # admin audit trail

# __all__ defines what `from app.models import *` gives the caller, and acts as
# a checklist: five tables exist, five names are exported.
__all__ = [
    "Base",
    "User",
    "Task",
    "Subtask",
    "Comment",
    "ActivityLog",
]
