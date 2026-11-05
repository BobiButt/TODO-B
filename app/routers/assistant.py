"""HTTP endpoint for the TaskFlow role-scoped AI assistant."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.chat import ChatMessage
from app.models.user import User
from app.services.activity import log_activity
from app.services.assistant import chat
from app.services.assistant_log import assistant_logger

router = APIRouter(prefix="/assistant", tags=["Assistant"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)


async def _persist_chat(db: AsyncSession, user_id: int, role: str, content: str, meta: dict | None = None) -> None:
    """Best-effort chat persistence; never break the main chat flow."""
    if not content:
        return
    try:
        db.add(ChatMessage(user_id=user_id, role=role, content=content[:8000], meta=meta or {}))
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        assistant_logger.exception("chat_persist_failed user=%s role=%s", user_id, role)


async def _log_chat_failure(db: AsyncSession, actor: User, detail: dict) -> None:
    try:
        await log_activity(db, actor, "failed", "chat", None, detail)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass


@router.get("/history", summary="Get persisted assistant chat history")
async def assistant_history(
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        rows = (await db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == current_user.id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )).scalars().all()
        return {
            "messages": [
                {"id": r.id, "role": r.role, "content": r.content, "created_at": r.created_at.isoformat(), "meta": r.meta or {}}
                for r in rows
            ]
        }
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        assistant_logger.exception("chat_history_failed user=%s", current_user.id)
        return {"messages": []}


@router.delete("/history", summary="Clear persisted assistant chat history")
async def assistant_clear_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    await db.execute(delete(ChatMessage).where(ChatMessage.user_id == current_user.id))
    await db.commit()
    return {"cleared": True}


@router.post("/chat", summary="Chat with the role-scoped TaskFlow assistant")
async def assistant_chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    history = [message.model_dump() for message in payload.messages]
    if history[-1]["role"] != "user":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The last chat message must be from the user.")
    user_text = history[-1].get("content", "")
    try:
        await _persist_chat(db, current_user.id, "user", user_text)
        result = await chat(db, current_user, history)
        reply = result.get("reply") if isinstance(result, dict) else None
        if isinstance(reply, str) and reply.strip():
            await _persist_chat(
                db,
                current_user.id,
                "assistant",
                reply,
                {"provider": result.get("provider"), "actions": result.get("actions", [])},
            )
        return result
    except HTTPException as exc:
        await _log_chat_failure(db, current_user, {"status": exc.status_code, "detail": str(exc.detail)[:500]})
        raise
    except Exception as exc:
        assistant_logger.exception(
            "chat_unhandled_error user=%s(%s)", current_user.name, current_user.id
        )
        await _log_chat_failure(db, current_user, {"status": 500, "detail": str(exc)[:500]})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Assistant failed to respond. Please try again.")