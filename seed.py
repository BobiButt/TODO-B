"""
Database seed script.

Behavior:
  - If an admin user already exists, exits immediately (does nothing).
  - Otherwise, creates ONLY the admin user.

Run from project root:
    python seed.py
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User
from app.schemas.common import AuthProvider, Role

# ---------------------------------------------------------------------------
# Admin credentials — change here if needed
# ---------------------------------------------------------------------------
ADMIN_NAME = "Mubeen Butt"
ADMIN_EMAIL = "mubeenbutt853@gmail.com"


async def seed_admin() -> None:
    """Create the admin user, unless one already exists."""
    async with AsyncSessionLocal() as db:
        # Check if ANY admin exists (not just this email).
        stmt = select(User).where(User.role == Role.ADMIN.value)
        result = await db.execute(stmt)
        existing_admin = result.scalars().first()

        if existing_admin:
            print(f"✅ Admin already exists ({existing_admin.email}) — seeder will not run.")
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
    await seed_admin()


if __name__ == "__main__":
    asyncio.run(main())