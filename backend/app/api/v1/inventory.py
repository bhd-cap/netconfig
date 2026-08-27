"""
Host inventory, OUI vendor data and connected-device reports
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_organization_id, require_permission
from app.core.database import get_db
from app.models.device import Device
from app.models.network import HostInventory, OuiVendor
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.services import oui as oui_service

logger = logging.getLogger(__name__)

router = APIRouter()

# The full IEEE registry is around 3 MB; Wireshark's manuf is smaller. 32 MB
# leaves plenty of headroom without letting an upload exhaust memory.
MAX_OUI_UPLOAD_BYTES = 32 * 1024 * 1024

# Columns the host list can be ordered by, same contract as the device list: a
# name from a query string is looked up here or refused, never interpolated
# into SQL. "switch" sorts on the joined device hostname, falling back to the
# name recorded on the row so a host whose switch has been deleted still sorts
# somewhere sensible rather than to one end.
HOST_SORTABLE_COLUMNS = {
    "mac_address": HostInventory.mac_address,
    "ip_address": HostInventory.ip_address,
    "vendor": HostInventory.vendor,
    "interface": HostInventory.interface,
    "vlan": HostInventory.vlan,
    "hostname": HostInventory.hostname,
    "discovered_hostname": HostInventory.discovered_hostname,
    "entry_type": HostInventory.entry_type,
    "first_seen": HostInventory.first_seen,
    "last_seen": HostInventory.last_seen,
    "is_active": HostInventory.is_active,
    "switch": func.coalesce(Device.hostname, HostInventory.device_hostname),
}


def _host_ordering(sort_by: str, sort_dir: str):
    """
    The ORDER BY for the host list, with a tiebreak that follows the direction

    Inventory ties constantly - a hundred hosts on one VLAN, or every row
    inactive - so a fixed tiebreak would make ascending and descending come
    back in the same order and the header look dead. MAC address is the
    tiebreak because it is the one column that is never null and never
    duplicated within a switch port.
    """
    column = HOST_SORTABLE_COLUMNS.get(sort_by, HostInventory.last_seen)
    descending = str(sort_dir).lower() == "desc"

    primary = column.desc().nullslast() if descending else column.asc().nullslast()

    if column is HostInventory.mac_address:
        return (primary,)

    tiebreak = (
        HostInventory.mac_address.desc() if descending else HostInventory.mac_address.asc()
    )
    return (primary, tiebreak)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class HostResponse(BaseModel):
    """One host seen on a switch port"""

    id: int
    # Null once the switch has been deleted: the row is kept as history and
    # device_hostname still says where the host was seen.
    device_id: Optional[int] = None
    device_hostname: Optional[str] = None
    interface: str
    mac_address: str
    vlan: Optional[int]
    entry_type: Optional[str]
    ip_address: Optional[str]
    # Entered by a person.
    hostname: Optional[str]
    # Announced by the host over LLDP or CDP on this port.
    discovered_hostname: Optional[str] = None
    discovered_via: Optional[str] = None
    discovered_platform: Optional[str] = None
    vendor: Optional[str]
    first_seen: datetime
    last_seen: datetime
    is_active: bool
    notes: Optional[str]

    class Config:
        from_attributes = True


class HostUpdate(BaseModel):
    """Editable fields on an inventory entry"""

    hostname: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class RefreshRequest(BaseModel):
    """Which devices to re-read"""

    device_ids: Optional[List[int]] = Field(
        None, description="Devices to sweep; every active device when omitted"
    )


class OuiImportRequest(BaseModel):
    """Where to import OUI data from"""

    source: str = Field(
        "system",
        pattern="^(system|bundled|ieee|url|file)$",
        description="system, bundled, ieee, url or file",
    )
    url: Optional[str] = None
    path: Optional[str] = None


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[HostResponse])
def list_hosts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    device_id: Optional[int] = Query(None),
    interface: Optional[str] = Query(None),
    vlan: Optional[int] = Query(None),
    vendor: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="MAC, IP or hostname"),
    active_only: bool = Query(True),
    seen_within_hours: Optional[int] = Query(None, ge=1),
    sort_by: str = Query("last_seen", description="Column to sort on"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: User = Depends(require_permission("inventory:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """List hosts seen on switch ports"""
    if sort_by not in HOST_SORTABLE_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot sort on '{sort_by}'. Sortable columns are: "
                f"{', '.join(sorted(HOST_SORTABLE_COLUMNS))}"
            ),
        )

    filters = [HostInventory.organization_id == organization_id]

    if device_id is not None:
        filters.append(HostInventory.device_id == device_id)
    if interface:
        filters.append(HostInventory.interface == interface)
    if vlan is not None:
        filters.append(HostInventory.vlan == vlan)
    if vendor:
        filters.append(HostInventory.vendor.ilike(f"%{vendor}%"))
    if active_only:
        filters.append(HostInventory.is_active.is_(True))
    if seen_within_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=seen_within_hours)
        filters.append(HostInventory.last_seen >= cutoff)
    if search:
        pattern = f"%{search}%"
        filters.append(
            HostInventory.mac_address.ilike(pattern)
            | HostInventory.ip_address.ilike(pattern)
            | HostInventory.hostname.ilike(pattern)
            | HostInventory.discovered_hostname.ilike(pattern)
        )

    total = db.scalar(
        select(func.count(HostInventory.id)).where(*filters)
    ) or 0

    # An outer join: a host whose switch has been deleted is still inventory,
    # and an inner join would silently drop exactly the history this table
    # exists to keep.
    rows = db.execute(
        select(HostInventory, Device.hostname)
        .outerjoin(Device, HostInventory.device_id == Device.id)
        .where(*filters)
        .order_by(*_host_ordering(sort_by, sort_dir))
        .offset(skip)
        .limit(limit)
    ).all()

    items = [
        HostResponse.model_validate(host).model_copy(
            # Fall back to the name stored on the row when the device is gone.
            update={"device_hostname": device_hostname or host.device_hostname}
        )
        for host, device_hostname in rows
    ]

    return {
        "total": total,
        "page": (skip // limit) + 1,
        "page_size": limit,
        "total_pages": (total + limit - 1) // limit,
        "items": items,
    }


@router.patch("/{host_id}", response_model=HostResponse)
def update_host(
    host_id: int,
    payload: HostUpdate,
    current_user: User = Depends(require_permission("inventory:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Annotate an inventory entry"""
    host = db.execute(
        select(HostInventory).where(
            HostInventory.id == host_id,
            HostInventory.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not host:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inventory entry not found"
        )

    if payload.hostname is not None:
        host.hostname = payload.hostname
    if payload.notes is not None:
        host.notes = payload.notes

    db.commit()
    return HostResponse.model_validate(host)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_inventory(
    payload: RefreshRequest = RefreshRequest(),
    current_user: User = Depends(require_permission("inventory:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Re-read MAC tables and ARP without walking the topology"""
    from app.tasks.discovery import refresh_inventory_task

    task = refresh_inventory_task.delay(
        organization_id=organization_id, device_ids=payload.device_ids
    )

    return {"queued": True, "task_id": task.id}


# --------------------------------------------------------------------------
# OUI vendor data
# --------------------------------------------------------------------------


@router.get("/oui/status")
def oui_status(
    current_user: User = Depends(require_permission("inventory:read")),
    db: Session = Depends(get_db),
):
    """How much OUI data is loaded, and where more can come from"""
    count = db.scalar(select(func.count(OuiVendor.oui))) or 0

    return {
        "prefixes": count,
        "system_file": oui_service.find_system_oui_file(),
        "ieee_url": oui_service.IEEE_OUI_CSV_URL,
        # Every URL an 'ieee' import will try, in order, so the UI can explain
        # what a failure actually attempted.
        "ieee_sources": list(oui_service.IEEE_OUI_SOURCES),
        "sources": ["system", "bundled", "ieee", "url", "file", "upload"],
        "note": (
            "The bundled list is a small starter set. Import the IEEE registry "
            "or a local OUI database for full vendor coverage. With no outbound "
            "internet access, upload oui.csv or Wireshark's manuf instead."
        ),
    }


@router.post("/oui/import")
def import_oui(
    request: OuiImportRequest,
    current_user: User = Depends(require_permission("settings:write")),
    db: Session = Depends(get_db),
):
    """
    Import OUI vendor data

    Sources: an OUI database already on this host, the small bundled set, the
    IEEE registry, an arbitrary URL, or a local file. Format is detected, so
    the IEEE CSV, the IEEE oui.txt, Wireshark's manuf and nmap's prefix file
    all work.
    """
    try:
        if request.source == "system":
            written = oui_service.import_from_system(db)
        elif request.source == "bundled":
            written = oui_service.import_bundled(db)
        elif request.source == "ieee":
            written = oui_service.import_from_ieee(db)
        elif request.source == "url":
            if not request.url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A url is required when source is 'url'",
                )
            written = oui_service.import_from_url(db, request.url)
        else:
            if not request.path:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A path is required when source is 'file'",
                )
            written = oui_service.import_from_file(db, request.path)

    except HTTPException:
        raise
    except RuntimeError as e:
        # A source that could not be fetched or parsed. The message names what
        # was tried, so it is worth returning verbatim.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read the OUI source: {e}",
        )
    except Exception as e:  # noqa: BLE001
        # Anything else - a database error mid-import, an unexpected format
        # crash - used to surface as a bare 500 with nothing to go on. Log the
        # traceback and return something an operator can act on.
        logger.exception(f"OUI import from '{request.source}' failed")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"The OUI import failed while writing: {type(e).__name__}: {e}. "
                f"The full traceback is in the API log "
                f"(journalctl -u netconfig-api)."
            ),
        )

    total = db.scalar(select(func.count(OuiVendor.oui))) or 0

    return {
        "success": True,
        "imported": written,
        "total_prefixes": total,
        "message": f"Imported {written} prefixes ({total} held in total)",
    }


@router.post("/oui/upload")
async def upload_oui(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("settings:write")),
    db: Session = Depends(get_db),
):
    """
    Import an OUI list from an uploaded file

    The way to populate vendor data on an install with no outbound internet
    access: download the registry somewhere that has it and upload it here. A
    browser cannot hand the server a local path, which is what made the
    'file' source unusable from the UI.

    Accepts the IEEE oui.csv or oui.txt, Wireshark's manuf, or nmap's
    nmap-mac-prefixes; the format is detected.
    """
    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty"
        )

    if len(content) > MAX_OUI_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"The uploaded file is {len(content) // 1_000_000} MB; the limit "
                f"is {MAX_OUI_UPLOAD_BYTES // 1_000_000} MB. The full IEEE "
                f"registry is around 3 MB."
            ),
        )

    try:
        written = oui_service.import_from_bytes(
            db, content, filename=file.filename or "upload"
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"OUI upload of '{file.filename}' failed")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The upload failed while writing: {type(e).__name__}: {e}",
        )

    total = db.scalar(select(func.count(OuiVendor.oui))) or 0

    return {
        "success": True,
        "imported": written,
        "total_prefixes": total,
        "message": f"Imported {written} prefixes from '{file.filename}' "
        f"({total} held in total)",
    }


@router.post("/oui/backfill")
def backfill_vendors(
    current_user: User = Depends(require_permission("inventory:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Re-resolve vendors for inventory rows that have none

    Run after importing more OUI data, so existing rows benefit without
    waiting for the hosts to be seen again.
    """
    oui_service.ensure_populated(db)
    oui_service.oui_lookup.load(db, force=True)

    rows = db.execute(
        select(HostInventory.id, HostInventory.mac_address).where(
            HostInventory.organization_id == organization_id,
            HostInventory.vendor.is_(None),
        )
    ).all()

    updated = 0
    for row in rows:
        vendor = oui_service.oui_lookup.lookup(row.mac_address)
        if vendor:
            db.execute(
                HostInventory.__table__.update()
                .where(HostInventory.id == row.id)
                .values(vendor=vendor)
            )
            updated += 1

    db.commit()

    return {
        "success": True,
        "examined": len(rows),
        "updated": updated,
        "message": f"Resolved {updated} of {len(rows)} unknown vendors",
    }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@router.get("/reports/summary")
def inventory_summary(
    current_user: User = Depends(require_permission("reports:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Headline counts for the inventory"""
    now = datetime.now(timezone.utc)

    row = db.execute(
        select(
            func.count(HostInventory.id).label("total"),
            func.count(HostInventory.id).filter(
                HostInventory.is_active.is_(True)
            ).label("active"),
            func.count(func.distinct(HostInventory.mac_address)).label("unique_macs"),
            func.count(func.distinct(HostInventory.device_id)).label("switches"),
            func.count(HostInventory.id).filter(
                HostInventory.last_seen >= now - timedelta(hours=24)
            ).label("seen_24h"),
            func.count(HostInventory.id).filter(
                HostInventory.first_seen >= now - timedelta(hours=24)
            ).label("new_24h"),
            func.count(HostInventory.id).filter(
                HostInventory.ip_address.isnot(None)
            ).label("with_ip"),
            func.count(HostInventory.id).filter(
                HostInventory.vendor.is_(None)
            ).label("unknown_vendor"),
        ).where(HostInventory.organization_id == organization_id)
    ).one()

    return {
        "total_entries": row.total,
        "active_entries": row.active,
        "unique_macs": row.unique_macs,
        "switches_reporting": row.switches,
        "seen_last_24h": row.seen_24h,
        "new_last_24h": row.new_24h,
        "with_ip_address": row.with_ip,
        "unknown_vendor": row.unknown_vendor,
    }


@router.get("/reports/by-vendor")
def report_by_vendor(
    limit: int = Query(25, ge=1, le=200),
    active_only: bool = Query(True),
    current_user: User = Depends(require_permission("reports:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """How many hosts each vendor accounts for"""
    filters = [HostInventory.organization_id == organization_id]
    if active_only:
        filters.append(HostInventory.is_active.is_(True))

    rows = db.execute(
        select(
            func.coalesce(HostInventory.vendor, "Unknown").label("vendor"),
            func.count(func.distinct(HostInventory.mac_address)).label("hosts"),
        )
        .where(*filters)
        .group_by(func.coalesce(HostInventory.vendor, "Unknown"))
        .order_by(func.count(func.distinct(HostInventory.mac_address)).desc())
        .limit(limit)
    ).all()

    return {
        "vendors": [{"vendor": row.vendor, "hosts": row.hosts} for row in rows],
        "total_vendors": len(rows),
    }


@router.get("/reports/by-port")
def report_by_port(
    device_id: Optional[int] = Query(None),
    min_hosts: int = Query(1, ge=1),
    active_only: bool = Query(True),
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(require_permission("reports:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    What is connected to each switch port

    A port with many MACs is usually an uplink or a downstream unmanaged
    switch, which is what min_hosts is for.
    """
    filters = [HostInventory.organization_id == organization_id]
    if device_id is not None:
        filters.append(HostInventory.device_id == device_id)
    if active_only:
        filters.append(HostInventory.is_active.is_(True))

    rows = db.execute(
        select(
            HostInventory.device_id,
            Device.hostname,
            HostInventory.interface,
            func.count(func.distinct(HostInventory.mac_address)).label("hosts"),
            func.max(HostInventory.last_seen).label("last_seen"),
            func.min(HostInventory.first_seen).label("first_seen"),
        )
        .join(Device, HostInventory.device_id == Device.id)
        .where(*filters)
        .group_by(HostInventory.device_id, Device.hostname, HostInventory.interface)
        .having(func.count(func.distinct(HostInventory.mac_address)) >= min_hosts)
        .order_by(func.count(func.distinct(HostInventory.mac_address)).desc())
        .limit(limit)
    ).all()

    return {
        "ports": [
            {
                "device_id": row.device_id,
                "device_hostname": row.hostname,
                "interface": row.interface,
                "hosts": row.hosts,
                "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                "last_seen": row.last_seen.isoformat() if row.last_seen else None,
                "likely_uplink": row.hosts > 5,
            }
            for row in rows
        ],
        "total_ports": len(rows),
    }


@router.get("/reports/changes")
def report_changes(
    days: int = Query(7, ge=1, le=365),
    current_user: User = Depends(require_permission("reports:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Hosts that appeared or disappeared over a window

    The question this answers is "what changed on the network last week",
    which is why inventory rows are aged rather than deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    appeared = db.execute(
        select(
            HostInventory.mac_address,
            HostInventory.vendor,
            HostInventory.ip_address,
            HostInventory.interface,
            Device.hostname,
            HostInventory.first_seen,
        )
        .join(Device, HostInventory.device_id == Device.id)
        .where(
            HostInventory.organization_id == organization_id,
            HostInventory.first_seen >= cutoff,
        )
        .order_by(HostInventory.first_seen.desc())
        .limit(500)
    ).all()

    disappeared = db.execute(
        select(
            HostInventory.mac_address,
            HostInventory.vendor,
            HostInventory.ip_address,
            HostInventory.interface,
            Device.hostname,
            HostInventory.last_seen,
        )
        .join(Device, HostInventory.device_id == Device.id)
        .where(
            HostInventory.organization_id == organization_id,
            HostInventory.is_active.is_(False),
            HostInventory.last_seen >= cutoff,
        )
        .order_by(HostInventory.last_seen.desc())
        .limit(500)
    ).all()

    return {
        "period_days": days,
        "appeared": [
            {
                "mac_address": row.mac_address,
                "vendor": row.vendor,
                "ip_address": row.ip_address,
                "device_hostname": row.hostname,
                "interface": row.interface,
                "first_seen": row.first_seen.isoformat(),
            }
            for row in appeared
        ],
        "disappeared": [
            {
                "mac_address": row.mac_address,
                "vendor": row.vendor,
                "ip_address": row.ip_address,
                "device_hostname": row.hostname,
                "interface": row.interface,
                "last_seen": row.last_seen.isoformat(),
            }
            for row in disappeared
        ],
        "appeared_count": len(appeared),
        "disappeared_count": len(disappeared),
    }


@router.get("/reports/export")
def export_inventory(
    active_only: bool = Query(True),
    device_id: Optional[int] = Query(None),
    current_user: User = Depends(require_permission("reports:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Export the inventory as CSV"""
    filters = [HostInventory.organization_id == organization_id]
    if active_only:
        filters.append(HostInventory.is_active.is_(True))
    if device_id is not None:
        filters.append(HostInventory.device_id == device_id)

    rows = db.execute(
        select(
            func.coalesce(Device.hostname, HostInventory.device_hostname).label("switch"),
            HostInventory.interface,
            HostInventory.vlan,
            HostInventory.mac_address,
            HostInventory.vendor,
            HostInventory.ip_address,
            # Both tables have a 'hostname'; label the host's so neither is
            # shadowed when the row is read by name.
            HostInventory.hostname.label("host_name"),
            HostInventory.discovered_hostname,
            HostInventory.discovered_via,
            HostInventory.entry_type,
            HostInventory.first_seen,
            HostInventory.last_seen,
            HostInventory.is_active,
            HostInventory.notes,
        )
        # Outer, so a host whose switch was deleted still exports.
        .outerjoin(Device, HostInventory.device_id == Device.id)
        .where(*filters)
        .order_by(Device.hostname, HostInventory.interface, HostInventory.mac_address)
        .limit(50000)
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "switch", "interface", "vlan", "mac_address", "vendor", "ip_address",
            "host_name", "discovered_hostname", "discovered_via", "entry_type",
            "first_seen", "last_seen", "active", "notes",
        ]
    )

    for row in rows:
        writer.writerow(
            [
                row.switch,
                row.interface,
                row.vlan or "",
                row.mac_address,
                row.vendor or "",
                row.ip_address or "",
                row.host_name or "",
                row.discovered_hostname or "",
                row.discovered_via or "",
                row.entry_type or "",
                row.first_seen.isoformat() if row.first_seen else "",
                row.last_seen.isoformat() if row.last_seen else "",
                "yes" if row.is_active else "no",
                row.notes or "",
            ]
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=inventory_{stamp}.csv"
        },
    )
