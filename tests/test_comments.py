# ============================================================================
# FILE:       backend/tests/test_comments.py
# PURPOSE:    Integration tests for task comment threads and permission rules.
#             Verifies posting, chronological retrieval, author-only edits,
#             is_edited flag toggling, admin moderation deletion, and validation
#             rules (1-1000 characters, whitespace trimming).
# HOW IT WORKS: Exercises the comment endpoints with varying header identities
#             to enforce that users cannot modify or delete peers' comments.
# USED BY / DEPENDS ON: pytest, tests/conftest.py.
# KEY CONCEPTS: Discussion permissions, author immutability, admin moderation,
#             input validation boundaries.
# ============================================================================

"""Tests for task comment creation, permissions, and moderation."""

# pytest and httpx.
import pytest
import httpx


@pytest.mark.asyncio
async def test_create_and_list_comments(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test creating comments and retrieving them in chronological order."""
    # Create parent task.
    task_resp = await client.post("/api/v1/tasks", json={"title": "Task for Comments"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    # Post comment 1.
    c1_resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "First comment on task."},
        headers=user1_headers,
    )
    assert c1_resp.status_code == 201
    c1 = c1_resp.json()
    assert c1["body"] == "First comment on task."
    assert c1["is_edited"] is False

    # Post comment 2.
    c2_resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "Second comment on task."},
        headers=user1_headers,
    )
    assert c2_resp.status_code == 201

    # List comments.
    list_resp = await client.get(f"/api/v1/tasks/{task_id}/comments", headers=user1_headers)
    assert list_resp.status_code == 200
    comments = list_resp.json()
    assert len(comments) == 2
    assert comments[0]["body"] == "First comment on task."
    assert comments[1]["body"] == "Second comment on task."


@pytest.mark.asyncio
async def test_author_can_edit_comment_sets_is_edited(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test author editing their comment sets is_edited=True."""
    task_resp = await client.post("/api/v1/tasks", json={"title": "Comment Edit Task"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    c_resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "Original text"},
        headers=user1_headers,
    )
    comment_id = c_resp.json()["id"]

    # Edit comment.
    edit_resp = await client.patch(
        f"/api/v1/tasks/{task_id}/comments/{comment_id}",
        json={"body": "Revised and corrected text"},
        headers=user1_headers,
    )
    assert edit_resp.status_code == 200
    edited = edit_resp.json()
    assert edited["body"] == "Revised and corrected text"
    assert edited["is_edited"] is True


@pytest.mark.asyncio
async def test_non_author_cannot_edit_comment(
    client: httpx.AsyncClient, user1_headers: dict[str, str], user2_headers: dict[str, str]
) -> None:
    """Test that a user cannot edit a comment written by another user (HTTP 403)."""
    task_resp = await client.post("/api/v1/tasks", json={"title": "Shared Task"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    c_resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "Author User 1 comment"},
        headers=user1_headers,
    )
    comment_id = c_resp.json()["id"]

    # User 2 attempts to edit User 1's comment.
    edit_resp = await client.patch(
        f"/api/v1/tasks/{task_id}/comments/{comment_id}",
        json={"body": "Malicious modification"},
        headers=user2_headers,
    )
    assert edit_resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_delete_any_comment_for_moderation(
    client: httpx.AsyncClient, user1_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    """Test that an administrator can delete any comment for moderation."""
    task_resp = await client.post("/api/v1/tasks", json={"title": "Moderation Task"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    c_resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "Inappropriate comment requiring moderation"},
        headers=user1_headers,
    )
    comment_id = c_resp.json()["id"]

    # Admin deletes the comment.
    del_resp = await client.delete(f"/api/v1/tasks/{task_id}/comments/{comment_id}", headers=admin_headers)
    assert del_resp.status_code == 204

    # Verify comment list is empty.
    list_resp = await client.get(f"/api/v1/tasks/{task_id}/comments", headers=user1_headers)
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_empty_or_whitespace_comment_rejected(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test that empty or purely whitespace comments are rejected with HTTP 422."""
    task_resp = await client.post("/api/v1/tasks", json={"title": "Validation Task"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    # 1. Pure whitespace.
    resp_ws = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": "     "},
        headers=user1_headers,
    )
    assert resp_ws.status_code == 422

    # 2. Empty string.
    resp_empty = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": ""},
        headers=user1_headers,
    )
    assert resp_empty.status_code == 422


@pytest.mark.asyncio
async def test_oversized_comment_rejected(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test that comments exceeding 1000 characters are rejected with HTTP 422."""
    task_resp = await client.post("/api/v1/tasks", json={"title": "Length Task"}, headers=user1_headers)
    task_id = task_resp.json()["id"]

    long_body = "x" * 1001
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/comments",
        json={"body": long_body},
        headers=user1_headers,
    )
    assert resp.status_code == 422
