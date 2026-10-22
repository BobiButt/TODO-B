# ============================================================================
# FILE:       backend/tests/test_users.py
# PURPOSE:    Integration tests for user CRUD endpoints and role-enforcement.
#             Verifies listing, reading, creating, updating, self-protection,
#             cascade deletion, and role elevation rejection.
# HOW IT WORKS: Issues HTTP requests using httpx.AsyncClient with X-User-Id and
#             X-Role headers, testing success paths and 401/403/409 error cases.
# USED BY / DEPENDS ON: pytest, tests/conftest.py.
# KEY CONCEPTS: RBAC test cases, status code validation, duplicate email handling.
# ============================================================================

"""Tests for user management endpoints and permission guards."""

# pytest and httpx imports.
import pytest
import httpx

# Models and schemas.
from app.models.user import User


@pytest.mark.asyncio
async def test_regular_user_lists_only_self(client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]) -> None:
    """Test that regular users cannot enumerate other user identities."""
    # Send GET /api/v1/users request.
    response = await client.get("/api/v1/users", headers=user1_headers)
    # Verify status code 200 OK.
    assert response.status_code == 200
    # Verify response body is a list containing only the requesting user.
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == seeded_users["user1"].id


@pytest.mark.asyncio
async def test_admin_can_list_users(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await client.get("/api/v1/users", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) >= 3


@pytest.mark.asyncio
async def test_get_user_by_id(client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]) -> None:
    """Test retrieving a single user by primary key ID."""
    target_id = seeded_users["user1"].id
    response = await client.get(f"/api/v1/users/{target_id}", headers=user1_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == target_id
    assert data["email"] == "alan@taskflow.dev"


@pytest.mark.asyncio
async def test_regular_user_cannot_read_another_profile(
    client: httpx.AsyncClient,
    user1_headers: dict[str, str],
    seeded_users: dict[str, User],
) -> None:
    response = await client.get(f"/api/v1/users/{seeded_users['user2'].id}", headers=user1_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_user_admin_success(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    """Test administrator successfully creating a new user account."""
    payload = {
        "name": "Katherine Johnson",
        "email": "katherine@taskflow.dev",
        "role": "user",
    }
    response = await client.post("/api/v1/users", json=payload, headers=admin_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Katherine Johnson"
    assert data["email"] == "katherine@taskflow.dev"
    assert data["role"] == "user"


@pytest.mark.asyncio
async def test_create_user_forbidden_for_regular_user(client: httpx.AsyncClient, user1_headers: dict[str, str]) -> None:
    """Test non-administrators are forbidden from creating new users (HTTP 403)."""
    payload = {
        "name": "Imposter User",
        "email": "imposter@taskflow.dev",
        "role": "user",
    }
    response = await client.post("/api/v1/users", json=payload, headers=user1_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_user_duplicate_email_conflict(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    """Test that creating a user with an already-registered email returns HTTP 409 Conflict."""
    payload = {
        "name": "Duplicate Alan",
        "email": "alan@taskflow.dev",  # already exists in seeded_users
        "role": "user",
    }
    response = await client.post("/api/v1/users", json=payload, headers=admin_headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_own_profile(client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]) -> None:
    """Test a user updating their own profile details."""
    target_id = seeded_users["user1"].id
    payload = {"name": "Alan Mathison Turing"}
    response = await client.patch(f"/api/v1/users/{target_id}", json=payload, headers=user1_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Alan Mathison Turing"


@pytest.mark.asyncio
async def test_update_other_user_forbidden_for_regular_user(
    client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]
) -> None:
    """Test that regular users cannot modify another user's profile (HTTP 403)."""
    target_id = seeded_users["user2"].id
    payload = {"name": "Hacked Name"}
    response = await client.patch(f"/api/v1/users/{target_id}", json=payload, headers=user1_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_regular_user_cannot_elevate_role(
    client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]
) -> None:
    """Test that regular users cannot elevate their role to admin (HTTP 403)."""
    target_id = seeded_users["user1"].id
    payload = {"role": "admin"}
    response = await client.patch(f"/api/v1/users/{target_id}", json=payload, headers=user1_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_demote_last_admin(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    seeded_users: dict[str, User],
) -> None:
    response = await client.patch(
        f"/api/v1/users/{seeded_users['admin'].id}",
        json={"role": "user"},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Cannot demote the last administrator."


@pytest.mark.asyncio
async def test_delete_user_admin_success(
    client: httpx.AsyncClient, admin_headers: dict[str, str], seeded_users: dict[str, User]
) -> None:
    """Test administrator deleting a user account."""
    target_id = seeded_users["user2"].id
    response = await client.delete(f"/api/v1/users/{target_id}", headers=admin_headers)
    assert response.status_code == 204

    # Verify user no longer exists.
    chk = await client.get(f"/api/v1/users/{target_id}", headers=admin_headers)
    assert chk.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_delete_self(
    client: httpx.AsyncClient, admin_headers: dict[str, str], seeded_users: dict[str, User]
) -> None:
    """Test administrator attempting to delete their own account receives HTTP 400."""
    admin_id = seeded_users["admin"].id
    response = await client.delete(f"/api/v1/users/{admin_id}", headers=admin_headers)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_delete_user_forbidden_for_regular_user(
    client: httpx.AsyncClient, user1_headers: dict[str, str], seeded_users: dict[str, User]
) -> None:
    """Test regular users cannot delete users (HTTP 403)."""
    target_id = seeded_users["user2"].id
    response = await client.delete(f"/api/v1/users/{target_id}", headers=user1_headers)
    assert response.status_code == 403
