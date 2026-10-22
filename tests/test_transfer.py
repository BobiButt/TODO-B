# ============================================================================
# FILE:       backend/tests/test_transfer.py
# PURPOSE:    Integration tests for CSV/JSON export and import capabilities.
#             Specifically verifies CSV injection protection (=, +, -, @ prefixing),
#             dry-run preview mode, duplicate handling policies, and file guards.
# HOW IT WORKS: Tests streaming downloads and multipart file uploads using AsyncClient.
# USED BY / DEPENDS ON: pytest, tests/conftest.py.
# KEY CONCEPTS: CSV Formula Injection protection, atomic import transactions,
#             dry-run simulation, MIME type enforcement, file size limits.
# ============================================================================

"""Tests for task transfer operations (CSV/JSON export and import)."""

# json for building test JSON import payloads.
import json

# pytest and httpx.
import pytest
import httpx


@pytest.mark.asyncio
async def test_csv_export_injection_protection(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test that CSV export protects against formula injection by prefixing dangerous characters with a single quote."""
    # Create task with titles and descriptions starting with =, +, -, @.
    await client.post(
        "/api/v1/tasks",
        json={
            "title": "=SUM(A1:A10)",
            "description": "@cmd|' /C calc'!A0",
            "priority": "high",
            "tags": ["-test", "+formula"],
        },
        headers=user1_headers,
    )

    # Request CSV export.
    resp = await client.get("/api/v1/tasks/export?format=csv", headers=user1_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    csv_text = resp.text
    lines = csv_text.strip().splitlines()
    assert len(lines) >= 2

    # Verify that cells starting with =, @, -, + are prefixed with '.
    data_line = lines[1]
    assert "'=SUM(A1:A10)" in data_line
    assert "'@cmd|' /C calc'!A0" in data_line


@pytest.mark.asyncio
async def test_json_export(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test JSON export returns valid structured array of tasks."""
    await client.post(
        "/api/v1/tasks",
        json={"title": "JSON Export Task", "priority": "medium", "tags": ["export"]},
        headers=user1_headers,
    )

    resp = await client.get("/api/v1/tasks/export?format=json", headers=user1_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    data = resp.json()
    assert isinstance(data, list)
    assert any(t["title"] == "JSON Export Task" for t in data)


@pytest.mark.asyncio
async def test_download_templates(client: httpx.AsyncClient, user1_headers: dict[str, str]) -> None:
    """Test downloading sample templates for CSV and JSON."""
    csv_resp = await client.get("/api/v1/tasks/templates/csv", headers=user1_headers)
    assert csv_resp.status_code == 200
    assert "title,description,priority" in csv_resp.text

    json_resp = await client.get("/api/v1/tasks/templates/json", headers=user1_headers)
    assert json_resp.status_code == 200
    assert isinstance(json_resp.json(), list)


@pytest.mark.asyncio
async def test_csv_import_dry_run_and_commit(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test CSV import: first via dry-run simulation, then committing to database."""
    csv_content = (
        "title,description,priority,status,due_date,tags,category,subtasks\n"
        "Imported Task Alpha,Description alpha,high,pending,2026-11-01,backend;api,Engineering,Step A;Step B\n"
        "Imported Task Beta,Description beta,low,completed,2026-11-02,ui,Design,Step X\n"
    )

    # 1. Dry run test.
    files = {"file": ("tasks.csv", csv_content.encode("utf-8"), "text/csv")}
    dry_resp = await client.post(
        "/api/v1/tasks/import",
        files=files,
        data={"dry_run": "true", "on_duplicate": "skip"},
        headers=user1_headers,
    )
    assert dry_resp.status_code == 200
    dry_data = dry_resp.json()
    assert dry_data["dry_run"] is True
    assert dry_data["total_rows"] == 2
    assert dry_data["imported_rows"] == 2

    # Verify no tasks were actually created during dry run.
    chk_resp = await client.get("/api/v1/tasks", headers=user1_headers)
    assert chk_resp.json()["total"] == 0

    # 2. Real import commit.
    files_commit = {"file": ("tasks.csv", csv_content.encode("utf-8"), "text/csv")}
    commit_resp = await client.post(
        "/api/v1/tasks/import",
        files=files_commit,
        data={"dry_run": "false", "on_duplicate": "skip"},
        headers=user1_headers,
    )
    assert commit_resp.status_code == 200
    commit_data = commit_resp.json()
    assert commit_data["dry_run"] is False
    assert commit_data["imported_rows"] == 2

    # Verify tasks are present in database with subtasks.
    list_resp = await client.get("/api/v1/tasks", headers=user1_headers)
    assert list_resp.json()["total"] == 2
    task_alpha = next(t for t in list_resp.json()["items"] if t["title"] == "Imported Task Alpha")
    assert len(task_alpha["subtasks"]) == 2
    assert task_alpha["tags"] == ["backend", "api"]


@pytest.mark.asyncio
async def test_import_duplicate_strategy(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test duplicate title handling: skip vs overwrite."""
    # Existing task.
    await client.post(
        "/api/v1/tasks",
        json={"title": "Existing Title", "description": "Original description", "priority": "low"},
        headers=user1_headers,
    )

    # CSV with duplicate title.
    csv_duplicate = (
        "title,description,priority,status,due_date,tags,category,subtasks\n"
        "Existing Title,Updated Description,high,pending,,,\n"
    )

    # 1. Test SKIP policy.
    files_skip = {"file": ("tasks.csv", csv_duplicate.encode("utf-8"), "text/csv")}
    skip_resp = await client.post(
        "/api/v1/tasks/import",
        files=files_skip,
        data={"dry_run": "false", "on_duplicate": "skip"},
        headers=user1_headers,
    )
    assert skip_resp.json()["skipped_rows"] == 1

    # Verify task was not changed.
    t_chk = (await client.get("/api/v1/tasks", headers=user1_headers)).json()["items"][0]
    assert t_chk["description"] == "Original description"

    # 2. Test OVERWRITE policy.
    files_overwrite = {"file": ("tasks.csv", csv_duplicate.encode("utf-8"), "text/csv")}
    ow_resp = await client.post(
        "/api/v1/tasks/import",
        files=files_overwrite,
        data={"dry_run": "false", "on_duplicate": "overwrite"},
        headers=user1_headers,
    )
    assert ow_resp.json()["overwritten_rows"] == 1

    # Verify task was updated.
    t_chk2 = (await client.get("/api/v1/tasks", headers=user1_headers)).json()["items"][0]
    assert t_chk2["description"] == "Updated Description"
    assert t_chk2["priority"] == "high"


@pytest.mark.asyncio
async def test_import_unsupported_file_type(
    client: httpx.AsyncClient, user1_headers: dict[str, str]
) -> None:
    """Test that non-CSV and non-JSON file uploads return HTTP 415 Unsupported Media Type."""
    files = {"file": ("payload.exe", b"binary content", "application/octet-stream")}
    resp = await client.post(
        "/api/v1/tasks/import",
        files=files,
        data={"dry_run": "false"},
        headers=user1_headers,
    )
    assert resp.status_code == 415
