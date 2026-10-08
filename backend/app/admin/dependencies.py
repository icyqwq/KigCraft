from fastapi import Depends, HTTPException

from app.auth.models import UserRole
from app.auth.sessions import get_current_user
from app.db.models import User


async def require_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
