"""HTTP endpoint for the TaskFlow role-scoped AI assistant."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.assistant import chat
from app.services.assistant_log import assistant_logger

router = APIRouter(prefix="/assistant", tags=["Assistant"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)


@router.post("/chat", summary="Chat with the role-scoped TaskFlow assistant")
async def assistant_chat(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    history = [message.model_dump() for message in payload.messages]
    if history[-1]["role"] != "user":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The last chat message must be from the user.")
    try:
        return await chat(db, current_user, history)
    except HTTPException:
        # Expected assistant errors (502 provider failures, 4xx input, ...)
        # are already classified and logged inside services/assistant.py.
        raise
    except Exception:
        # Unexpected bug (e.g. a bad query) — log the full traceback to the
        # chatbot log so it can be diagnosed from backend/logs/assistant.log.
        assistant_logger.exception(
            "chat_unhandled_error user=%s(%s)", current_user.name, current_user.id
        )
        raise