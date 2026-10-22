# ============================================================================
# FILE:       backend/app/services/assistant_log.py
# PURPOSE:    Detailed diagnostics and logging for the TaskFlow chatbot so we
#             can see EXACTLY why an AI provider call failed (rate limit,
#             quota, invalid key, timeout, model not found, ...) and fix it.
# HOW IT WORKS:
#   - A dedicated logger ("taskflow.assistant") writes to a rotating file
#     backend/logs/assistant.log AND to the console (visible in run-linux.sh).
#   - classify_http_error()/classify_exception() turn raw provider failures
#     into a stable category + human-readable explanation.
#   - record_attempt() appends one structured entry per provider attempt to
#     the request's `diagnostics` list (returned to the frontend) and writes
#     the same information to the log file.
# USED BY / DEPENDS ON: services/assistant.py, routers/assistant.py,
#                       services/local_commands.py, tests/test_assistant.py.
# KEY CONCEPTS: Rotating file handler, error classification, request-scoped
#               diagnostics list, ProviderError.
# ============================================================================

"""Detailed logging and failure diagnostics for the TaskFlow assistant."""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx

# Dedicated logger name — never collides with uvicorn's own loggers.
LOGGER_NAME = "taskflow.assistant"

# backend-test/logs/assistant.log  (this file lives at backend-test/app/services/...)
_PROJECT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def _resolve_log_dir() -> Path:
    """Return a writable directory for the rotating log file.

    A Vercel function's bundle is read-only, so the project directory cannot
    be written to. In that case the log file is placed in the container's
    temporary directory instead (still visible in the function logs through
    the console handler).

    Returns:
        Path: a directory that is writable in the current runtime.
    """
    # Explicit override always wins.
    configured = os.getenv("ASSISTANT_LOG_DIR")
    if configured:
        candidate = Path(configured)
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            pass

    try:
        _PROJECT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        return _PROJECT_LOG_DIR
    except OSError:
        # Read-only bundle (serverless) — fall back to the temp directory.
        fallback = Path(tempfile.gettempdir()) / "taskflow-logs"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback


# Resolved once at import time; the path never changes during a run.
LOG_DIR = _resolve_log_dir()
LOG_FILE = LOG_DIR / "assistant.log"

# Cap stored/sent error snippets so responses and log lines stay small.
_MAX_DETAIL = 300


class ProviderError(Exception):
    """A provider call failed in a classified, human-explainable way.

    Attributes:
        category (str): Stable machine-readable failure kind
            (rate_limited, quota_exceeded, auth_error, model_not_found,
            invalid_request, server_error, timeout, connection_error,
            invalid_response, unknown).
        http_status (int | None): HTTP status code when one was received.
        detail (str): Human-readable explanation shown in diagnostics.
    """

    def __init__(self, category: str, detail: str, http_status: int | None = None):
        super().__init__(detail)
        self.category = category
        self.http_status = http_status
        self.detail = detail


def configure_assistant_logging() -> logging.Logger:
    """Configure (idempotently) and return the chatbot logger.

    Returns:
        logging.Logger: Logger writing to backend-test/logs/assistant.log
        (rotating, 1 MB x 3 backups) and to the process stdout/stderr.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_taskflow_configured", False):
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [assistant] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    # File handler — the durable log we inspect when debugging provider issues.
    # On a read-only bundle (Vercel) this is skipped and only the console
    # handler is used, so the import can never break the function.
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only filesystem fallback
        pass

    # Console handler — same lines appear in the run-linux.sh output.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Our handlers only — don't duplicate into the root/uvicorn loggers.
    logger.propagate = False
    logger._taskflow_configured = True  # type: ignore[attr-defined]
    return logger


# Module-level logger, configured once at import time.
assistant_logger = configure_assistant_logging()


def _snippet(text: str | None) -> str:
    """Return a single-line, truncated excerpt of a provider response body."""
    if not text:
        return ""
    return " ".join(text.split())[:_MAX_DETAIL]



def classify_http_error(status_code: int, body_text: str = "") -> tuple[str, str]:
    """Classify an HTTP error status from a provider into (category, detail).

    Parameters:
        status_code (int): HTTP status code returned by the provider.
        body_text (str): Response body excerpt (used for finer-grained causes).

    Returns:
        tuple[str, str]: Stable category plus a human-readable explanation.
    """
    body = (body_text or "").lower()
    snippet = _snippet(body_text)
    suffix = f" Provider response: {snippet}" if snippet else ""

    # Body markers first — providers often signal limits in the body only
    # (e.g. Gemini free tier returns 403 RESOURCE_EXHAUSTED for quota).
    if any(marker in body for marker in ("rate limit", "rate_limit", "ratelimit", "too many requests")):
        return "rate_limited", f"Rate limit reached (HTTP {status_code}).{suffix}"
    if any(marker in body for marker in ("quota", "resource_exhausted", "billing", "insufficient", "credit")):
        return "quota_exceeded", f"Quota or credit limit reached (HTTP {status_code}).{suffix}"
    if status_code == 429:
        return "rate_limited", f"Rate limit reached (HTTP 429).{suffix}"
    if status_code == 402:
        return "quota_exceeded", f"Payment required — quota or credit limit reached (HTTP 402).{suffix}"
    if status_code in (401, 403):
        return (
            "auth_error",
            f"Authentication failed (HTTP {status_code}) — check the API key in backend/.env.{suffix}",
        )
    if "model" in body and any(marker in body for marker in ("not found", "unknown", "does not exist", "invalid")):
        return "model_not_found", f"Model not found or not available (HTTP {status_code}).{suffix}"
    if 400 <= status_code < 500:
        return "invalid_request", f"Provider rejected the request (HTTP {status_code}).{suffix}"
    if status_code >= 500:
        return "server_error", f"Provider server error (HTTP {status_code}).{suffix}"
    return "unknown", f"Unexpected HTTP status {status_code}.{suffix}"


def classify_exception(exc: BaseException) -> tuple[str, int | None, str]:
    """Classify any provider exception into (category, http_status, detail)."""
    if isinstance(exc, ProviderError):
        return exc.category, exc.http_status, exc.detail[:_MAX_DETAIL]
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = exc.response.text
        except Exception:  # pragma: no cover - unreadable body
            body = ""
        category, detail = classify_http_error(status, body)
        return category, status, detail
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", None, f"Request to the provider timed out: {exc}"[:_MAX_DETAIL]
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "connection_error", None, f"Could not reach the provider: {exc}"[:_MAX_DETAIL]
    if isinstance(exc, httpx.TransportError):
        return "connection_error", None, f"Transport error talking to the provider: {exc}"[:_MAX_DETAIL]
    if isinstance(exc, httpx.HTTPError):
        return "connection_error", None, f"HTTP error calling the provider: {exc}"[:_MAX_DETAIL]
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        return "invalid_response", None, f"Provider returned an unreadable response: {exc}"[:_MAX_DETAIL]
    return "unknown", None, f"{type(exc).__name__}: {exc}"[:_MAX_DETAIL]



def record_attempt(
    diagnostics: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    phase: str,
    ok: bool,
    elapsed_ms: int,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Append one classified provider attempt to `diagnostics` and log it.

    Parameters:
        diagnostics (list[dict]): Request-scoped list accumulating attempts.
        provider (str): Provider name (openrouter/gemini/groq).
        model (str): Model identifier sent to the provider.
        phase (str): "chat" for the first round, "tool_loop" afterwards.
        ok (bool): Whether the attempt succeeded.
        elapsed_ms (int): Wall-clock duration of the attempt.
        error (BaseException | None): The failure (when ok is False).

    Returns:
        dict: The diagnostics entry that was appended.
    """
    if ok:
        category, http_status, detail = "ok", None, "Provider responded successfully."
    else:
        category, http_status, detail = classify_exception(
            error if error is not None else RuntimeError("unknown error")
        )

    entry: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "phase": phase,
        "ok": ok,
        "category": category,
        "http_status": http_status,
        "detail": detail,
        "elapsed_ms": elapsed_ms,
    }
    diagnostics.append(entry)
    assistant_logger.info(
        "provider_attempt phase=%s provider=%s model=%s ok=%s category=%s http_status=%s elapsed_ms=%s detail=%r",
        phase,
        provider,
        model,
        ok,
        category,
        http_status,
        elapsed_ms,
        detail,
    )
    return entry


def summarize_failures(diagnostics: list[dict[str, Any]]) -> str:
    """Build a one-line summary of the last failure per provider.

    Parameters:
        diagnostics (list[dict]): Request-scoped diagnostics list.

    Returns:
        str: e.g. "openrouter: rate_limited — Rate limit reached (HTTP 429)..."
    """
    failures: dict[str, dict[str, Any]] = {}
    for entry in diagnostics:
        if not entry.get("ok"):
            failures[str(entry.get("provider", "unknown"))] = entry
    if not failures:
        return "No provider attempt was recorded."
    return "; ".join(
        f"{name}: {entry['category']} — {entry['detail']}"
        for name, entry in failures.items()
    )


def log_local_command(actor: Any, intent: str, confidence: float, result: dict[str, Any]) -> None:
    """Log a locally executed (no-API) chatbot command.

    Parameters:
        actor (User): The user who sent the message.
        intent (str): Matched local command name.
        confidence (float): Matcher confidence.
        result (dict): Command result (reply/actions) produced locally.
    """
    reply = str(result.get("reply", ""))[:200]
    assistant_logger.info(
        "local_command user=%s(%s) intent=%s confidence=%.2f reply=%r",
        getattr(actor, "name", None),
        getattr(actor, "id", None),
        intent,
        confidence,
        reply,
    )
