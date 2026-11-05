"""HTTP endpoint for text-to-image generation (isolated from assistant/chat)."""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/image", tags=["Image"])
logger = logging.getLogger("taskflow.image")

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/"
MAX_PROMPT_LENGTH = 1000
REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


@router.post("/generate", summary="Generate an image from a text prompt")
async def generate_image(payload: ImageGenerateRequest) -> StreamingResponse:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt cannot be empty.",
        )

    url = f"{POLLINATIONS_BASE}{quote(prompt, safe='')}"
    logger.info("image_generate prompt_len=%d", len(prompt))

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        logger.warning("image_generate timeout: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Image generation timed out. Please try again with a shorter prompt.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("image_generate http_error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the image generation service.",
        ) from exc

    if response.status_code >= 400:
        logger.warning("image_generate upstream_status=%d", response.status_code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The image generation service returned an error. Please try again.",
        )

    content_type = response.headers.get("content-type", "image/jpeg")
    if "image" not in content_type.lower():
        content_type = "image/jpeg"

    async def stream():
        yield response.content

    return StreamingResponse(
        stream(),
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
