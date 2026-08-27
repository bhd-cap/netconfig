"""
User and role administration

Roles carry the permissions; the legacy is_admin and is_superuser booleans on
User are kept in step with whatever role is assigned, so existing checks and
the frontend's admin-only routes keep working while permissions move to the
role model.

Two rules protect an organization from locking itself out, and both are
enforced here rather than in the API so every caller gets them:

  * the last active administrator cannot be deactivated, demoted or deleted
  * a system role cannot be deleted
"""
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import (
    SYSTEM_ROLES,
    all_permissions,
    expand,
    has_permission,
    invalid_permissions,
    role_implies_admin,
)
from app.core.security import get_password_hash
from app.models.administration import Role
from app.models.user import User

logger = logging.getLogger(__name__)


class UserAdminError(RuntimeError):
    """Raised when an administrative action is not allowed"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def seed_system_roles(db: Session, organization_id: int) -> List[Role]:
    """
    Create the built-in roles for an organization if they are missing

    Idempotent, so it can run on every startup and after an upgrade that adds
    a role.

    Args:
        db: Database session
        organization_id: Organization to seed

    Returns:
        The organization's system roles
    """
    existing = {
        role.name: role
        for role in db.execute(
            select(Role).where(Role.organization_id == organization_id)
        ).scalars()
    }

    created = []
    for name, definition in SYSTEM_ROLES.items():
        if name in existing:
            continue

        role = Role(
            organization_id=organization_id,
            name=name,
            description=definition["description"],
            permissions=list(definition["permissions"]),
            is_system=True,
        )
        db.add(role)
        created.append(role)

    if created:
        db.commit()
        logger.info(
            f"Seeded {len(created)} system role(s) for organization {organization_id}"
        )

    return list(existing.values()) + created


def get_role(db: Session, role_id: int, organization_id: int) -> Optional[Role]:
    """Fetch a role within an organization"""
    return db.execute(
        select(Role).where(
            Role.id == role_id, Role.organization_id == organization_id
        )
    ).scalar_one_or_none()


def create_role(
    db: Session,
    organization_id: int,
    name: str,
    permissions: Sequence[str],
    description: Optional[str] = None,
) -> Role:
    """
    Create a role

    Args:
        db: Database session
        organization_id: Owning organization
        name: Role name, unique per organization
        permissions: Permission strings
        description: Free text

    Returns:
        The new role

    Raises:
        UserAdminError: On a duplicate name or an unknown permission
    """
    bad = invalid_permissions(permissions)
    if bad:
        raise UserAdminError(
            f"Unknown permission(s): {', '.join(bad)}. "
            f"Valid permissions are: {', '.join(all_permissions())}"
        )

    clash = db.execute(
        select(Role.id).where(
            Role.organization_id == organization_id, Role.name == name
        )
    ).scalar()
    if clash:
        raise UserAdminError(f"A role named '{name}' already exists")

    role = Role(
        organization_id=organization_id,
        name=name,
        description=description,
        permissions=list(permissions),
        is_system=False,
    )
    db.add(role)
    db.commit()

    return role


def update_role(
    db: Session,
    role: Role,
    name: Optional[str] = None,
    permissions: Optional[Sequence[str]] = None,
    description: Optional[str] = None,
) -> Role:
    """
    Change a role

    A system role's permissions can be edited but it cannot be renamed, so
    the seeding above keeps recognising it.

    Args:
        db: Database session
        role: The role to change
        name: New name
        permissions: New permission list
        description: New description

    Returns:
        The updated role

    Raises:
        UserAdminError: On an invalid change
    """
    if permissions is not None:
        bad = invalid_permissions(permissions)
        if bad:
            raise UserAdminError(f"Unknown permission(s): {', '.join(bad)}")

        # Changing a role's permissions can demote its holders, so the
        # last-administrator rule applies here too.
        if role_implies_admin(role.permissions) and not role_implies_admin(permissions):
            _assert_not_last_admin_role(db, role)

        role.permissions = list(permissions)

    if name is not None and name != role.name:
        if role.is_system:
            raise UserAdminError("A built-in role cannot be renamed")

        clash = db.execute(
            select(Role.id).where(
                Role.organization_id == role.organization_id,
                Role.name == name,
                Role.id != role.id,
            )
        ).scalar()
        if clash:
            raise UserAdminError(f"A role named '{name}' already exists")

        role.name = name

    if description is not None:
        role.description = description

    db.commit()
    _sync_role_holders(db, role)

    return role


def delete_role(db: Session, role: Role) -> None:
    """
    Delete a role

    Args:
        db: Database session
        role: The role to delete

    Raises:
        UserAdminError: If it is a system role or still has holders
    """
    if role.is_system:
        raise UserAdminError("A built-in role cannot be deleted")

    holders = db.scalar(
        select(func.count(User.id)).where(User.role_id == role.id)
    )
    if holders:
        raise UserAdminError(
            f"{holders} user(s) still hold this role; reassign them first"
        )

    db.delete(role)
    db.commit()


def _sync_role_holders(db: Session, role: Role) -> None:
    """Bring the legacy admin flags on a role's holders back in step"""
    is_admin = role_implies_admin(role.permissions)

    db.execute(
        User.__table__.update()
        .where(User.role_id == role.id)
        .values(is_admin=is_admin)
    )
    db.commit()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def effective_permissions(db: Session, user: User) -> List[str]:
    """
    The permissions a user actually holds

    A user with no role falls back to the legacy flags, so accounts that
    predate roles keep working exactly as they did.

    Args:
        db: Database session
        user: The user

    Returns:
        Sorted permission strings
    """
    if user.role_id:
        role = db.get(Role, user.role_id)
        if role:
            return sorted(expand(role.permissions))

    if user.is_superuser or user.is_admin:
        return all_permissions()

    # No role and not an administrator: read-only, matching what such an
    # account could previously see.
    return sorted(
        expand(
            [
                "devices:read",
                "backups:read",
                "jobs:read",
                "discovery:read",
                "inventory:read",
                "reports:read",
            ]
        )
    )


def user_has_permission(db: Session, user: User, permission: str) -> bool:
    """
    Whether a user may perform an action

    Args:
        db: Database session
        user: The user
        permission: Required permission

    Returns:
        bool
    """
    if user.is_superuser:
        return True

    return has_permission(effective_permissions(db, user), permission)


def count_active_admins(db: Session, organization_id: int, exclude_user_id: int = None) -> int:
    """
    How many active administrators an organization has

    Args:
        db: Database session
        organization_id: Organization
        exclude_user_id: Ignore this user (the one being changed)

    Returns:
        Count
    """
    statement = select(func.count(User.id)).where(
        User.organization_id == organization_id,
        User.is_active.is_(True),
        User.is_admin.is_(True),
    )
    if exclude_user_id:
        statement = statement.where(User.id != exclude_user_id)

    return db.scalar(statement) or 0


def _assert_not_last_admin(db: Session, user: User, action: str) -> None:
    """
    Refuse a change that would leave an organization with no administrator

    Args:
        db: Database session
        user: The user being changed
        action: What is being attempted, for the message

    Raises:
        UserAdminError
    """
    if not user.is_admin or not user.is_active:
        return

    if count_active_admins(db, user.organization_id, exclude_user_id=user.id) == 0:
        raise UserAdminError(
            f"Cannot {action}: this is the only active administrator in the "
            f"organization. Promote another user first."
        )


def _assert_not_last_admin_role(db: Session, role: Role) -> None:
    """Refuse a role change that would remove the last administrator"""
    holders = db.scalar(
        select(func.count(User.id)).where(
            User.role_id == role.id, User.is_active.is_(True)
        )
    )
    if not holders:
        return

    others = db.scalar(
        select(func.count(User.id)).where(
            User.organization_id == role.organization_id,
            User.is_active.is_(True),
            User.is_admin.is_(True),
            User.role_id != role.id,
        )
    )
    if not others:
        raise UserAdminError(
            "Cannot remove administrative permissions from this role: its "
            "holders are the only administrators in the organization."
        )


def generate_password(length: int = 16) -> str:
    """
    Generate a random password

    Args:
        length: Number of characters

    Returns:
        A password drawn from letters, digits and a few symbols
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_="
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_user(
    db: Session,
    organization_id: int,
    username: str,
    email: str,
    password: Optional[str] = None,
    role_id: Optional[int] = None,
    full_name: Optional[str] = None,
    is_active: bool = True,
    must_change_password: bool = True,
) -> Dict:
    """
    Create a user

    Args:
        db: Database session
        organization_id: Owning organization
        username: Login name, unique across the installation
        email: Email address, unique across the installation
        password: Password; generated when omitted
        role_id: Role to assign
        full_name: Display name
        is_active: Whether the account is enabled
        must_change_password: Require a change at first login

    Returns:
        dict with the user and, when generated, the password to hand over

    Raises:
        UserAdminError: On a duplicate username or email, or an unknown role
    """
    if db.execute(select(User.id).where(User.username == username)).scalar():
        raise UserAdminError(f"Username '{username}' is already taken")

    if db.execute(select(User.id).where(User.email == email)).scalar():
        raise UserAdminError(f"Email '{email}' is already registered")

    role = None
    if role_id is not None:
        role = get_role(db, role_id, organization_id)
        if not role:
            raise UserAdminError(f"Role {role_id} not found in this organization")

    generated = password is None
    if generated:
        password = generate_password()

    user = User(
        organization_id=organization_id,
        username=username,
        email=email,
        full_name=full_name,
        hashed_password=get_password_hash(password),
        is_active=is_active,
        is_admin=role_implies_admin(role.permissions) if role else False,
        is_superuser=False,
        role_id=role.id if role else None,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.commit()

    logger.info(
        f"Created user {username} in organization {organization_id} "
        f"with role {role.name if role else 'none'}"
    )

    return {"user": user, "generated_password": password if generated else None}


def update_user(
    db: Session,
    user: User,
    email: Optional[str] = None,
    full_name: Optional[str] = None,
    role_id: Optional[int] = None,
    clear_role: bool = False,
) -> User:
    """
    Change a user's details or role

    Args:
        db: Database session
        user: The user
        email: New email
        full_name: New display name
        role_id: New role
        clear_role: Remove the role instead of setting one

    Returns:
        The updated user

    Raises:
        UserAdminError: On a duplicate email, unknown role, or removing the
            last administrator
    """
    if email is not None and email != user.email:
        clash = db.execute(
            select(User.id).where(User.email == email, User.id != user.id)
        ).scalar()
        if clash:
            raise UserAdminError(f"Email '{email}' is already registered")
        user.email = email

    if full_name is not None:
        user.full_name = full_name

    if clear_role:
        _assert_not_last_admin(db, user, "remove the role from this user")
        user.role_id = None
        user.is_admin = False
    elif role_id is not None and role_id != user.role_id:
        role = get_role(db, role_id, user.organization_id)
        if not role:
            raise UserAdminError(f"Role {role_id} not found in this organization")

        becomes_admin = role_implies_admin(role.permissions)
        if user.is_admin and not becomes_admin:
            _assert_not_last_admin(db, user, "change this user's role")

        user.role_id = role.id
        user.is_admin = becomes_admin

    db.commit()
    return user


def set_active(db: Session, user: User, is_active: bool) -> User:
    """
    Activate or deactivate a user

    Args:
        db: Database session
        user: The user
        is_active: Desired state

    Returns:
        The updated user

    Raises:
        UserAdminError: When deactivating the last administrator
    """
    if not is_active:
        _assert_not_last_admin(db, user, "deactivate this user")

    user.is_active = is_active
    user.deactivated_at = None if is_active else _now()
    db.commit()

    logger.info(f"User {user.username} {'activated' if is_active else 'deactivated'}")
    return user


def reset_password(
    db: Session,
    user: User,
    new_password: Optional[str] = None,
    must_change: bool = True,
) -> str:
    """
    Reset a user's password

    Args:
        db: Database session
        user: The user
        new_password: The password to set; generated when omitted
        must_change: Require a change at next login

    Returns:
        The password that was set, so it can be handed over once
    """
    password = new_password or generate_password()

    user.hashed_password = get_password_hash(password)
    user.must_change_password = must_change
    db.commit()

    logger.info(f"Password reset for user {user.username}")
    return password


def change_own_password(
    db: Session, user: User, current_password: str, new_password: str
) -> None:
    """
    Change a user's own password, verifying the current one

    Args:
        db: Database session
        user: The user
        current_password: Their existing password
        new_password: The replacement

    Raises:
        UserAdminError: If the current password is wrong or the new one is weak
    """
    from app.core.security import verify_password

    if not verify_password(current_password, user.hashed_password):
        raise UserAdminError("The current password is incorrect")

    if len(new_password) < 8:
        raise UserAdminError("The new password must be at least 8 characters")

    if new_password == current_password:
        raise UserAdminError("The new password must differ from the current one")

    user.hashed_password = get_password_hash(new_password)
    user.must_change_password = False
    db.commit()


def delete_user(db: Session, user: User) -> None:
    """
    Delete a user

    Args:
        db: Database session
        user: The user

    Raises:
        UserAdminError: When deleting the last administrator
    """
    _assert_not_last_admin(db, user, "delete this user")

    db.delete(user)
    db.commit()

    logger.info(f"Deleted user {user.username}")
