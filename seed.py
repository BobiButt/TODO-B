# ============================================================================
# FILE:       backend/seed.py
# PURPOSE:    Populate database with initial sample data: 1 administrator,
#             4 regular users, realistic tasks across categories, checklist
#             subtasks, comment threads, and initial audit activity records.
# HOW IT WORKS: Connects via AsyncSessionLocal from app.database. Checks if users
#             exist; if empty, inserts sample rows inside an atomic transaction.
#             Safe to run multiple times (idempotent: exits if data exists).
# USED BY / DEPENDS ON: setup workflow, local dev (`python seed.py`), test suite.
# KEY CONCEPTS: Database seeding, realistic mock data, relational tree insertion.
# ============================================================================

"""Database seeding script for local development and demonstration."""

# asyncio for executing asynchronous database operations.
import asyncio

# datetime and timedelta for generating realistic due dates.
from datetime import date, datetime, timedelta, timezone

# SQLAlchemy select statement.
from sqlalchemy import select

# Database session factory and declarative Base.
from app.database import AsyncSessionLocal

# ORM models.
from app.models.activity import ActivityLog
from app.models.comment import Comment
from app.models.subtask import Subtask
from app.models.task import Task
from app.models.user import User

# Shared enums.
from app.schemas.common import AuthProvider, Priority, Role, Status


async def seed_data() -> None:
    """Populate database with demo accounts, tasks, subtasks, and comments."""
    # Open an async database session.
    async with AsyncSessionLocal() as db:
        # Check if users already exist to ensure idempotency.
        stmt = select(User)
        result = await db.execute(stmt)
        existing_users = result.scalars().all()

        if existing_users:
            print("Database already contains users. Skipping seed execution.")
            return

        print("Seeding initial TaskFlow database...")

        # 1. Create Users.
        admin_user = User(
            name="Ada Lovelace",
            email="ada@taskflow.dev",
            role=Role.ADMIN.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        user_alan = User(
            name="Alan Turing",
            email="alan@taskflow.dev",
            role=Role.USER.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        user_grace = User(
            name="Grace Hopper",
            email="grace@taskflow.dev",
            role=Role.USER.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        user_margaret = User(
            name="Margaret Hamilton",
            email="margaret@taskflow.dev",
            role=Role.USER.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        user_linus = User(
            name="Linus Torvalds",
            email="linus@taskflow.dev",
            role=Role.USER.value,
            provider=AuthProvider.EMAIL,
            is_verified=True,
            email_verified_at=datetime.now(timezone.utc),
        )

        db.add_all([admin_user, user_alan, user_grace, user_margaret, user_linus])
        await db.flush()

        today = date.today()
        yesterday = today - timedelta(days=2)
        tomorrow = today + timedelta(days=1)
        next_week = today + timedelta(days=7)

        # 2. Create Tasks for Alan Turing.
        task1 = Task(
            user_id=user_alan.id,
            title="Design Enigma machine decipher logic",
            description="Formulate state-space reduction algorithms for electro-mechanical rotors.",
            priority=Priority.HIGH.value,
            status=Status.IN_PROGRESS.value,
            due_date=tomorrow,
            tags=["crypto", "theory", "hardware"],
            category="Research",
            position=1.0,
        )
        db.add(task1)
        await db.flush()

        db.add_all([
            Subtask(task_id=task1.id, title="Analyze permutation cycles", is_done=True, position=0),
            Subtask(task_id=task1.id, title="Construct Bombe prototype circuit", is_done=False, position=1),
            Subtask(task_id=task1.id, title="Verify diagonal board wiring", is_done=False, position=2),
        ])

        db.add_all([
            Comment(task_id=task1.id, user_id=admin_user.id, body="The diagonal board design has been approved."),
            Comment(task_id=task1.id, user_id=user_alan.id, body="Excellent, testing relay configurations now."),
        ])

        task2 = Task(
            user_id=user_alan.id,
            title="Universal Turing Machine proof paper",
            description="Finalize draft on computable numbers with an application to the Entscheidungsproblem.",
            priority=Priority.MEDIUM.value,
            status=Status.COMPLETED.value,
            due_date=yesterday,
            tags=["math", "theory"],
            category="Research",
            position=2.0,
        )
        db.add(task2)
        await db.flush()

        db.add_all([
            Subtask(task_id=task2.id, title="Formulate halting problem theorem", is_done=True, position=0),
            Subtask(task_id=task2.id, title="Submit manuscript to London Math Society", is_done=True, position=1),
        ])

        # 3. Create Tasks for Grace Hopper.
        task3 = Task(
            user_id=user_grace.id,
            title="Write first machine-independent compiler",
            description="Create A-0 system translator to translate mathematical code into machine instructions.",
            priority=Priority.HIGH.value,
            status=Status.IN_PROGRESS.value,
            due_date=today,
            tags=["compiler", "software", "innovation"],
            category="Engineering",
            position=1.0,
        )
        db.add(task3)
        await db.flush()

        db.add_all([
            Subtask(task_id=task3.id, title="Map call syntax to UNIVAC subroutines", is_done=True, position=0),
            Subtask(task_id=task3.id, title="Document memory allocation spec", is_done=True, position=1),
            Subtask(task_id=task3.id, title="Benchmark execution speed vs manual assembly", is_done=False, position=2),
        ])

        db.add(Comment(task_id=task3.id, user_id=user_grace.id, body="Compilers make programming accessible to everyone."))

        task4 = Task(
            user_id=user_grace.id,
            title="Investigate Mark II relay malfunction (Moth incident)",
            description="Extract moth trapped in relay #70 to restore computer operation.",
            priority=Priority.LOW.value,
            status=Status.COMPLETED.value,
            due_date=yesterday,
            tags=["hardware", "debugging"],
            category="Maintenance",
            position=2.0,
        )
        db.add(task4)

        # 4. Create Tasks for Margaret Hamilton.
        task5 = Task(
            user_id=user_margaret.id,
            title="Apollo 11 AGC Priority Asynchronous Executive",
            description="Implement priority scheduler so flight computer sheds lower-priority telemetry under radar overload.",
            priority=Priority.HIGH.value,
            status=Status.IN_PROGRESS.value,
            due_date=next_week,
            tags=["avionics", "space", "critical"],
            category="Engineering",
            position=1.0,
        )
        db.add(task5)
        await db.flush()

        db.add_all([
            Subtask(task_id=task5.id, title="Write executive job priority table", is_done=True, position=0),
            Subtask(task_id=task5.id, title="Simulate 1202 alarm load spike", is_done=True, position=1),
            Subtask(task_id=task5.id, title="Deliver rope core memory weave spec", is_done=False, position=2),
        ])

        # 5. Create Tasks for Linus Torvalds.
        task6 = Task(
            user_id=user_linus.id,
            title="Release Linux Kernel 0.01",
            description="Just a hobby, won't be big and professional like gnu. Requires 386 AT hardware.",
            priority=Priority.MEDIUM.value,
            status=Status.COMPLETED.value,
            due_date=yesterday,
            tags=["os", "kernel", "c"],
            category="Systems",
            position=1.0,
        )
        db.add(task6)

        task7 = Task(
            user_id=user_linus.id,
            title="Design Git distributed content tracker",
            description="Create fast content-addressed storage for Linux kernel version control.",
            priority=Priority.HIGH.value,
            status=Status.PENDING.value,
            due_date=next_week,
            tags=["vcs", "c", "tools"],
            category="Systems",
            position=2.0,
        )
        db.add(task7)

        # 6. Seed initial activity log records.
        db.add_all([
            ActivityLog(
                user_id=admin_user.id,
                actor_name=admin_user.name,
                action="created",
                entity_type="user",
                entity_id=user_alan.id,
                detail={"note": "Initial system setup"},
            ),
            ActivityLog(
                user_id=user_alan.id,
                actor_name=user_alan.name,
                action="created",
                entity_type="task",
                entity_id=task1.id,
                detail={"title": task1.title},
            ),
            ActivityLog(
                user_id=user_grace.id,
                actor_name=user_grace.name,
                action="created",
                entity_type="task",
                entity_id=task3.id,
                detail={"title": task3.title},
            ),
        ])

        # Commit entire seed transaction.
        await db.commit()
        print("Database successfully seeded with users, tasks, subtasks, comments, and activity logs!")


if __name__ == "__main__":
    # Run seeding coroutine.
    asyncio.run(seed_data())
