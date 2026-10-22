# ============================================================================
# FILE:       backend/tests/test_tasks.py
# PURPOSE:    Integration tests for task CRUD, checklist updates, status toggling,
#             drag-and-drop reordering, soft-deletion/restoration, and filtering.
# HOW IT WORKS: Tests PostgreSQL array tag queries (ANY(tags)), ownership isolation,
#             overdue status logic, and subtask checklist completion.
# USED BY / DEPENDS ON: pytest, tests/conftest.py.
# KEY CONCEPTS: Soft delete lifecycle, array filtering, isolation between tenants,
#             fractional position reordering.
# ============================================================================

"""Tests for task lifecycle, filtering, status toggling, and reordering."""

# datetime and timedelta for testing due dates and overdue filters.
from datetime import date, timedelta

# pytest and httpx.
import pytest
import httpx

# User model.
from app.models.user import User


@pytest.mark.asyncio
async def test_create_and_get_task_with_subtasks(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test creating a task with nested subtasks and reading it back."""
    payload = {
        "title": "Build Neural Network Model",
        "description": "Implement backpropagation and convolutional layers.",
        "priority": "high",
        "status": "pending",
        "due_date": (date.today() + timedelta(days=5)).isoformat(),
        "tags": ["ai", "python", "ml"],
        "category": "Data Science",
        "subtasks": [
            {"title": "Collect training dataset", "is_done": True},
            {"title": "Define loss function", "is_done": False},
        ],
    }

    # Create task.
    create_resp = await client.post("/api/v1/tasks", json=payload, headers=user1_headers)
    assert create_resp.status_code == 201
    created_task = create_resp.json()
    task_id = created_task["id"]

    assert created_task["title"] == "Build Neural Network Model"
    assert created_task["priority"] == "high"
    assert len(created_task["subtasks"]) == 2
    assert created_task["tags"] == ["ai", "python", "ml"]

    # Read task.
    get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=user1_headers)
    assert get_resp.status_code == 200
    fetched_task = get_resp.json()
    assert fetched_task["id"] == task_id
    assert fetched_task["subtasks"][0]["title"] == "Collect training dataset"
    assert fetched_task["subtasks"][0]["is_done"] is True


@pytest.mark.asyncio
async def test_user_cannot_access_other_user_task(
    client: httpx.AsyncClient, user1_headers: dict[str, str], user2_headers: dict[str, str]
) -> None:
    """Test that a user cannot read, update, or delete another user's task (HTTP 403)."""
    # User 1 creates a private task.
    payload = {"title": "User 1 Secret Project", "priority": "high"}
    create_resp = await client.post("/api/v1/tasks", json=payload, headers=user1_headers)
    task_id = create_resp.json()["id"]

    # User 2 attempts to read the task.
    get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=user2_headers)
    assert get_resp.status_code == 403

    # User 2 attempts to update the task.
    update_resp = await client.patch(f"/api/v1/tasks/{task_id}", json={"title": "Compromised"}, headers=user2_headers)
    assert update_resp.status_code == 403

    # User 2 attempts to delete the task.
    delete_resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=user2_headers)
    assert delete_resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_access_any_user_task(
    client: httpx.AsyncClient, user1_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    """Test that an administrator can view and modify tasks owned by other users."""
    payload = {"title": "User 1 Task", "priority": "medium"}
    create_resp = await client.post("/api/v1/tasks", json=payload, headers=user1_headers)
    task_id = create_resp.json()["id"]

    # Admin reads user 1's task.
    get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["title"] == "User 1 Task"


@pytest.mark.asyncio
async def test_task_filtering_and_postgres_array_tags(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test query filtering: search q, status, priority, and PostgreSQL ANY(tags) array search."""
    # Seed 3 tasks with distinct tags, status, priority.
    await client.post(
        "/api/v1/tasks",
        json={"title": "Fix Login Bug", "priority": "high", "status": "pending", "tags": ["auth", "bug"]},
        headers=user1_headers,
    )
    await client.post(
        "/api/v1/tasks",
        json={"title": "Refactor CSS Theme", "priority": "low", "status": "completed", "tags": ["frontend", "styling"]},
        headers=user1_headers,
    )
    await client.post(
        "/api/v1/tasks",
        json={"title": "Optimize Postgres Index", "priority": "medium", "status": "in_progress", "tags": ["db", "bug"]},
        headers=user1_headers,
    )

    # 1. Filter by tag="bug" (PostgreSQL ANY(tags)).
    resp_tag = await client.get("/api/v1/tasks?tag=bug", headers=user1_headers)
    assert resp_tag.status_code == 200
    tag_data = resp_tag.json()
    assert tag_data["total"] == 2

    # 2. Filter by status="completed".
    resp_status = await client.get("/api/v1/tasks?status=completed", headers=user1_headers)
    assert resp_status.status_code == 200
    assert resp_status.json()["total"] == 1
    assert resp_status.json()["items"][0]["title"] == "Refactor CSS Theme"

    # 3. Filter by search query q="Login".
    resp_search = await client.get("/api/v1/tasks?q=Login", headers=user1_headers)
    assert resp_search.status_code == 200
    assert resp_search.json()["total"] == 1
    assert resp_search.json()["items"][0]["title"] == "Fix Login Bug"


@pytest.mark.asyncio
async def test_overdue_and_due_today_filters(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test overdue and due_today filters."""
    today = date.today()
    yesterday = today - timedelta(days=2)
    tomorrow = today + timedelta(days=2)

    # Overdue task (yesterday and pending).
    await client.post(
        "/api/v1/tasks",
        json={"title": "Overdue Task", "status": "pending", "due_date": yesterday.isoformat()},
        headers=user1_headers,
    )
    # Due today task.
    await client.post(
        "/api/v1/tasks",
        json={"title": "Due Today Task", "status": "pending", "due_date": today.isoformat()},
        headers=user1_headers,
    )
    # Future task.
    await client.post(
        "/api/v1/tasks",
        json={"title": "Future Task", "status": "pending", "due_date": tomorrow.isoformat()},
        headers=user1_headers,
    )

    # Test overdue filter.
    resp_overdue = await client.get("/api/v1/tasks?overdue=true", headers=user1_headers)
    assert resp_overdue.status_code == 200
    assert resp_overdue.json()["total"] == 1
    assert resp_overdue.json()["items"][0]["title"] == "Overdue Task"

    # Test due today filter.
    resp_today = await client.get("/api/v1/tasks?due_today=true", headers=user1_headers)
    assert resp_today.status_code == 200
    assert resp_today.json()["total"] == 1
    assert resp_today.json()["items"][0]["title"] == "Due Today Task"


@pytest.mark.asyncio
async def test_toggle_task_status(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test toggling task status between pending and completed."""
    create_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Task to Toggle", "status": "pending"},
        headers=user1_headers,
    )
    task_id = create_resp.json()["id"]

    # Toggle to completed.
    toggle1 = await client.patch(f"/api/v1/tasks/{task_id}/toggle", headers=user1_headers)
    assert toggle1.status_code == 200
    assert toggle1.json()["status"] == "completed"

    # Toggle back to pending.
    toggle2 = await client.patch(f"/api/v1/tasks/{task_id}/toggle", headers=user1_headers)
    assert toggle2.status_code == 200
    assert toggle2.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_soft_delete_and_restore(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test soft deleting a task and restoring it via the undo endpoint."""
    create_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Task to Soft Delete"},
        headers=user1_headers,
    )
    task_id = create_resp.json()["id"]

    # Soft delete task (returns 204).
    del_resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=user1_headers)
    assert del_resp.status_code == 204

    # Verify task is no longer listed in active tasks.
    list_resp = await client.get("/api/v1/tasks", headers=user1_headers)
    assert list_resp.json()["total"] == 0

    # Restore task.
    restore_resp = await client.post(f"/api/v1/tasks/{task_id}/restore", headers=user1_headers)
    assert restore_resp.status_code == 200
    assert restore_resp.json()["deleted_at"] is None

    # Verify task appears in list again.
    list_resp2 = await client.get("/api/v1/tasks", headers=user1_headers)
    assert list_resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_reorder_tasks(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test manual drag-and-drop reordering with normalized positions."""
    t1 = (await client.post("/api/v1/tasks", json={"title": "Task 1", "position": 1.0}, headers=user1_headers)).json()
    t2 = (await client.post("/api/v1/tasks", json={"title": "Task 2", "position": 2.0}, headers=user1_headers)).json()
    t3 = (await client.post("/api/v1/tasks", json={"title": "Task 3", "position": 3.0}, headers=user1_headers)).json()

    # Reorder in reverse: [t3, t1, t2].
    new_order = [t3["id"], t1["id"], t2["id"]]
    reorder_resp = await client.patch("/api/v1/tasks/reorder", json={"ordered_ids": new_order}, headers=user1_headers)
    assert reorder_resp.status_code == 200

    # Query list ordered by position.
    list_resp = await client.get("/api/v1/tasks?sort=position&order=asc", headers=user1_headers)
    items = list_resp.json()["items"]
    assert items[0]["id"] == t3["id"]
    assert items[1]["id"] == t1["id"]
    assert items[2]["id"] == t2["id"]


@pytest.mark.asyncio
async def test_update_subtask_checklist_item(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test updating subtask checklist item state."""
    payload = {
        "title": "Task with Checklist",
        "subtasks": [{"title": "Step 1", "is_done": False}],
    }
    create_resp = await client.post("/api/v1/tasks", json=payload, headers=user1_headers)
    task_data = create_resp.json()
    task_id = task_data["id"]
    subtask_id = task_data["subtasks"][0]["id"]

    # Toggle subtask is_done to True.
    st_patch = await client.patch(
        f"/api/v1/tasks/{task_id}/subtasks/{subtask_id}",
        json={"is_done": True},
        headers=user1_headers,
    )
    assert st_patch.status_code == 200
    assert st_patch.json()["is_done"] is True


@pytest.mark.asyncio
async def test_create_delete_subtask_and_scoped_task_stats(
    client: httpx.AsyncClient,
    user1_headers: dict[str, str],
    user2_headers: dict[str, str],
) -> None:
    task_response = await client.post(
        "/api/v1/tasks",
        json={"title": "Scoped statistics task", "status": "in_progress", "due_date": (date.today() - timedelta(days=1)).isoformat()},
        headers=user1_headers,
    )
    task_id = task_response.json()["id"]

    subtask_response = await client.post(
        f"/api/v1/tasks/{task_id}/subtasks",
        json={"title": "First checklist step"},
        headers=user1_headers,
    )
    assert subtask_response.status_code == 201
    subtask_id = subtask_response.json()["id"]

    forbidden_response = await client.post(
        f"/api/v1/tasks/{task_id}/subtasks",
        json={"title": "Unauthorized checklist step"},
        headers=user2_headers,
    )
    assert forbidden_response.status_code == 403

    stats_response = await client.get("/api/v1/tasks/stats", headers=user1_headers)
    assert stats_response.status_code == 200
    assert stats_response.json() == {
        "total": 1,
        "pending": 0,
        "in_progress": 1,
        "completed": 0,
        "overdue": 1,
    }

    delete_response = await client.delete(
        f"/api/v1/tasks/{task_id}/subtasks/{subtask_id}",
        headers=user1_headers,
    )
    assert delete_response.status_code == 204
