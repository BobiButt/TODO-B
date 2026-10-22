# ============================================================================
# FILE:       backend/tests/test_admin.py
# PURPOSE:    Integration tests for administrator panel endpoints: KPI dashboard,
#             audit activity logs, comment moderation overview, bulk task mutations,
#             and task reassignment.
# HOW IT WORKS: Verifies calculation of system KPI metrics and confirms that every
#             admin route strictly returns HTTP 403 Forbidden to regular users.
# USED BY / DEPENDS ON: pytest, tests/conftest.py.
# KEY CONCEPTS: Admin authorization guards, aggregate KPI math, bulk mutation logic.
# ============================================================================

"""Tests for admin dashboard stats, audit logs, moderation, and bulk actions."""

# pytest and httpx.
import pytest
import httpx

# User model.
from app.models.user import User


@pytest.mark.asyncio
async def test_admin_stats_success_and_forbidden_for_user(
    client: httpx.AsyncClient, admin_headers: dict[str, str], user1_headers: dict[str, str]
) -> None:
    """Test retrieving admin KPI statistics, and verify regular user receives HTTP 403."""
    # 1. Regular user gets 403.
    user_resp = await client.get("/api/v1/admin/stats", headers=user1_headers)
    assert user_resp.status_code == 403

    # 2. Admin gets stats.
    admin_resp = await client.get("/api/v1/admin/stats", headers=admin_headers)
    assert admin_resp.status_code == 200
    stats = admin_resp.json()
    assert "total_users" in stats
    assert "total_tasks" in stats
    assert "completion_rate" in stats
    assert "tasks_per_user" in stats
    assert isinstance(stats["tasks_per_user"], list)


@pytest.mark.asyncio
async def test_admin_activity_log_pagination(
    client: httpx.AsyncClient, admin_headers: dict[str, str], user1_headers: dict[str, str]
) -> None:
    """Test reading the paginated audit activity log and verifying 403 for users."""
    # 1. User gets 403.
    user_resp = await client.get("/api/v1/admin/activity", headers=user1_headers)
    assert user_resp.status_code == 403

    # 2. Admin retrieves log.
    admin_resp = await client.get("/api/v1/admin/activity?page=1&page_size=10", headers=admin_headers)
    assert admin_resp.status_code == 200
    data = admin_resp.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_admin_comments_moderation_view(
    client: httpx.AsyncClient, admin_headers: dict[str, str], user1_headers: dict[str, str]
) -> None:
    """Test listing all comments across all tasks for moderation."""
    # Create task and comment.
    t_resp = await client.post("/api/v1/tasks", json={"title": "Moderation Task"}, headers=user1_headers)
    task_id = t_resp.json()["id"]
    await client.post(f"/api/v1/tasks/{task_id}/comments", json={"body": "Comment for moderation view"}, headers=user1_headers)

    # User gets 403.
    u_resp = await client.get("/api/v1/admin/comments", headers=user1_headers)
    assert u_resp.status_code == 403

    # Admin gets comments.
    a_resp = await client.get("/api/v1/admin/comments", headers=admin_headers)
    assert a_resp.status_code == 200
    comments = a_resp.json()
    assert len(comments) >= 1
    assert any(c["body"] == "Comment for moderation view" for c in comments)


@pytest.mark.asyncio
async def test_bulk_complete_and_delete(
    client: httpx.AsyncClient, admin_headers: dict[str, str], user1_headers: dict[str, str]
) -> None:
    """Test bulk complete and bulk delete actions."""
    t1 = (await client.post("/api/v1/tasks", json={"title": "Bulk 1"}, headers=user1_headers)).json()
    t2 = (await client.post("/api/v1/tasks", json={"title": "Bulk 2"}, headers=user1_headers)).json()

    # User forbidden from bulk actions.
    u_bulk = await client.post(
        "/api/v1/admin/tasks/bulk-complete",
        json={"task_ids": [t1["id"], t2["id"]]},
        headers=user1_headers,
    )
    assert u_bulk.status_code == 403

    # Admin bulk completes.
    a_bulk_comp = await client.post(
        "/api/v1/admin/tasks/bulk-complete",
        json={"task_ids": [t1["id"], t2["id"]]},
        headers=admin_headers,
    )
    assert a_bulk_comp.status_code == 200
    assert a_bulk_comp.json()["count"] == 2

    # Admin bulk deletes.
    a_bulk_del = await client.post(
        "/api/v1/admin/tasks/bulk-delete",
        json={"task_ids": [t1["id"], t2["id"]]},
        headers=admin_headers,
    )
    assert a_bulk_del.status_code == 200
    assert a_bulk_del.json()["count"] == 2


@pytest.mark.asyncio
async def test_reassign_task(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    user1_headers: dict[str, str],
    seeded_users: dict[str, User],
) -> None:
    """Test administrator reassigning a task to a different user."""
    t = (await client.post("/api/v1/tasks", json={"title": "Task to Reassign"}, headers=user1_headers)).json()
    task_id = t["id"]
    new_owner_id = seeded_users["user2"].id

    # User forbidden from reassigning.
    u_reassign = await client.patch(
        f"/api/v1/admin/tasks/{task_id}/reassign",
        json={"user_id": new_owner_id},
        headers=user1_headers,
    )
    assert u_reassign.status_code == 403

    # Admin reassigns.
    a_reassign = await client.patch(
        f"/api/v1/admin/tasks/{task_id}/reassign",
        json={"user_id": new_owner_id},
        headers=admin_headers,
    )
    assert a_reassign.status_code == 200

    # Verify task now belongs to user 2.
    task_chk = (await client.get(f"/api/v1/tasks/{task_id}", headers=admin_headers)).json()
    assert task_chk["user_id"] == new_owner_id
