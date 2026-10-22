"""FastAPI routers mounted under the API version prefix."""

# Individual domain routers.
from app.routers.admin import router as admin_router
from app.routers.assistant import router as assistant_router
from app.routers.auth import router as auth_router
from app.routers.comments import router as comments_router
from app.routers.tasks import router as tasks_router
from app.routers.transfer import router as transfer_router
from app.routers.users import router as users_router

__all__ = [
    "admin_router",
    "assistant_router",
    "auth_router",
    "comments_router",
    "tasks_router",
    "transfer_router",
    "users_router",
]
