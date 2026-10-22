"""Tests for provider fallback and role-scoped assistant operations."""

from datetime import date, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.models.task import Task
from app.services import assistant as assistant_service


@pytest.mark.asyncio
async def test_chat_without_a_provider_key_offers_local_commands(client, seeded_users, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")

    # Not a built-in command, so the friendly no-key guidance is returned.
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=admin_headers,
        json={"messages": [{"role": "user", "content": "Tell me a joke about cats"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "local"
    assert "backend/.env" in body["reply"]
    assert "list tasks" in body["reply"]


@pytest.mark.asyncio
async def test_provider_falls_back_from_openrouter_to_gemini(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-test")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "groq_api_key", "")
    requested_urls: list[str] = []

    async def fake_provider_request(client, url, api_key, model, messages, tools):
        requested_urls.append(url)
        if "openrouter" in url:
            raise httpx.ConnectError("provider unavailable")
        return {"role": "assistant", "content": "Ready to help."}

    monkeypatch.setattr(assistant_service, "_provider_request", fake_provider_request)
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=admin_headers,
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "gemini"
    assert response.json()["reply"] == "Ready to help."
    assert len(requested_urls) == 2


@pytest.mark.asyncio
async def test_provider_falls_back_after_tool_call(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-test")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "groq_api_key", "")
    requested_urls: list[str] = []

    async def fake_provider_request(client, url, api_key, model, messages, tools):
        requested_urls.append(url)
        if len(requested_urls) == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-list",
                    "type": "function",
                    "function": {"name": "list_tasks", "arguments": "{}"},
                }],
            }
        if "openrouter" in url:
            raise httpx.ConnectError("provider failed after tool call")
        return {"role": "assistant", "content": "I found your tasks."}

    async def fake_call_tool(name, arguments, db, actor):
        return {"tasks": []}

    monkeypatch.setattr(assistant_service, "_provider_request", fake_provider_request)
    monkeypatch.setattr(assistant_service, "_call_tool", fake_call_tool)
    # "Hello" (not "List tasks") so the built-in local command does not
    # intercept the request before the AI provider path is reached.
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=admin_headers,
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "gemini"
    assert response.json()["reply"] == "I found your tasks."
    assert len(requested_urls) == 3


@pytest.mark.asyncio
async def test_regular_user_can_only_list_own_tasks(db_session, seeded_users):
    user_task = Task(user_id=seeded_users["user1"].id, title="Mine", priority="low", status="pending", tags=[], position=1)
    other_task = Task(user_id=seeded_users["user2"].id, title="Not mine", priority="low", status="pending", tags=[], position=1)
    db_session.add_all([user_task, other_task])
    await db_session.commit()

    result = await assistant_service._call_tool("list_tasks", {}, db_session, seeded_users["user1"])

    assert [task["title"] for task in result["tasks"]] == ["Mine"]
    user_tools = {tool["function"]["name"] for tool in assistant_service._tool_definitions(False)}
    assert "get_admin_summary" not in user_tools
    assert "list_users" not in user_tools
    assert "reassign_task" not in user_tools


@pytest.mark.asyncio
async def test_regular_user_cannot_invoke_admin_tool(db_session, seeded_users):
    with pytest.raises(HTTPException) as error:
        await assistant_service._call_tool("get_admin_summary", {}, db_session, seeded_users["user1"])

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_local_create_task_command_works_without_provider(client, user1_headers, db_session):
    """Regression: the old nested select() in the create-task local command
    crashed chat with a 500 sqlalchemy.ArgumentError."""
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=user1_headers,
        json={"messages": [{"role": "user", "content": "add task Buy milk for the week"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "local"
    assert "Buy milk for the week" in body["reply"]
    task = (
        await db_session.execute(select(Task).where(Task.title == "Buy milk for the week"))
    ).scalars().first()
    assert task is not None
    assert task.status == "pending"


@pytest.mark.asyncio
async def test_local_create_task_with_due_tomorrow(client, user1_headers, db_session):
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=user1_headers,
        json={"messages": [{"role": "user", "content": "add task Pay rent due tomorrow"}]},
    )

    assert response.status_code == 200
    task = (await db_session.execute(select(Task).where(Task.title == "Pay rent"))).scalars().first()
    assert task is not None
    assert task.due_date == date.today() + timedelta(days=1)


@pytest.mark.asyncio
async def test_local_stats_and_overdue_commands(client, user1_headers, db_session, seeded_users):
    db_session.add_all([
        Task(
            user_id=seeded_users["user1"].id, title="Late task", priority="high",
            status="pending", tags=[], position=1, due_date=date.today() - timedelta(days=2),
        ),
        Task(
            user_id=seeded_users["user1"].id, title="Fresh task", priority="low",
            status="pending", tags=[], position=2,
        ),
    ])
    await db_session.commit()

    stats_response = await client.post(
        "/api/v1/assistant/chat",
        headers=user1_headers,
        json={"messages": [{"role": "user", "content": "how many tasks do i have"}]},
    )
    assert stats_response.status_code == 200
    assert "Total: 2" in stats_response.json()["reply"]
    assert "Overdue: 1" in stats_response.json()["reply"]

    overdue_response = await client.post(
        "/api/v1/assistant/chat",
        headers=user1_headers,
        json={"messages": [{"role": "user", "content": "overdue tasks"}]},
    )
    assert overdue_response.status_code == 200
    assert "Late task" in overdue_response.json()["reply"]


@pytest.mark.asyncio
async def test_provider_rate_limit_is_reported_in_diagnostics(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-test")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "groq_api_key", "")

    async def fake_provider_request(client, url, api_key, model, messages, tools):
        if "openrouter" in url:
            raise assistant_service.ProviderError(
                "rate_limited", "Rate limit reached (HTTP 429).", http_status=429
            )
        return {"role": "assistant", "content": "All good."}

    monkeypatch.setattr(assistant_service, "_provider_request", fake_provider_request)
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=admin_headers,
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "gemini"
    failed = [entry for entry in body["diagnostics"] if not entry["ok"]]
    assert failed[0]["provider"] == "openrouter"
    assert failed[0]["category"] == "rate_limited"
    assert failed[0]["http_status"] == 429
    succeeded = [entry for entry in body["diagnostics"] if entry["ok"]]
    assert succeeded[0]["provider"] == "gemini"


@pytest.mark.asyncio
async def test_all_providers_failing_returns_502_with_reasons(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-test")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "groq_api_key", "")

    async def fake_provider_request(client, url, api_key, model, messages, tools):
        if "openrouter" in url:
            raise assistant_service.ProviderError(
                "auth_error",
                "Authentication failed (HTTP 401) — check the API key in backend/.env.",
                http_status=401,
            )
        raise assistant_service.ProviderError(
            "rate_limited", "Rate limit reached (HTTP 429).", http_status=429
        )

    monkeypatch.setattr(assistant_service, "_provider_request", fake_provider_request)
    response = await client.post(
        "/api/v1/assistant/chat",
        headers=admin_headers,
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "openrouter" in detail and "auth_error" in detail
    assert "gemini" in detail and "rate_limited" in detail
    assert "assistant.log" in detail


def test_classify_http_error_categories():
    from app.services.assistant_log import classify_http_error

    assert classify_http_error(429, "")[0] == "rate_limited"
    assert classify_http_error(403, "RESOURCE_EXHAUSTED: quota exceeded")[0] == "quota_exceeded"
    assert classify_http_error(401, "")[0] == "auth_error"
    assert classify_http_error(400, "model not found")[0] == "model_not_found"
    assert classify_http_error(503, "")[0] == "server_error"
    assert classify_http_error(400, "bad request shape")[0] == "invalid_request"
