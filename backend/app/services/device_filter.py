"""
Resolve a scheduled job's device filter into a set of device IDs

`BackupJob.device_filter` is a JSONB document describing which of an
organization's devices the job should back up. Every criterion present is
ANDed; within a criterion, a list of values is ORed. An empty or absent filter
means "every device that can be backed up", which is what a job created before
filtering existed does.

    {
      "device_ids":         [1, 2, 3],
      "exclude_device_ids": [9],
      "device_types":       ["cisco_ios", "arista_eos"],
      "locations":          ["NYC", "LON"],
      "hostname_pattern":   "core-*",
      "tags":               {"role": "core", "env": "prod"},
      "transports":         ["ssh", "telnet"],
      "include_inactive":   false,
      "include_snmp":       false
    }

Unknown keys are rejected rather than ignored. A filter that silently matches
nothing - or everything - is the worst possible failure here: it either skips
backups nobody notices are missing, or quietly widens a job past what someone
intended.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.device_types import DEVICE_TYPE_CONFIG
from app.config.discovery_commands import SUPPORTED_TRANSPORTS
from app.models.device import Device

logger = logging.getLogger(__name__)


class FilterError(ValueError):
    """Raised when a device filter is malformed"""


# Every key a filter may carry, and whether it is a list, a scalar or a map.
LIST_KEYS = (
    "device_ids",
    "exclude_device_ids",
    "device_types",
    "locations",
    "transports",
)
SCALAR_KEYS = ("hostname_pattern",)
FLAG_KEYS = ("include_inactive", "include_snmp")
MAP_KEYS = ("tags",)

ALLOWED_KEYS = frozenset(LIST_KEYS + SCALAR_KEYS + FLAG_KEYS + MAP_KEYS)

# A job selecting more than this many devices is almost certainly a filter
# mistake rather than an intention, but it is not this module's place to
# refuse - the caller logs it.
LARGE_SELECTION = 500


def is_empty(device_filter: Optional[Dict[str, Any]]) -> bool:
    """
    Whether a filter constrains anything at all

    `{}`, `None` and a filter whose every value is empty all mean "no
    constraint", so they are treated alike.

    Args:
        device_filter: The stored filter

    Returns:
        bool
    """
    if not device_filter:
        return True

    return not any(
        device_filter.get(key) not in (None, [], {}, "")
        for key in ALLOWED_KEYS
        if key not in FLAG_KEYS
    )


def validate(device_filter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Check a filter and return it normalised for storage

    Args:
        device_filter: Raw filter from a request

    Returns:
        The normalised filter; `{}` when nothing is constrained

    Raises:
        FilterError: On an unknown key or a malformed value
    """
    if not device_filter:
        return {}

    if not isinstance(device_filter, dict):
        raise FilterError("A device filter must be an object")

    unknown = sorted(set(device_filter) - ALLOWED_KEYS)
    if unknown:
        raise FilterError(
            f"Unknown filter key(s): {', '.join(unknown)}. "
            f"Valid keys are: {', '.join(sorted(ALLOWED_KEYS))}"
        )

    normalised: Dict[str, Any] = {}

    for key in ("device_ids", "exclude_device_ids"):
        value = device_filter.get(key)
        if value in (None, []):
            continue
        if not isinstance(value, (list, tuple)):
            raise FilterError(f"'{key}' must be a list of device ids")
        try:
            ids = sorted({int(entry) for entry in value})
        except (TypeError, ValueError):
            raise FilterError(f"'{key}' must contain only integers")
        if any(entry < 1 for entry in ids):
            raise FilterError(f"'{key}' contains an invalid device id")
        normalised[key] = ids

    value = device_filter.get("device_types")
    if value:
        if not isinstance(value, (list, tuple)):
            raise FilterError("'device_types' must be a list")
        bad = [entry for entry in value if entry not in DEVICE_TYPE_CONFIG]
        if bad:
            raise FilterError(
                f"Unknown device type(s): {', '.join(map(str, bad))}. "
                f"Valid types are: {', '.join(sorted(DEVICE_TYPE_CONFIG))}"
            )
        normalised["device_types"] = sorted(set(value))

    value = device_filter.get("transports")
    if value:
        if not isinstance(value, (list, tuple)):
            raise FilterError("'transports' must be a list")
        bad = [entry for entry in value if entry not in SUPPORTED_TRANSPORTS]
        if bad:
            raise FilterError(
                f"Unknown transport(s): {', '.join(map(str, bad))}. "
                f"Valid transports are: {', '.join(SUPPORTED_TRANSPORTS)}"
            )
        normalised["transports"] = sorted(set(value))

    value = device_filter.get("locations")
    if value:
        if not isinstance(value, (list, tuple)):
            raise FilterError("'locations' must be a list")
        locations = sorted({str(entry).strip() for entry in value if str(entry).strip()})
        if locations:
            normalised["locations"] = locations

    value = device_filter.get("hostname_pattern")
    if value not in (None, ""):
        if not isinstance(value, str):
            raise FilterError("'hostname_pattern' must be a string")
        pattern = value.strip()
        if pattern:
            if len(pattern) > 255:
                raise FilterError("'hostname_pattern' is too long")
            normalised["hostname_pattern"] = pattern

    value = device_filter.get("tags")
    if value:
        if not isinstance(value, dict):
            raise FilterError("'tags' must be an object of key/value pairs")
        tags = {
            str(key_): value_
            for key_, value_ in value.items()
            if str(key_).strip() != ""
        }
        for key_, value_ in tags.items():
            if isinstance(value_, (dict, list)):
                raise FilterError(
                    f"Tag '{key_}' must be a string, number or boolean"
                )
        if tags:
            normalised["tags"] = tags

    for key in FLAG_KEYS:
        if key in device_filter:
            normalised[key] = bool(device_filter[key])

    return normalised


def _like_pattern(pattern: str) -> str:
    """
    Turn a glob into a SQL LIKE pattern

    Operators write `core-*`, not `core-%`. Any literal `%` or `_` already in
    the string is escaped first, so a hostname containing one is matched
    literally rather than becoming a wildcard.
    """
    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.replace("*", "%").replace("?", "_")


def _conditions(organization_id: int, device_filter: Dict[str, Any]) -> list:
    """Build the WHERE clauses for a normalised filter"""
    conditions = [Device.organization_id == organization_id]

    if not device_filter.get("include_inactive"):
        conditions.append(Device.is_active.is_(True))

    # SNMP is read-only: such a device cannot produce a configuration, so a
    # scheduled backup that includes one fails every single run. Excluded
    # unless the filter names SNMP explicitly or opts in.
    transports = device_filter.get("transports")
    if transports:
        conditions.append(Device.transport.in_(transports))
    elif not device_filter.get("include_snmp"):
        conditions.append(Device.transport != "snmp")

    if device_filter.get("device_ids"):
        conditions.append(Device.id.in_(device_filter["device_ids"]))

    if device_filter.get("exclude_device_ids"):
        conditions.append(Device.id.notin_(device_filter["exclude_device_ids"]))

    if device_filter.get("device_types"):
        conditions.append(Device.device_type.in_(device_filter["device_types"]))

    if device_filter.get("locations"):
        conditions.append(Device.location.in_(device_filter["locations"]))

    if device_filter.get("hostname_pattern"):
        conditions.append(
            Device.hostname.ilike(
                _like_pattern(device_filter["hostname_pattern"]), escape="\\"
            )
        )

    if device_filter.get("tags"):
        # JSONB containment: every pair must be present on the device.
        conditions.append(Device.tags.contains(device_filter["tags"]))

    return conditions


def resolve(
    db: Session,
    organization_id: int,
    device_filter: Optional[Dict[str, Any]] = None,
) -> List[int]:
    """
    The device IDs a filter selects, within one organization

    Only identifiers are read, so the encrypted credentials on every candidate
    row are never materialised just to pick a set.

    Args:
        db: Database session
        organization_id: Tenant scope
        device_filter: The job's filter; every backable device when empty

    Returns:
        Device IDs, ascending

    Raises:
        FilterError: If the stored filter is malformed
    """
    normalised = validate(device_filter)
    conditions = _conditions(organization_id, normalised)

    return list(
        db.scalars(select(Device.id).where(*conditions).order_by(Device.id)).all()
    )


def count(
    db: Session,
    organization_id: int,
    device_filter: Optional[Dict[str, Any]] = None,
) -> int:
    """
    How many devices a filter selects

    Used by the job editor to show a match count without listing everything.

    Args:
        db: Database session
        organization_id: Tenant scope
        device_filter: The job's filter

    Returns:
        Number of matching devices
    """
    normalised = validate(device_filter)
    conditions = _conditions(organization_id, normalised)

    return db.scalar(select(func.count(Device.id)).where(*conditions)) or 0


def preview(
    db: Session,
    organization_id: int,
    device_filter: Optional[Dict[str, Any]] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    What a filter would select, for showing before a job is saved

    Args:
        db: Database session
        organization_id: Tenant scope
        device_filter: The job's filter
        limit: How many devices to name

    Returns:
        dict with the total, a capped device list and a human summary
    """
    normalised = validate(device_filter)
    conditions = _conditions(organization_id, normalised)

    total = db.scalar(select(func.count(Device.id)).where(*conditions)) or 0

    rows = db.execute(
        select(
            Device.id,
            Device.hostname,
            Device.ip_address,
            Device.device_type,
            Device.location,
            Device.transport,
            Device.is_active,
        )
        .where(*conditions)
        .order_by(Device.hostname)
        .limit(limit)
    ).all()

    return {
        "total": total,
        "summary": describe(normalised),
        "truncated": total > len(rows),
        "devices": [
            {
                "id": row.id,
                "hostname": row.hostname,
                "ip_address": row.ip_address,
                "device_type": row.device_type,
                "location": row.location,
                "transport": row.transport,
                "is_active": row.is_active,
            }
            for row in rows
        ],
    }


def describe(device_filter: Optional[Dict[str, Any]]) -> str:
    """
    A one-line description of what a filter selects

    Shown next to a job so its scope is readable without opening the editor.

    Args:
        device_filter: The job's filter

    Returns:
        Human-readable summary
    """
    if is_empty(device_filter):
        return "Every device that can be backed up"

    normalised = device_filter or {}
    parts = []

    if normalised.get("device_ids"):
        parts.append(f"{len(normalised['device_ids'])} named device(s)")
    if normalised.get("device_types"):
        parts.append(f"type in {', '.join(normalised['device_types'])}")
    if normalised.get("locations"):
        parts.append(f"location in {', '.join(normalised['locations'])}")
    if normalised.get("hostname_pattern"):
        parts.append(f"hostname matching '{normalised['hostname_pattern']}'")
    if normalised.get("tags"):
        pairs = ", ".join(f"{key}={value}" for key, value in normalised["tags"].items())
        parts.append(f"tagged {pairs}")
    if normalised.get("transports"):
        parts.append(f"reached over {', '.join(normalised['transports'])}")
    if normalised.get("exclude_device_ids"):
        parts.append(f"excluding {len(normalised['exclude_device_ids'])} device(s)")
    if normalised.get("include_inactive"):
        parts.append("including inactive devices")

    return "Devices where " + "; ".join(parts) if parts else "Every device"
