"""
Database seed script.

Behavior:
  - Clears all rows from every table (children first to respect FKs).
  - Creates ONLY the admin user.
  - Safe to run repeatedly (won't duplicate admin).

Run from project root:
    python seed.py
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models.activity import ActivityLog
from app.models.comment import Comment
from app.models.subtask import Subtask
from app.models.task import Task
from app.models.user import User
from app.schemas.common import AuthProvider, Role

# ---------------------------------------------------------------------------
# Admin credentials — change here if needed
# ---------------------------------------------------------------------------
ADMIN_NAME = "Mubeen Butt"
ADMIN_EMAIL = "mubeenbutt853@gmail.com"


async def reset_tables() -> None:
    """Delete every row from every table (dependency order)."""
    async with AsyncSessionLocal() as db:
        # Child tables first, then parents.
        await db.execute(delete(ActivityLog))
        await db.execute(delete(Comment))
        await db.execute(delete(Subtask))
        await db.execute(delete(Task))
        await db.execute(delete(User))
        await db.commit()
    print("🗑️  All tables cleared.")


async def seed_admin() -> None:
    """Insert the single admin account (idempotent)."""
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        if existing.scalars().first():
            print("✅ Admin already exists — skipping insert.")
            return

        admin = User(
            name=ADMIN_NAME,
            email=ADMIN_EMAIL,
            role=Role.ADMIN.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(admin)
        await db.commit()
        print(f"👤 Admin created: {ADMIN_EMAIL}")


async def main() -> None:
    await reset_tables()
    await seed_admin()
    print("🎉 Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())