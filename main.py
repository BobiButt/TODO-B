# ============================================================================
# FILE:       backend-test/main.py
# PURPOSE:    Vercel entrypoint. Exposes the FastAPI application as a
#             top-level `app` object, which is the name Vercel's Python
#             runtime loads as the function handler.
# HOW IT WORKS: Vercel resolves a Python entrypoint from the project root
#             (app.py / index.py / server.py / main.py / wsgi.py / asgi.py).
#             Because the entrypoint lives at the root of the project, every
#             request — not just /api/* — is routed to this function, which
#             is what makes `/`, `/health` and `/docs` reachable.
#             The project root is prepended to sys.path so that the `app`
#             package resolves no matter which directory the runtime picks
#             as the working directory.
# USED BY / DEPENDS ON: Vercel (function handler), `uvicorn main:app`.
# KEY CONCEPTS: ASGI entrypoint, sys.path bootstrapping, catch-all routing.
# ============================================================================

"""Vercel serverless entry point for the TaskFlow FastAPI backend."""

import os
import sys

# Absolute path of the project root (the folder that contains `app/`).
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# The Python runtime starts the function with the bundle root as the working
# directory, but the interpreter's sys.path only contains the directory of
# this file. Prepending the project root makes the `app` package importable.
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Make the same root visible to any subprocess (Alembic runs migrations in a
# worker thread and imports `app.config` from there).
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (_PROJECT_ROOT, os.environ.get("PYTHONPATH", "")) if part
)

from app.main import app  # noqa: E402  (import must follow the sys.path fix)

# Vercel's Python runtime looks for `app` (the ASGI application) in this file.
handler = app

__all__ = ["app", "handler"]
