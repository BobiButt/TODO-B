"""User-scoped activity/log endpoints (list/delete) for the Logs page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.activity import ActivityLog
from app.models.user import User
from app.schemas.common import Page, Role

router = APIRouter(prefix="/logs", tags=["Logs"])


@router.get("", summary="List my logs (admins may see all)")
async def list_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    all_users: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[dict]:
    query = select(ActivityLog)
    if current_user.role != Role.ADMIN or not all_users:
        query = query.where(ActivityLog.user_id == current_user.id)
    if action:
        query = query.where(ActivityLog.action == action)
    if entity_type:
        query = query.where(ActivityLog.entity_type == entity_type)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = (await db.execute(
        query.order_by(ActivityLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "actor_name": r.actor_name,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "detail": r.detail or {},
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    pages = (total + page_size - 1) // page_size if total else 0
    return Page(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.delete("/{log_id}", summary="Delete one log entry")
async def delete_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = delete(ActivityLog).where(ActivityLog.id == log_id)
    if current_user.role != Role.ADMIN:
        stmt = stmt.where(ActivityLog.user_id == current_user.id)
    result = await db.execute(stmt)
    await db.commit()
    return {"deleted": bool(result.rowcount)}


@router.delete("", summary="Delete logs (current user scope)")
async def clear_logs(
    all_users: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = delete(ActivityLog)
    if current_user.role != Role.ADMIN or not all_users:
        stmt = stmt.where(ActivityLog.user_id == current_user.id)
    result = await db.execute(stmt)
    await db.commit()
    return {"deleted": int(result.rowcount or 0)}
