# ============================================================================
# FILE:       backend/app/__init__.py
# PURPOSE:    Marks the `app/` directory as a Python *package*, which is what
#             lets us write `from app.config import settings` and lets uvicorn
#             find `app.main:app`.
# HOW IT WORKS: Python executes this file when the package is imported. It is
#             intentionally almost empty — its existence is the feature. The
#             version string below is the only content we add.
# USED BY / DEPENDS ON: imported implicitly by every module under app/.
# KEY CONCEPTS: Python packages, `__init__.py` as package marker.
# ============================================================================

"""TaskFlow backend package: FastAPI application, models, schemas, routers."""

# __version__ identifies the API build; surfaced in the /health response so a
# client (or a curious learner) can tell which code is deployed.
__version__ = "1.0.0"
