"""
Authentication API endpoints
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.api.deps import get_current_user
from app.models.user import User
from app.repositories.user import UserRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.audit_log import AuditLogRepository
from app.schemas.user import Token, UserResponse, UserCreate

router = APIRouter()


@router.post("/login", response_model=Token)
def login(
    db: Session = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    OAuth2 compatible token login

    Args:
        db: Database session
        form_data: Login form data (username, password)

    Returns:
        Token: Access token and user info

    Raises:
        HTTPException: If authentication fails
    """
    user_repo = UserRepository(db)
    audit_repo = AuditLogRepository(db)

    # Get user by username or email
    user = user_repo.get_by_username(form_data.username)
    if not user:
        user = user_repo.get_by_email(form_data.username)

    # Verify user and password
    if not user or not verify_password(form_data.password, user.hashed_password):
        # Log failed attempt
        audit_repo.log_action(
            user_id=None,
            action="login_failed",
            resource_type="user",
            details={"username": form_data.username},
            status="failed",
            error_message="Invalid credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "org_id": user.organization_id},
        expires_delta=access_token_expires,
    )

    # Record the sign-in. The column has always existed and the user
    # administration screen shows it, so it has to actually be written.
    user.last_login_at = datetime.now(timezone.utc)

    # Log successful login. The audit entry commits the timestamp with it.
    audit_repo.log_action(
        user_id=user.id,
        action="login_success",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username},
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user,
    }


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_in: UserCreate,
    db: Session = Depends(get_db),
):
    """
    Register a new user

    Args:
        user_in: User creation data
        db: Database session

    Returns:
        UserResponse: Created user

    Raises:
        HTTPException: If username/email exists or organization invalid
    """
    user_repo = UserRepository(db)
    org_repo = OrganizationRepository(db)
    audit_repo = AuditLogRepository(db)

    # Check if username exists
    if user_repo.get_by_username(user_in.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    # Check if email exists
    if user_repo.get_by_email(user_in.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Verify organization exists
    organization = org_repo.get(user_in.organization_id)
    if not organization or not organization.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid organization",
        )

    # Create user
    user_data = {
        "organization_id": user_in.organization_id,
        "username": user_in.username,
        "email": user_in.email,
        "hashed_password": get_password_hash(user_in.password),
        "is_admin": user_in.is_admin,
        "is_active": True,
    }

    user = user_repo.create(user_data)

    # Log registration
    audit_repo.log_action(
        user_id=user.id,
        action="user_registered",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "email": user.email},
    )

    return user


@router.get("/me", response_model=UserResponse)
def read_users_me(
    current_user: User = Depends(get_current_user),
):
    """
    Get current user information

    Args:
        current_user: Current authenticated user

    Returns:
        UserResponse: Current user info
    """
    return current_user


@router.post("/logout")
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Logout current user (for audit logging)

    Args:
        current_user: Current authenticated user
        db: Database session

    Returns:
        dict: Success message
    """
    audit_repo = AuditLogRepository(db)

    # Log logout
    audit_repo.log_action(
        user_id=current_user.id,
        action="logout",
        resource_type="user",
        resource_id=current_user.id,
    )

    return {"message": "Successfully logged out"}
