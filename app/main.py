"""Main FastAPI application entry point and configuration."""
from __future__ import annotations

import asyncio
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

logger = logging.getLogger("taskflow")


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    logger.info(
        "TaskFlow API starting (environment=%s, vercel=%s)",
        settings.environment,
        settings.is_vercel,
    )

    for problem in settings.config_errors:
        logger.error("Configuration problem: %s", problem)

    # Migrations are timeout-protected so startup can never hang.
    if settings.auto_migrate:
        try:
            await asyncio.wait_for(apply_migrations(), timeout=45.0)
            logger.info("Startup migrations completed.")
        except asyncio.TimeoutError:
            logger.warning(
                "Startup migrations timed out after 45s. "
                "Run `alembic upgrade head` manually to finish."
            )
        except Exception:
            logger.exception("Startup migrations failed; continuing anyway.")

    yield

    try:
        await dispose_engine()
    except Exception:
        logger.warning("Engine disposal during shutdown failed.", exc_info=True)


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

app.add_middleware(GZipMiddleware, minimum_size=1024)

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
async def reject_misconfigured_requests(request: Request, call_next):
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


@app.get("/health", tags=["System"])
async def health_check() -> JSONResponse:
    config_errors = settings.config_errors
    database_ok = not config_errors and await ping_database()
    payload = {
        "status": "healthy" if database_ok else "degraded",
        "environment": settings.environment,
        "version": "1.0.0",
        "database": "connected" if database_ok else "unreachable",
        "migrations": migration_status(),
        "configuration": {"ok": not config_errors, "errors": config_errors},
    }
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content=payload,
    )


@app.get("/", tags=["System"])
async def home() -> JSONResponse:
    config_errors = settings.config_errors
    docs_hint = (
        "/docs (interactive OpenAPI documentation)"
        if settings.docs_are_enabled
        else "disabled (set DOCS_ENABLED=true to show it)"
    )
    return JSONResponse(
        content={
            "message": "TaskFlow API is running",
            "status": "healthy" if not config_errors else "misconfigured",
            "version": "1.0.0",
            "environment": settings.environment,
            "health": "/health",
            "api_prefix": settings.app_prefix,
            "docs": docs_hint,
            "configuration": {"ok": not config_errors, "errors": config_errors},
        }
    )


app.include_router(auth_router, prefix=settings.app_prefix)
app.include_router(users_router, prefix=settings.app_prefix)
app.include_router(transfer_router, prefix=settings.app_prefix)
app.include_router(tasks_router, prefix=settings.app_prefix)
app.include_router(comments_router, prefix=settings.app_prefix)
app.include_router(admin_router, prefix=settings.app_prefix)
app.include_router(assistant_router, prefix=settings.app_prefix)

app.add_api_route(
    f"{settings.app_prefix}/health",
    health_check,
    methods=["GET"],
    tags=["System"],
    summary="Versioned health check",
    include_in_schema=False,
)