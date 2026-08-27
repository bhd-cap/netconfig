"""
User and role administration endpoints

Everything here is tenant-scoped: an administrator can only see and change
users and roles inside their own organization. The one exception is the
"/me" family, which acts on the caller.

Passwords are never returned by a read endpoint. A generated or reset
password is returned exactly once, in the response to the call that set it,
because there is nowhere else to get it from afterwards.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    get_current_user,
    get_organization_id,
    require_permission,
)
from app.core.database import get_db
from app.core.permissions import PERMISSION_CATALOGUE, all_permissions
from app.models.administration import Role
from app.models.user import User
from app.repositories.audit_log import AuditLogRepository
from app.schemas.common import PaginatedResponse
from app.services import user_admin
from app.services.user_admin import UserAdminError

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class RoleSummary(BaseModel):
    """A role, as referenced from a user"""

    id: int
    name: str
    is_system: bool

    class Config:
        from_attributes = True


class RoleResponse(BaseModel):
    """A role"""

    id: int
    name: str
    description: Optional[str]
    permissions: List[str]
    is_system: bool
    user_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class RoleCreate(BaseModel):
    """Create a role"""

    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    permissions: List[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Change a role"""

    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)
    permissions: Optional[List[str]] = None


class AdminUserResponse(BaseModel):
    """A user, as an administrator sees them"""

    id: int
    organization_id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    is_admin: bool
    is_superuser: bool
    must_change_password: bool
    role_id: Optional[int]
    role: Optional[RoleSummary]
    last_login_at: Optional[datetime]
    deactivated_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class UserCreateRequest(BaseModel):
    """Create a user"""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=255)
    password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=100,
        description="Left out, a password is generated and returned once",
    )
    role_id: Optional[int] = None
    is_active: bool = True
    must_change_password: bool = True


class UserUpdateRequest(BaseModel):
    """Change a user's details or role"""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=255)
    role_id: Optional[int] = None
    clear_role: bool = False


class PasswordResetRequest(BaseModel):
    """Reset another user's password"""

    new_password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=100,
        description="Left out, a password is generated and returned once",
    )
    must_change: bool = True


class PasswordChangeRequest(BaseModel):
    """Change your own password"""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=100)


class UserCreatedResponse(BaseModel):
    """A new user plus the one-time password, when one was generated"""

    user: AdminUserResponse
    generated_password: Optional[str] = None


class PasswordResetResponse(BaseModel):
    """The password that was set, returned once"""

    user_id: int
    username: str
    password: str
    must_change_password: bool


class ActivationRequest(BaseModel):
    """Activate or deactivate a user"""

    is_active: bool


class PermissionEntry(BaseModel):
    """One permission in the catalogue"""

    permission: str
    resource: str
    action: str
    description: str


class MeResponse(BaseModel):
    """The caller, with what they are allowed to do"""

    id: int
    organization_id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    is_admin: bool
    is_superuser: bool
    must_change_password: bool
    role: Optional[RoleSummary]
    permissions: List[str]

    class Config:
        from_attributes = True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort client address for the audit log"""
    return request.client.host if request.client else None


def _get_user_or_404(db: Session, user_id: int, organization_id: int) -> User:
    """Fetch a user inside the caller's organization or raise 404"""
    user = db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.id == user_id, User.organization_id == organization_id)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return user


def _get_role_or_404(db: Session, role_id: int, organization_id: int) -> Role:
    """Fetch a role inside the caller's organization or raise 404"""
    role = user_admin.get_role(db, role_id, organization_id)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
        )
    return role


# --------------------------------------------------------------------------
# The caller
# --------------------------------------------------------------------------


@router.get("/me", response_model=MeResponse)
def read_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The current user, their role and the permissions they hold

    The frontend uses the permission list to decide what to render, so it
    does not offer actions the API will refuse.
    """
    return MeResponse(
        id=current_user.id,
        organization_id=current_user.organization_id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin,
        is_superuser=current_user.is_superuser,
        must_change_password=current_user.must_change_password,
        role=RoleSummary.model_validate(current_user.role)
        if current_user.role
        else None,
        permissions=user_admin.effective_permissions(db, current_user),
    )


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Change your own password

    Requires the current password, so a stolen session cannot lock the owner
    out of their own account.
    """
    try:
        user_admin.change_own_password(
            db, current_user, payload.current_password, payload.new_password
        )
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="change_password",
        resource_type="user",
        resource_id=current_user.id,
        ip_address=_client_ip(request),
    )


# --------------------------------------------------------------------------
# Permission catalogue
# --------------------------------------------------------------------------


@router.get("/permissions", response_model=List[PermissionEntry])
def list_permissions(
    current_user: User = Depends(require_permission("users:read")),
):
    """
    Every permission that can be granted

    Drives the role editor, so it never offers a permission the API would
    reject.
    """
    return [
        PermissionEntry(
            permission=f"{resource}:{action}",
            resource=resource,
            action=action,
            description=description,
        )
        for resource, actions in PERMISSION_CATALOGUE.items()
        for action, description in actions.items()
    ]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


@router.get("/roles", response_model=List[RoleResponse])
def list_roles(
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:read")),
):
    """
    Roles in the organization, with how many users hold each

    The built-in roles are seeded on first use, so a fresh organization is
    not presented with an empty list.
    """
    user_admin.seed_system_roles(db, organization_id)

    counts = dict(
        db.execute(
            select(User.role_id, func.count(User.id))
            .where(User.organization_id == organization_id)
            .group_by(User.role_id)
        ).all()
    )

    roles = list(
        db.execute(
            select(Role)
            .where(Role.organization_id == organization_id)
            .order_by(Role.is_system.desc(), Role.name)
        ).scalars()
    )

    return [
        RoleResponse(
            id=role.id,
            name=role.name,
            description=role.description,
            permissions=list(role.permissions or []),
            is_system=role.is_system,
            user_count=counts.get(role.id, 0),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
        for role in roles
    ]


@router.post(
    "/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED
)
def create_role(
    payload: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """Create a role"""
    try:
        role = user_admin.create_role(
            db,
            organization_id=organization_id,
            name=payload.name,
            permissions=payload.permissions,
            description=payload.description,
        )
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="create_role",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name, "permissions": role.permissions},
        ip_address=_client_ip(request),
    )

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=list(role.permissions or []),
        is_system=role.is_system,
        user_count=0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@router.get("/roles/{role_id}", response_model=RoleResponse)
def read_role(
    role_id: int,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:read")),
):
    """One role"""
    role = _get_role_or_404(db, role_id, organization_id)

    holders = db.scalar(select(func.count(User.id)).where(User.role_id == role.id))

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=list(role.permissions or []),
        is_system=role.is_system,
        user_count=holders or 0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@router.put("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """
    Change a role

    A built-in role's permissions can be tuned but it cannot be renamed, and
    a change that would leave the organization without an administrator is
    refused.
    """
    role = _get_role_or_404(db, role_id, organization_id)

    try:
        role = user_admin.update_role(
            db,
            role,
            name=payload.name,
            permissions=payload.permissions,
            description=payload.description,
        )
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="update_role",
        resource_type="role",
        resource_id=role.id,
        details={"name": role.name, "permissions": role.permissions},
        ip_address=_client_ip(request),
    )

    holders = db.scalar(select(func.count(User.id)).where(User.role_id == role.id))

    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=list(role.permissions or []),
        is_system=role.is_system,
        user_count=holders or 0,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """Delete a role that no one holds"""
    role = _get_role_or_404(db, role_id, organization_id)
    name = role.name

    try:
        user_admin.delete_role(db, role)
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="delete_role",
        resource_type="role",
        resource_id=role_id,
        details={"name": name},
        ip_address=_client_ip(request),
    )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[AdminUserResponse])
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Match username, email or name"),
    role_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:read")),
):
    """Users in the organization"""
    conditions = [User.organization_id == organization_id]

    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                User.full_name.ilike(pattern),
            )
        )

    if role_id is not None:
        conditions.append(User.role_id == role_id)

    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))

    total = db.scalar(select(func.count(User.id)).where(*conditions)) or 0

    users = list(
        db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(*conditions)
            .order_by(User.username)
            .offset(skip)
            .limit(limit)
        ).scalars()
    )

    page_size = limit or 1

    return PaginatedResponse[AdminUserResponse](
        total=total,
        page=(skip // page_size) + 1,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[AdminUserResponse.model_validate(user) for user in users],
    )


@router.post(
    "", response_model=UserCreatedResponse, status_code=status.HTTP_201_CREATED
)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """
    Add a user

    With no password supplied one is generated and returned in this response
    only, so it has to be handed over now.
    """
    user_admin.seed_system_roles(db, organization_id)

    try:
        result = user_admin.create_user(
            db,
            organization_id=organization_id,
            username=payload.username,
            email=str(payload.email),
            password=payload.password,
            role_id=payload.role_id,
            full_name=payload.full_name,
            is_active=payload.is_active,
            must_change_password=payload.must_change_password,
        )
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        )

    user = result["user"]

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="create_user",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "role_id": user.role_id},
        ip_address=_client_ip(request),
    )

    return UserCreatedResponse(
        user=AdminUserResponse.model_validate(user),
        generated_password=result["generated_password"],
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
def read_user(
    user_id: int,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:read")),
):
    """One user"""
    return AdminUserResponse.model_validate(
        _get_user_or_404(db, user_id, organization_id)
    )


@router.get("/{user_id}/permissions", response_model=List[str])
def read_user_permissions(
    user_id: int,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:read")),
):
    """What a user is actually allowed to do, after wildcards are expanded"""
    user = _get_user_or_404(db, user_id, organization_id)
    return user_admin.effective_permissions(db, user)


@router.put("/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """
    Change a user's details or role

    A change that would leave the organization with no active administrator
    is refused.
    """
    user = _get_user_or_404(db, user_id, organization_id)

    try:
        user = user_admin.update_user(
            db,
            user,
            email=str(payload.email) if payload.email else None,
            full_name=payload.full_name,
            role_id=payload.role_id,
            clear_role=payload.clear_role,
        )
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="update_user",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "role_id": user.role_id},
        ip_address=_client_ip(request),
    )

    db.refresh(user, ["role"])
    return AdminUserResponse.model_validate(user)


@router.post("/{user_id}/activation", response_model=AdminUserResponse)
def set_user_activation(
    user_id: int,
    payload: ActivationRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:write")),
):
    """
    Activate or deactivate a user

    Deactivating is preferred over deleting: the audit log keeps referring to
    the account, and reactivating restores exactly what was there before.
    """
    user = _get_user_or_404(db, user_id, organization_id)

    if user.id == current_user.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    try:
        user = user_admin.set_active(db, user, payload.is_active)
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="activate_user" if payload.is_active else "deactivate_user",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username},
        ip_address=_client_ip(request),
    )

    return AdminUserResponse.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=PasswordResetResponse)
def reset_user_password(
    user_id: int,
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:reset_password")),
):
    """
    Reset another user's password

    The password comes back in this response and nowhere else. By default the
    user must change it at next login.
    """
    user = _get_user_or_404(db, user_id, organization_id)

    password = user_admin.reset_password(
        db, user, new_password=payload.new_password, must_change=payload.must_change
    )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="reset_password",
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "generated": payload.new_password is None},
        ip_address=_client_ip(request),
    )

    return PasswordResetResponse(
        user_id=user.id,
        username=user.username,
        password=password,
        must_change_password=user.must_change_password,
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("users:delete")),
):
    """
    Delete a user

    Deactivation is usually the better answer; this exists for accounts
    created by mistake.
    """
    user = _get_user_or_404(db, user_id, organization_id)

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    username = user.username

    try:
        user_admin.delete_user(db, user)
    except UserAdminError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="delete_user",
        resource_type="user",
        resource_id=user_id,
        details={"username": username},
        ip_address=_client_ip(request),
    )
