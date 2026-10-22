# ============================================================================
# FILE:       backend-test/app/main.py
# PURPOSE:    FastAPI application entry point. Configures CORS, mounts the API
#             routers under /api/v1, defines the /health heartbeat endpoint and
#             configures interactive OpenAPI documentation.
# HOW IT WORKS: Instantiates the FastAPI app with application metadata.
#             Attaches CORSMiddleware and GZipMiddleware. On startup the
#             database migrations are applied (serverless cold start); on
#             shutdown the connection pool is closed. Every router is mounted
#             under the configured prefix.
# USED BY / DEPENDS ON: api/index.py (Vercel entrypoint), uvicorn
#             app.main:app, tests/conftest.py, frontend HTTP client.
# KEY CONCEPTS: Application factory, lifespan, CORS, API prefix mounting.
# ============================================================================

"""Main FastAPI application entry point and configuration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import dispose_engine, ping_database
from app.migrations import apply_migrations, migration_status
from app.routers.admin import router as admin_router
from app.routers.assistant import router as assistant_router
from app.routers.auth import router as auth_router
from app.routers.comments import router as comments_router
from app.routers.tasks import router as tasks_router
from app.routers.transfer import router as transfer_router
from app.routers.users import router as users_router

# Application-wide logger.
logger = logging.getLogger("taskflow")


def _configure_logging() -> None:
    """Set up a sane logging configuration for a serverless container."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context manager for startup and shutdown events.

    Startup:
        Apply pending database migrations so a fresh database works without
        a manual migration step.

    Shutdown:
        Dispose of the connection pool (Vercel only allows ~500 ms here).

    Parameters:
        app (FastAPI): The running FastAPI application instance.

    Yields:
        None: Hands execution control to the application server.
    """
    _configure_logging()
    logger.info(
        "TaskFlow API starting (environment=%s, vercel=%s)",
        settings.environment,
        settings.is_vercel,
    )

    # Configuration problems are reported, never raised: a boot failure would
    # turn into an opaque platform-level 404 with no usable logs.
    for problem in settings.config_errors:
        logger.error("Configuration problem: %s", problem)

    # Never let a migration problem stop the function from booting: the
    # /health endpoint reports the real state and the logs carry the reason.
    try:
        await apply_migrations()
    except Exception:  # noqa: BLE001 — cold start must not explode
        logger.exception("Migration step failed during startup.")

    yield

    try:
        await dispose_engine()
    except Exception:  # noqa: BLE001 — shutdown is best effort
        logger.warning("Engine disposal during shutdown failed.", exc_info=True)


# Create FastAPI application instance with descriptive title and OpenAPI docs paths.
app = FastAPI(
    title="TaskFlow API",
    description=(
        "Production-quality Todo Application backend with role-based access control, "
        "task comments, CSV/JSON import & export, and administration metrics."
    ),
    version="1.0.0",
    docs_url="/docs" if settings.docs_are_enabled else None,
    redoc_url="/redoc" if settings.docs_are_enabled else None,
    openapi_url="/openapi.json" if settings.docs_are_enabled else None,
    lifespan=lifespan,
)

# Compress JSON responses — a large task list shrinks by ~80%.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Configure Cross-Origin Resource Sharing (CORS) so the Vite dev server and
# the deployed frontend can call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def ensure_schema_ready(request: Request, call_next):
    """Guarantee the schema is migrated before the first request is served.

    Vercel runs the FastAPI lifespan on cold start, so this middleware is
    normally a no-op. It stays as a safety net for runtimes that skip the
    lifespan, and it never delays a request once the migration has run.
    """
    if not request.url.path.endswith("/health"):
        try:
            await apply_migrations()
        except Exception:  # noqa: BLE001 — never block a request
            logger.warning("Deferred migration check failed.", exc_info=True)
    return await call_next(request)


@app.middleware("http")
async def reject_misconfigured_requests(request: Request, call_next):
    """Answer API calls with 503 while the deployment is misconfigured.

    Without this guard a placeholder ``SECRET_KEY`` would silently sign real
    access tokens with a publicly known value. Refusing to serve the API
    turns a security hole into an obvious, self-describing error.
    """
    config_errors = settings.config_errors
    is_api_call = request.url.path.startswith(f"{settings.app_prefix}/")
    is_health_check = request.url.path.endswith("/health")

    if config_errors and is_api_call and not is_health_check and settings.is_production:
        logger.error(
            "Refusing %s %s: configuration incomplete (%s).",
            request.method,
            request.url.path,
            "; ".join(config_errors),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "The API is not configured yet.",
                "errors": config_errors,
            },
        )

    return await call_next(request)


@app.get(
    "/health",
    tags=["System"],
    summary="Health check endpoint",
    description="Returns service health status, database reachability and environment.",
    status_code=status.HTTP_200_OK,
)
async def health_check() -> JSONResponse:
    """Service heartbeat and readiness check.

    Returns:
        JSONResponse: Status dictionary confirming the backend is alive. The
        HTTP status is 503 when the database cannot be reached or the
        configuration is incomplete, so a load balancer takes the instance
        out of rotation.
    """
    config_errors = settings.config_errors
    database_ok = not config_errors and await ping_database()
    payload = {
        "status": "healthy" if database_ok else "degraded",
        "environment": settings.environment,
        "version": "1.0.0",
        "database": "connected" if database_ok else "unreachable",
        "migrations": migration_status(),
        "configuration": {
            "ok": not config_errors,
            "errors": config_errors,
        },
    }
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=payload,
    )


@app.get(
    "/",
    tags=["System"],
    summary="Home / server status",
    description="Returns a welcome message and useful links to verify the backend is running.",
    status_code=status.HTTP_200_OK,
)
async def home() -> JSONResponse:
    """Backend home endpoint.

    Returns:
        JSONResponse: Welcome message with links to the health check and API docs.
    """
    docs_hint = (
        "/docs (interactive OpenAPI documentation)"
        if settings.docs_are_enabled
        else "disabled (set DOCS_ENABLED=true to show it)"
    )
    config_errors = settings.config_errors
    return JSONResponse(
        content={
            "message": "TaskFlow API is running",
            "status": "healthy" if not config_errors else "misconfigured",
            "version": "1.0.0",
            "environment": settings.environment,
            "health": "/health",
            "api_prefix": settings.app_prefix,
            "docs": docs_hint,
            "configuration": {
                "ok": not config_errors,
                "errors": config_errors,
            },
        }
    )


# Mount versioned API routers under the configured prefix (/api/v1).
app.include_router(auth_router, prefix=settings.app_prefix)
app.include_router(users_router, prefix=settings.app_prefix)
app.include_router(transfer_router, prefix=settings.app_prefix)
app.include_router(tasks_router, prefix=settings.app_prefix)
app.include_router(comments_router, prefix=settings.app_prefix)
app.include_router(admin_router, prefix=settings.app_prefix)
app.include_router(assistant_router, prefix=settings.app_prefix)

# The same health check under the versioned prefix, handy for monitoring
# tools that only know the API base URL.
app.add_api_route(
    f"{settings.app_prefix}/health",
    health_check,
    methods=["GET"],
    tags=["System"],
    summary="Versioned health check",
    include_in_schema=False,
)
