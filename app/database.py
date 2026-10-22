# ============================================================================
# FILE:       backend-test/app/database.py
# PURPOSE:    Own everything that touches the database connection: the async
#             engine, the session factory, the shared declarative Base that
#             all models inherit from, and the `get_db` dependency that gives
#             each request its own session.
# HOW IT WORKS: On import we build one async engine (a pool of reusable
#             connections) from settings.database_url. Routes ask for a session
#             via FastAPI's Depends(get_db); the session is closed by this
#             generator even if the route raises an error.
# USED BY / DEPENDS ON: imported by every model (Base), every router (get_db),
#             alembic/env.py, migrations.py, tests.
# KEY CONCEPTS: async engine, connection pooling, session-per-request,
#             declarative base, dependency injection.
# ============================================================================

"""Async SQLAlchemy engine, session factory, declarative Base, get_db."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model.

    Why a single Base? All models inherit from it, so `Base.metadata`
    automatically knows every table — Alembic uses that knowledge to generate
    migrations, and tests can create the whole schema with one call.
    """

    # Nothing extra to configure: SQLAlchemy 2.0 infers column types from
    # `Mapped[...]` annotations. The class exists to be inherited.


def _split_ssl_from_url(url: str) -> tuple[str, bool]:
    """Remove an `ssl` query parameter from a connection URL.

    asyncpg accepts the `ssl` keyword exactly once. When the URL already
    carries it (added by the URL normalizer) it must not also be passed
    through `connect_args`, otherwise asyncpg raises "got multiple values
    for keyword argument 'ssl'".

    Parameters:
        url: the connection URL.

    Returns:
        tuple[str, bool]: the (possibly rewritten) URL and whether it still
        contains an `ssl` parameter.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "ssl" not in query:
        return url, False
    query.pop("ssl")
    return (
        urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        ),
        True,
    )


def _build_engine() -> AsyncEngine:
    """Create the application's async SQLAlchemy engine.

    The pool is deliberately small and short-lived: a serverless function
    keeps one warm container per region, and every extra pooled connection
    counts against the database provider's connection limit.

    Creating the engine must never raise. If ``DATABASE_URL`` is missing or
    still a placeholder, SQLAlchemy would refuse to build the engine and the
    import of ``app.main`` would explode — which a serverless platform
    reports as a bare 404 with no logs. Instead a syntactically valid dummy
    URL is used; the connection attempt then fails at request time and the
    real reason is reported by ``/health``.

    Returns:
        AsyncEngine: pooled connection object used for the app's whole life.
    """
    url = settings.database_url
    connect_args: dict[str, object] = dict(settings.connect_args)

    if not url:
        # Placeholder URL: valid syntax, unreachable host. The engine can be
        # created, the app can boot, and /health reports the misconfiguration.
        url = "postgresql+asyncpg://invalid:invalid@127.0.0.1:1/invalid"
    else:
        try:
            # SQLAlchemy validates the URL when the engine is created; an
            # unsupported scheme (e.g. a bare "postgres://" that the
            # normalizer missed) raises here.
            make_url(url)
        except Exception:  # noqa: BLE001 — never break the import
            url = "postgresql+asyncpg://invalid:invalid@127.0.0.1:1/invalid"

    if connect_args.get("ssl") is not None:
        # The driver keyword wins — strip it from the URL to avoid a clash.
        url, _ = _split_ssl_from_url(url)

    return create_async_engine(
        url,
        echo=settings.db_echo,
        pool_pre_ping=True,  # drop connections the provider closed
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_timeout=settings.db_pool_timeout,
        connect_args=connect_args,
    )


# The one engine for the running application (created once at import time).
engine: AsyncEngine = _build_engine()

# async_sessionmaker builds new AsyncSession objects on demand.
# expire_on_commit=False: after commit() objects keep their values, so routers
# can read attributes without triggering a lazy refresh query (which would
# fail outside the request scope anyway).
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,  # explicit flush() calls only — avoids surprise INSERTs
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one database session per request.

    Yields:
        AsyncSession: an open session bound to this request.

    Side effects / errors:
        - On normal exit the session is closed (connection returned to pool).
        - On an exception inside the route the transaction is rolled back
          first, so a half-written request never leaves the DB inconsistent.
    """
    async with AsyncSessionLocal() as session:
        try:
            # Hand control to the route handler.
            yield session
        except Exception:
            # Any unhandled error: undo uncommitted work, then re-raise so the
            # client still receives the proper error status code.
            await session.rollback()
            raise
        finally:
            # Always release the connection back to the pool.
            await session.close()


async def ping_database() -> bool:
    """Check that PostgreSQL answers a trivial query (for GET /health).

    Returns:
        bool: True when a round-trip query succeeded, False otherwise.

    Errors:
        Swallows every exception on purpose — a health endpoint must report
        "database down" instead of crashing with a 500.
    """
    try:
        # async with engine.connect() checks out a pooled connection...
        async with engine.connect() as connection:
            # ...and runs the cheapest possible query.
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — health checks must never propagate
        return False


async def dispose_engine() -> None:
    """Close all pooled connections (used by tests and graceful shutdown).

    Returns: None.

    Side effects: after this call the engine opens fresh connections on demand.
    """
    await engine.dispose()
