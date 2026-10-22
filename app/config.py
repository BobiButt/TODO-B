# ============================================================================
# FILE:       backend-test/app/config.py
# PURPOSE:    Single source of truth for every runtime setting. Values come
#             from real environment variables first (Vercel dashboard / CLI),
#             then from a .env file in the project root, then from safe
#             development defaults.
# HOW IT WORKS: pydantic-settings' BaseSettings maps KEY=VALUE onto annotated
#             Python fields. Complex fields (the CORS list) are parsed from
#             JSON or comma-separated text. The database URL is normalized so
#             that the asyncpg dialect can open it no matter which provider
#             (Neon, Supabase, Railway, local Postgres) issued it.
# USED BY / DEPENDS ON: database.py, main.py, migrations.py, alembic/env.py,
#             services/*, routers/*.
# KEY CONCEPTS: 12-factor config, import-time singleton, URL normalization.
# ============================================================================

"""Typed application settings loaded from the environment / .env file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend-test/app/config.py -> parents[1] -> backend-test/ (the project root).
# Using an absolute path means the .env file is found no matter what the
# current working directory is (Vercel runs functions from /var/task).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Hosts that never need TLS.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})

# libpq-only query parameters that asyncpg does not understand and would
# reject with "unexpected keyword argument".
_LIBPQ_ONLY_PARAMS = ("channel_binding", "sslcert", "sslkey", "sslrootcert")

# Value that must never reach production.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "your-",
    "example",
    "placeholder",
    "xxx",
    "todo",
)


def normalize_database_url(url: str) -> str:
    """Return a PostgreSQL URL that SQLAlchemy's asyncpg dialect can open.

    Problems this solves for a Vercel deployment:
      * Providers hand out ``postgres://`` or ``postgresql://`` URLs. The
        asyncpg driver is selected with the ``postgresql+asyncpg://`` scheme.
      * ``?sslmode=require`` is a libpq concept. asyncpg would raise
        ``TypeError: connect() got an unexpected keyword argument 'sslmode'``,
        so it is translated to the asyncpg ``ssl`` parameter.
      * Hosted databases (Neon, Supabase, Railway) require TLS by default, so
        ``ssl=require`` is added automatically for non-local hosts.

    Parameters:
        url: the raw DATABASE_URL value.

    Returns:
        str: a normalized, asyncpg-compatible connection URL.
    """
    if not url or not url.strip():
        return url

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()

    if scheme in {
        "postgres",
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
        "postgresql+asyncpg",
    }:
        scheme = "postgresql+asyncpg"
    elif not scheme:
        scheme = "postgresql+asyncpg"

    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    # asyncpg does not understand libpq's sslmode keyword.
    sslmode = query.pop("sslmode", None)
    for key in _LIBPQ_ONLY_PARAMS:
        query.pop(key, None)

    host = (parts.hostname or "").lower()
    if "ssl" not in query:
        if sslmode:
            query["ssl"] = sslmode
        elif host and host not in _LOCAL_HOSTS:
            # Hosted Postgres (Neon / Supabase / Railway) mandates TLS.
            query["ssl"] = "require"

    return urlunsplit(
        (scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _is_placeholder(value: str) -> bool:
    """bool: True when a setting still carries an example/placeholder value."""
    lowered = value.strip().lower()
    return any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS)


def parse_origin_list(raw: str | list[str] | None) -> list[str]:
    """Turn the raw CORS_ORIGINS value into a clean list of origins.

    Three notations are accepted:
      * JSON     — ``["https://a.app", "https://b.app"]``
      * CSV      — ``https://a.app,https://b.app``
      * a single — ``https://a.app``

    Trailing slashes are removed: a browser sends the Origin header without
    one, so "https://a.app/" would never match.

    Parameters:
        raw: the raw configuration value.

    Returns:
        list[str]: de-duplicated origins without trailing slashes.
    """
    if raw is None:
        return []

    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except ValueError:
                decoded = [text]
            items = decoded if isinstance(decoded, list) else [decoded]
        else:
            items = text.split(",")

    cleaned: list[str] = []
    for item in items:
        origin = str(item).strip().rstrip("/")
        if origin and origin not in cleaned:
            cleaned.append(origin)
    return cleaned


class Settings(BaseSettings):
    """Every runtime setting of the TaskFlow backend.

    All fields are optional and validation never raises, so importing this
    module can never break a deployment. Missing production values are
    collected by :attr:`config_errors` and reported through ``/health``,
    the startup log and the home endpoint.
    """

    # --- Application -------------------------------------------------------
    environment: str = "production"
    app_prefix: str = "/api/v1"
    log_level: str = "INFO"
    # Force /docs on or off; None means "follow the environment".
    docs_enabled: bool | None = None

    # --- Database ----------------------------------------------------------
    database_url: str = ""
    test_database_url: str = ""
    db_echo: bool = False
    # Serverless-safe pool: a few connections per warm instance, recycled
    # before the provider kills idle connections.
    db_pool_size: int = 2
    db_max_overflow: int = 2
    db_pool_recycle: int = 280
    db_pool_timeout: int = 10
    # auto | require | disable — "auto" = TLS everywhere except localhost.
    db_ssl: str = "auto"

    # Apply database migrations automatically on cold start.
    auto_migrate: bool = True
    # Seconds to wait for the migration advisory lock.
    migration_lock_timeout: int = 30
    # Seconds to wait for the whole migration run.
    migration_timeout: int = 120

    # --- CORS --------------------------------------------------------------
    # Kept as a raw string on purpose: pydantic-settings tries to JSON-decode
    # any list-typed field before validators run, which would reject the
    # convenient comma-separated form. The parsed list is exposed through the
    # `cors_origin_list` property below.
    cors_origins: str = "http://localhost:5173"
    cors_allow_credentials: bool = True
    # Extra regex for wildcard origins such as https://*.vercel.app
    cors_origin_regex: str = r"https://.*\.vercel\.app"

    # --- Uploads -----------------------------------------------------------
    max_import_bytes: int = 2_097_152

    # --- JWT ---------------------------------------------------------------
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    # --- SMTP / email OTP --------------------------------------------------
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@example.com"
    otp_expire_minutes: int = 10

    # --- Google OAuth ------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- AI assistant providers -------------------------------------------
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # model_config tells pydantic-settings where to look for the .env file.
    # The absolute path first, the relative path second (last one wins).
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Rewrite DATABASE_URL into an asyncpg-compatible form."""
        return normalize_database_url(value)

    @field_validator("test_database_url", mode="after")
    @classmethod
    def _normalize_test_database_url(cls, value: str) -> str:
        """Rewrite TEST_DATABASE_URL into an asyncpg-compatible form."""
        return normalize_database_url(value)

    @field_validator("db_ssl", mode="before")
    @classmethod
    def _normalize_db_ssl(cls, value: Any) -> Any:
        """Lowercase the SSL mode keyword."""
        return value.strip().lower() if isinstance(value, str) else value

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def config_errors(self) -> list[str]:
        """list[str]: Human-readable description of every missing setting.

        Importing this module must NEVER raise. A serverless platform that
        cannot import the entrypoint answers every request with an opaque
        edge-level 404 and no logs at all, which is impossible to debug.
        Instead the problems are collected here and reported through
        ``/health`` (HTTP 503), the startup log and the home endpoint.

        In ``development`` only a completely missing ``DATABASE_URL`` counts
        as an error; placeholder secrets are tolerated so that a fresh
        checkout still runs locally. In ``production`` every placeholder is
        reported, because a placeholder ``SECRET_KEY`` must never be used to
        sign real access tokens.

        Returns:
            list[str]: One sentence per problem; empty when the
            configuration is complete.
        """
        problems: list[str] = []

        if not self.database_url:
            problems.append("DATABASE_URL is not set")
        elif self.is_production:
            # Only the host is inspected for placeholder markers: a password
            # may legitimately contain a word like "todo" or "example" and
            # must not be mistaken for an unfinished configuration.
            host = (urlsplit(self.database_url).hostname or "").lower()
            if _is_placeholder(host):
                problems.append(
                    "DATABASE_URL still contains a placeholder host "
                    "(set the real database URL as a Vercel Environment Variable)"
                )
            elif self.is_vercel and host in _LOCAL_HOSTS:
                problems.append(
                    "DATABASE_URL points at localhost, which does not exist on Vercel"
                )

        if self.is_production:
            if not self.secret_key:
                problems.append("SECRET_KEY is not set")
            elif _is_placeholder(self.secret_key):
                problems.append(
                    "SECRET_KEY is still the placeholder value "
                    "(generate one with: openssl rand -hex 32)"
                )

        return problems

    @property
    def is_misconfigured(self) -> bool:
        """bool: True when a required setting is missing or a placeholder."""
        return bool(self.config_errors)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        """list[str]: The allowed browser origins, normalized.

        Accepts JSON, comma-separated text or a single origin.
        """
        return parse_origin_list(self.cors_origins)

    @property
    def is_development(self) -> bool:
        """bool: True when running locally (Swagger UI + verbose errors)."""
        return self.environment.lower() in {"development", "dev", "local"}

    @property
    def is_production(self) -> bool:
        """bool: True when the environment is explicitly "production"."""
        return self.environment.lower() == "production"

    @property
    def is_vercel(self) -> bool:
        """bool: True when running inside a Vercel function."""
        return bool(os.getenv("VERCEL"))

    @property
    def docs_are_enabled(self) -> bool:
        """bool: Whether the interactive OpenAPI docs should be served.

        The docs are enabled by default so that ``/docs`` and ``/openapi.json``
        are reachable in every environment. Set ``DOCS_ENABLED=false`` to hide
        them again.
        """
        if self.docs_enabled is not None:
            return self.docs_enabled
        return True

    @property
    def connect_args(self) -> dict[str, Any]:
        """dict: Extra keyword arguments handed to the asyncpg driver.

        ``ssl`` is passed explicitly instead of through the URL so that the
        value is validated by asyncpg itself.
        """
        args: dict[str, Any] = {}
        if self.db_ssl == "require":
            args["ssl"] = "require"
        elif self.db_ssl == "disable":
            args["ssl"] = "disable"
        return args


# `settings` is the single configuration object imported everywhere:
#     from app.config import settings
settings = Settings()
