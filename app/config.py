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

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})

_LIBPQ_ONLY_PARAMS = ("channel_binding", "sslcert", "sslkey", "sslrootcert")

_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "your-",
    "example",
    "placeholder",
    "xxx",
    
)


def normalize_database_url(url: str) -> str:
    """Return a PostgreSQL URL that SQLAlchemy's asyncpg dialect can open."""
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

    sslmode = query.pop("sslmode", None)
    for key in _LIBPQ_ONLY_PARAMS:
        query.pop(key, None)

    host = (parts.hostname or "").lower()
    if "ssl" not in query:
        if sslmode:
            query["ssl"] = sslmode
        elif host and host not in _LOCAL_HOSTS:
            query["ssl"] = "require"

    return urlunsplit(
        (scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _is_placeholder(value: str) -> bool:
    """bool: True when a setting still carries an example/placeholder value."""
    lowered = value.strip().lower()
    return any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS)


def parse_origin_list(raw: str | list[str] | None) -> list[str]:
    """Turn the raw CORS_ORIGINS value into a clean list of origins."""
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
    """Every runtime setting of the TaskFlow backend."""

    # --- Application -------------------------------------------------------
    environment: str = "production"
    app_prefix: str = "/api/v1"
    log_level: str = "INFO"
    docs_enabled: bool | None = None
        # --- Brevo Email API ---
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    # --- Database ----------------------------------------------------------
    database_url: str = ""
    test_database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 2
    db_max_overflow: int = 2
    db_pool_recycle: int = 280
    db_pool_timeout: int = 10
    db_ssl: str = "auto"

    auto_migrate: bool = True
    migration_lock_timeout: int = 30
    migration_timeout: int = 120

    # --- CORS --------------------------------------------------------------
    # FIX: localhost:5173 (Vite dev server) added to the default so a fresh
    # local run does not get blocked by CORS. In production this is overridden
    # by the CORS_ORIGINS environment variable.
    # cors_origins: str = "https://todo-f-orcin.vercel.app"
    cors_origins: str = "https://todo-f-orcin.vercel.app,https://todo-b-nu.vercel.app"    
    cors_allow_credentials: bool = True
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
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: str = ""
    groq_model: str = "groq/compound"

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("test_database_url", mode="after")
    @classmethod
    def _normalize_test_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("db_ssl", mode="before")
    @classmethod
    def _normalize_db_ssl(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @property
    def config_errors(self) -> list[str]:
        """list[str]: Human-readable description of every missing setting."""
        problems: list[str] = []

        if not self.database_url:
            problems.append("DATABASE_URL is not set")
        elif self.is_production:
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
        return bool(self.config_errors)

    @property
    def cors_origin_list(self) -> list[str]:
        return parse_origin_list(self.cors_origins)

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "local"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_vercel(self) -> bool:
        return bool(os.getenv("VERCEL"))

    @property
    def docs_are_enabled(self) -> bool:
        if self.docs_enabled is not None:
            return self.docs_enabled
        return True

    @property
    def connect_args(self) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if self.db_ssl == "require":
            args["ssl"] = "require"
        elif self.db_ssl == "disable":
            args["ssl"] = "disable"
        return args


settings = Settings()