"""
API dependencies for authentication and database access
"""
from typing import Optional, Generator
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import TokenPayload

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """
    Get current authenticated user from JWT token

    Args:
        db: Database session
        token: JWT token from request

    Returns:
        User: Current user

    Raises:
        HTTPException: If token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id_str: str = payload.get("sub")
        org_id: int = payload.get("org_id")

        if user_id_str is None or org_id is None:
            raise credentials_exception

        # Convert user_id from string to int
        user_id: int = int(user_id_str)

    except (JWTError, ValueError):
        raise credentials_exception

    user_repo = UserRepository(db)
    user = user_repo.get(user_id)

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    if user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization mismatch",
        )

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current active user

    Args:
        current_user: Current user from token

    Returns:
        User: Active user

    Raises:
        HTTPException: If user is inactive
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )
    return current_user


def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current user with admin privileges

    Args:
        current_user: Current user from token

    Returns:
        User: Admin user

    Raises:
        HTTPException: If user is not admin
    """
    if not current_user.is_admin and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges",
        )
    return current_user


def get_current_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current user with superuser privileges

    Args:
        current_user: Current user from token

    Returns:
        User: Superuser

    Raises:
        HTTPException: If user is not superuser
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser privileges required",
        )
    return current_user


def get_organization_id(
    current_user: User = Depends(get_current_user),
) -> int:
    """
    Get organization ID from current user (for tenant scoping)

    Args:
        current_user: Current user from token

    Returns:
        int: Organization ID
    """
    return current_user.organization_id


def require_permission(permission: str):
    """
    Build a dependency that enforces one permission

    Used as: current_user: User = Depends(require_permission("devices:write"))

    Permissions come from the user's role; an account with no role falls back
    to the legacy is_admin/is_superuser flags, so nothing that worked before
    roles existed stops working now.

    Args:
        permission: The permission string the endpoint requires

    Returns:
        A FastAPI dependency
    """

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        from app.services.user_admin import user_has_permission

        if not user_has_permission(db, current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{permission}' permission",
            )

        return current_user

    return dependency


def get_current_permissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list:
    """
    The permissions the current user holds

    The frontend uses this to decide what to render, so it does not offer
    actions the API will refuse.
    """
    from app.services.user_admin import effective_permissions

    return effective_permissions(db, current_user)
