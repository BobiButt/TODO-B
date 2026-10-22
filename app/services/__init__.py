# ============================================================================
# FILE:       backend/app/services/__init__.py
# PURPOSE:    Package marker for backend reusable business logic services.
# HOW IT WORKS: Re-exports common service functions for audit logging and transfer.
# USED BY / DEPENDS ON: routers/*.
# KEY CONCEPTS: Service layer pattern, separation of concerns.
# ============================================================================

"""Business logic services for audit logging and task import/export."""

# Expose log_activity for audit trail recording.
from app.services.activity import log_activity

# Document exported service members.
__all__ = [
    "log_activity",
]
