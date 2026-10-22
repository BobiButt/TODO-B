# ============================================================================
# FILE:       backend-test/app/migrations.py
# PURPOSE:    Run the Alembic migrations automatically when the serverless
#             function cold-starts, so a fresh database is usable without a
#             manual `alembic upgrade head` step.
# HOW IT WORKS: `apply_migrations()` takes a PostgreSQL advisory lock (so two
#             warm instances can never migrate at the same time), then runs
#             `alembic upgrade head` inside a worker thread. If Alembic is not
#             available in the bundle, it falls back to creating the schema
#             straight from the SQLAlchemy models.
# USED BY / DEPENDS ON: main.py (lifespan + first-request middleware).
# KEY CONCEPTS: advisory locks, asyncio.to_thread, idempotent DDL.
# ============================================================================

"""Automatic database migrations for serverless cold starts."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.database import Base, engine

logger = logging.getLogger("taskflow.migrations")

# Arbitrary but stable key for the PostgreSQL advisory lock. Every instance
# of this application uses the same key, so migrations are serialized.
_ADVISORY_LOCK_KEY = 728364512

# Module level state: "has this container already migrated?" — the answer is
# cached for the lifetime of the warm container.
_state: dict[str, Any] = {"done": False, "ok": False, "error": ""}


def _alembic_config():
    """Build an Alembic Config with absolute paths.

    Absolute paths matter on Vercel: the function's working directory is the
    bundle root, but relying on that is fragile when the bundle is relocated.

    Returns:
        alembic.config.Config: a ready-to-use configuration object.
    """
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("prepend_sys_path", str(project_root))
    return config


def _upgrade_sync() -> None:
    """Run `alembic upgrade head` (blocking, called from a worker thread)."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


async def _create_all_fallback() -> None:
    """Create any missing tables straight from the SQLAlchemy models."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def apply_migrations(force: bool = False) -> bool:
    """Bring the database schema up to date.

    Parameters:
        force: re-run even if this container already migrated.

    Returns:
        bool: True when the schema is known to be up to date.
    """
    if not settings.auto_migrate and not force:
        _state["done"] = True
        _state["ok"] = True
        return True

    if _state["done"] and not force:
        return bool(_state["ok"])

    try:
        async with engine.connect() as lock_connection:
            # Serialize concurrent cold starts across function instances.
            # set_config() is used because plain `SET` does not accept bind
            # parameters.
            await lock_connection.execute(
                text("SELECT set_config('lock_timeout', :timeout, false)"),
                {"timeout": f"{settings.migration_lock_timeout}s"},
            )
            await lock_connection.execute(
                text("SELECT pg_advisory_lock(CAST(:key AS bigint))"),
                {"key": _ADVISORY_LOCK_KEY},
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(_upgrade_sync),
                    timeout=settings.migration_timeout,
                )
            finally:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(CAST(:key AS bigint))"),
                    {"key": _ADVISORY_LOCK_KEY},
                )
        logger.info("Database migrations applied successfully.")
        _state.update({"done": True, "ok": True, "error": ""})
        return True
    except Exception as exc:  # noqa: BLE001 — never break the cold start
        logger.warning("Alembic migration failed (%s); falling back to create_all.", exc)

    try:
        await _create_all_fallback()
        logger.info("Schema created from models (Alembic fallback).")
        _state.update({"done": True, "ok": True, "error": ""})
        return True
    except Exception as exc:  # noqa: BLE001 — report, but keep serving
        logger.exception("Database migration failed: %s", exc)
        _state.update({"done": True, "ok": False, "error": str(exc)})
        return False


def migration_status() -> dict[str, Any]:
    """dict: The current migration state of this container."""
    return {
        "auto_migrate": settings.auto_migrate,
        "ran": _state["done"],
        "ok": _state["ok"],
        "error": _state["error"],
    }
