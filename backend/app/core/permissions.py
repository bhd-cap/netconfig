"""
Permission catalogue and the built-in roles

Permissions are plain strings of the form "<resource>:<action>", stored on a
Role as a JSONB list. The catalogue below is the authority on which ones
exist: the API validates against it, so a typo in a role definition is
rejected rather than silently granting nothing.

The wildcard "*" grants everything and is what the seeded Administrator role
holds, so a permission added in a later release does not have to be
backfilled onto existing administrator roles.
"""
from typing import Dict, Iterable, List, Set

WILDCARD = "*"

# resource -> {action: human description}
PERMISSION_CATALOGUE: Dict[str, Dict[str, str]] = {
    "devices": {
        "read": "View devices",
        "write": "Add and edit devices",
        "delete": "Delete devices",
        "test": "Test device connectivity",
    },
    "backups": {
        "read": "View and download stored configurations",
        "trigger": "Start backups on demand",
        "delete": "Delete stored configurations",
    },
    "jobs": {
        "read": "View scheduled backup jobs",
        "write": "Create and edit scheduled jobs",
        "delete": "Delete scheduled jobs",
    },
    "discovery": {
        "read": "View discovered neighbours and topology",
        "run": "Start a discovery crawl",
        "write": "Edit and save topology diagrams",
    },
    "inventory": {
        "read": "View the host inventory",
        "write": "Edit inventory notes and refresh inventory",
    },
    "reports": {
        "read": "View and export reports",
    },
    "users": {
        "read": "View users and roles",
        "write": "Create and edit users, assign roles",
        "delete": "Delete users",
        "reset_password": "Reset another user's password",
    },
    "settings": {
        "read": "View application settings",
        "write": "Change application settings",
    },
    "targets": {
        "read": "View remote backup targets",
        "write": "Create and edit remote backup targets",
        "delete": "Delete remote backup targets",
    },
    "audit": {
        "read": "View the audit log",
    },
}


def all_permissions() -> List[str]:
    """Every permission string the catalogue defines, sorted"""
    return sorted(
        f"{resource}:{action}"
        for resource, actions in PERMISSION_CATALOGUE.items()
        for action in actions
    )


def is_valid_permission(permission: str) -> bool:
    """
    Whether a permission string exists in the catalogue

    Accepts the global wildcard and per-resource wildcards ("devices:*").

    Args:
        permission: Permission string

    Returns:
        bool
    """
    if permission == WILDCARD:
        return True

    if ":" not in permission:
        return False

    resource, action = permission.split(":", 1)
    if resource not in PERMISSION_CATALOGUE:
        return False

    return action == "*" or action in PERMISSION_CATALOGUE[resource]


def invalid_permissions(permissions: Iterable[str]) -> List[str]:
    """
    Return the entries that are not valid permissions

    Args:
        permissions: Candidate permission strings

    Returns:
        List of the invalid ones, in order
    """
    return [p for p in permissions if not is_valid_permission(p)]


def expand(permissions: Iterable[str]) -> Set[str]:
    """
    Expand wildcards into concrete permissions

    Args:
        permissions: Permission strings, possibly containing wildcards

    Returns:
        Set of concrete permission strings
    """
    granted: Set[str] = set()

    for permission in permissions:
        if permission == WILDCARD:
            return set(all_permissions())

        if permission.endswith(":*"):
            resource = permission[:-2]
            granted.update(
                f"{resource}:{action}"
                for action in PERMISSION_CATALOGUE.get(resource, {})
            )
            continue

        granted.add(permission)

    return granted


def has_permission(granted: Iterable[str], required: str) -> bool:
    """
    Whether a set of granted permissions satisfies a requirement

    Args:
        granted: Permissions held (may contain wildcards)
        required: The permission being checked

    Returns:
        bool
    """
    granted = list(granted)

    if WILDCARD in granted:
        return True

    if required in granted:
        return True

    resource = required.split(":", 1)[0]
    return f"{resource}:*" in granted


# --------------------------------------------------------------------------
# Built-in roles
# --------------------------------------------------------------------------

SYSTEM_ROLES: Dict[str, Dict[str, object]] = {
    "Administrator": {
        "description": "Full access to everything in the organization",
        "permissions": [WILDCARD],
        "is_admin": True,
    },
    "Operator": {
        "description": "Run backups and discovery, manage devices, no user or settings administration",
        "permissions": [
            "devices:*",
            "backups:*",
            "jobs:*",
            "discovery:*",
            "inventory:*",
            "reports:read",
            "targets:read",
        ],
        "is_admin": False,
    },
    "Viewer": {
        "description": "Read-only access",
        "permissions": [
            "devices:read",
            "backups:read",
            "jobs:read",
            "discovery:read",
            "inventory:read",
            "reports:read",
            "targets:read",
        ],
        "is_admin": False,
    },
}


def role_implies_admin(permissions: Iterable[str]) -> bool:
    """
    Whether a permission set amounts to administrative access

    The legacy is_admin flag is kept in sync with this so older checks, and
    the frontend's admin-only routes, keep behaving sensibly.

    Args:
        permissions: Permissions held by the role

    Returns:
        bool
    """
    return has_permission(permissions, "users:write") or WILDCARD in list(permissions)
