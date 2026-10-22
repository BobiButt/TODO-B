# ============================================================================
# FILE:       backend/tests/conftest.py
# PURPOSE:    Pytest configuration and test fixture definitions for the test suite.
#             Initializes the PostgreSQL test database (taskflow_test), manages
#             schema lifecycle, provides isolated database sessions per test,
#             and provides an async HTTP client (httpx.AsyncClient).
# HOW IT WORKS:
#   - Connects to TEST_DATABASE_URL (PostgreSQL) using NullPool to prevent event-loop
#     sharing across async test runs.
#   - Creates all tables from Base.metadata.
#   - Overrides FastAPI's get_db dependency with test database sessions.
#   - Provides pre-seeded admin and regular user headers for authentication.
# USED BY / DEPENDS ON: pytest, tests/test_*.py.
# KEY CONCEPTS: Fixture isolation, NullPool per-test connection, AsyncClient transport,
#             PostgreSQL test database with native array support.
# ============================================================================

"""Pytest fixtures and configuration for TaskFlow asynchronous test suite."""

# asyncio for managing async test execution and event loop fixtures.
import asyncio
from collections.abc import AsyncGenerator

# pytest and pytest_asyncio for test suite setup.
import pytest
import pytest_asyncio

# httpx for issuing asynchronous HTTP requests against the FastAPI app.
import httpx

# SQLAlchemy asynchronous engine and session maker with NullPool.
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy import text

# Application configuration, database dependency, and Base model metadata.
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.activity import ActivityLog
from app.models.comment import Comment
from app.models.subtask import Subtask
from app.models.task import Task
from app.models.user import User
from app.services.auth import create_access_token
from app.schemas.common import Priority, Role, Status

# Tests always use the separate database configured in backend/.env.
TEST_DB_URL = settings.test_database_url


@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a fresh AsyncEngine using NullPool for each test function."""
    engine = create_async_engine(
        TEST_DB_URL,
        echo=False,
        poolclass=NullPool,
    )
    # Ensure tables exist.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Clean up.
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE TABLE activity_log, comments, subtasks, tasks, users RESTART IDENTITY CASCADE;")
        )

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session for a test function.

    Parameters:
        test_engine (AsyncEngine): Fresh engine fixture for this test.

    Yields:
        AsyncSession: Active test database session.
    """
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an AsyncClient wired to the app with overridden database session.

    Parameters:
        db_session (AsyncSession): Test database session fixture.

    Yields:
        httpx.AsyncClient: Client for dispatching test HTTP requests.
    """
    # Dependency override function yielding the test session.
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    # Apply override to FastAPI dependency map.
    app.dependency_overrides[get_db] = override_get_db

    # Create ASGI client transport without binding to a physical network port.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Clean up dependency override.
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def seeded_users(db_session: AsyncSession) -> dict[str, User]:
    """Seed one admin user and two regular users for testing permissions.

    Parameters:
        db_session (AsyncSession): Test database session.

    Returns:
        dict[str, User]: Dictionary containing 'admin', 'user1', and 'user2'.
    """
    admin = User(name="Ada Admin", email="admin@taskflow.dev", role=Role.ADMIN.value)
    user1 = User(name="Alan User", email="alan@taskflow.dev", role=Role.USER.value)
    user2 = User(name="Grace User", email="grace@taskflow.dev", role=Role.USER.value)

    db_session.add_all([admin, user1, user2])
    await db_session.commit()
    await db_session.refresh(admin)
    await db_session.refresh(user1)
    await db_session.refresh(user2)

    return {"admin": admin, "user1": user1, "user2": user2}


@pytest.fixture
def admin_headers(seeded_users: dict[str, User]) -> dict[str, str]:
    """Headers for acting as the seeded administrator (JWT Bearer token)."""
    admin = seeded_users["admin"]
    token = create_access_token({"sub": str(admin.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user1_headers(seeded_users: dict[str, User]) -> dict[str, str]:
    """Headers for acting as regular user 1 (JWT Bearer token)."""
    user = seeded_users["user1"]
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user2_headers(seeded_users: dict[str, User]) -> dict[str, str]:
    """Headers for acting as regular user 2 (JWT Bearer token)."""
    user = seeded_users["user2"]
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}
