"""
Device API endpoints with multi-tenant support
"""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.api.deps import get_current_user, get_organization_id
from app.models.user import User
from app.repositories.device import DeviceRepository, SORTABLE_COLUMNS
from app.repositories.audit_log import AuditLogRepository
from app.schemas.device import (
    DeviceResponse,
    DeviceCreate,
    DeviceUpdate,
    DeviceWithBackupCount,
    DeviceTestConnection,
)
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.services.device_connector import DeviceConnector, snmp_params
from app.utils.encryption import encryption_service
from app.utils.csv_parser import parse_device_csv, generate_csv_template, CSVParseError

logger = logging.getLogger(__name__)

router = APIRouter()


class BulkDeviceIds(BaseModel):
    """A selection of devices to act on"""

    device_ids: List[int] = Field(..., min_length=1, max_length=1000)


class BulkDeviceUpdate(BulkDeviceIds):
    """
    Fields to set on every selected device

    Credentials are absent on purpose: pushing one password across a selection
    is how a whole rack ends up locked out, and the credential vault exists
    for sharing logins.
    """

    is_active: Optional[bool] = Field(
        None, description="False removes them from the backup list"
    )
    device_type: Optional[str] = None
    transport: Optional[str] = Field(None, pattern="^(ssh|telnet|snmp)$")
    port: Optional[int] = Field(None, ge=1, le=65535)
    location: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None

# SNMP secrets are handled like the login password: encrypted on the way in,
# never returned, and left alone when an update omits them.
SNMP_SECRETS = {"snmp_community", "snmp_v3_auth_key", "snmp_v3_priv_key"}


def _encrypted_snmp_secrets(device_in) -> dict:
    """
    Encrypt whichever SNMP secrets were supplied

    Args:
        device_in: A DeviceCreate or DeviceUpdate

    Returns:
        dict of column name to ciphertext, for the secrets that were set
    """
    return {
        field: encryption_service.encrypt(getattr(device_in, field))
        for field in SNMP_SECRETS
        if getattr(device_in, field, None)
    }


@router.get("", response_model=PaginatedResponse[DeviceResponse])
def list_devices(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    device_type: str = Query(None),
    is_active: bool = Query(None),
    search: str = Query(None),
    sort_by: str = Query("hostname", description="Column to sort on"),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    List devices in organization with pagination, filtering and sorting

    Args:
        skip: Number of records to skip
        limit: Maximum number of records
        device_type: Filter by device type
        is_active: Filter by active status
        search: Search in hostname or IP
        sort_by: Column to sort on; see /devices/sortable
        sort_dir: 'asc' or 'desc'
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        PaginatedResponse: Paginated list of devices
    """
    if sort_by not in SORTABLE_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot sort on '{sort_by}'. Sortable columns are: "
                f"{', '.join(sorted(SORTABLE_COLUMNS))}"
            ),
        )

    device_repo = DeviceRepository(db)

    devices = device_repo.get_by_organization(
        organization_id=organization_id,
        skip=skip,
        limit=limit,
        device_type=device_type,
        is_active=is_active,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )

    total = device_repo.count_by_organization(
        organization_id=organization_id,
        device_type=device_type,
        is_active=is_active,
    )

    return {
        "total": total,
        "page": (skip // limit) + 1,
        "page_size": limit,
        "total_pages": (total + limit - 1) // limit,
        "items": devices,
    }


@router.get("/sortable")
def sortable_columns(
    organization_id: int = Depends(get_organization_id),
):
    """
    The columns the device list can be sorted on

    Declared before /{device_id} so the literal path is matched first.
    """
    return {"columns": sorted(SORTABLE_COLUMNS)}


@router.patch("/bulk", response_model=dict)
def bulk_update_devices(
    payload: BulkDeviceUpdate,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Change the same fields on many devices at once

    Only the fields supplied are written. Credentials are deliberately not
    settable here: pushing one password onto a selection is how a whole rack
    ends up unable to authenticate, and there is a credential vault for
    sharing logins.
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    changes = payload.model_dump(exclude_unset=True, exclude={"device_ids"})
    changes = {key: value for key, value in changes.items() if value is not None}

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supply at least one field to change",
        )

    devices = [
        device
        for device in device_repo.get_many(payload.device_ids)
        if device.organization_id == organization_id
    ]

    found = {device.id for device in devices}
    missing = [did for did in payload.device_ids if did not in found]

    for device in devices:
        for field, value in changes.items():
            setattr(device, field, value)

    db.commit()

    audit_repo.log_action(
        user_id=current_user.id,
        action="devices_bulk_updated",
        resource_type="device",
        details={
            "count": len(devices),
            "fields": sorted(changes),
            "device_ids": sorted(found),
        },
    )

    return {
        "success": True,
        "updated": len(devices),
        "not_found": missing,
        "fields": sorted(changes),
        "message": f"Updated {len(devices)} device(s)",
    }


@router.post("/bulk-delete", response_model=dict)
def bulk_delete_devices(
    payload: BulkDeviceIds,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Remove many devices from the backup list

    The inventory and adjacency history survive: those rows record what was
    plugged into a port and what a switch was cabled to, and deleting the
    switch is not a statement about either. Their device_id is nulled and the
    hostname they were seen on is already stored on the row.

    Stored configuration files are removed with the device, because they are
    the device's own data.
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    devices = [
        device
        for device in device_repo.get_many(payload.device_ids)
        if device.organization_id == organization_id
    ]

    found = {device.id for device in devices}
    missing = [did for did in payload.device_ids if did not in found]
    names = sorted(device.hostname for device in devices)

    for device in devices:
        db.delete(device)

    db.commit()

    audit_repo.log_action(
        user_id=current_user.id,
        action="devices_bulk_deleted",
        resource_type="device",
        details={"count": len(devices), "hostnames": names[:50]},
    )

    return {
        "success": True,
        "deleted": len(devices),
        "not_found": missing,
        "message": (
            f"Deleted {len(devices)} device(s). Their inventory and adjacency "
            f"history has been kept."
        ),
    }


@router.get("/{device_id}/detail")
def get_device_detail(
    device_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Everything known about one device

    What the probe learned, the outcome of each transport tried, the
    credential that worked, its neighbours and how many hosts have been seen
    on its ports. This is what the device name links to from both the Devices
    page and the Inventory page.
    """
    from sqlalchemy import func, select as sa_select

    from app.models.credential import Credential, DeviceProbe
    from app.models.network import HostInventory, Neighbor

    device_repo = DeviceRepository(db)
    device = device_repo.get_by_id_and_organization(device_id, organization_id)

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
        )

    probes = list(
        db.execute(
            sa_select(DeviceProbe)
            .where(DeviceProbe.device_id == device_id)
            .order_by(DeviceProbe.transport)
        ).scalars()
    )

    neighbors = list(
        db.execute(
            sa_select(
                Neighbor.local_interface,
                Neighbor.remote_hostname,
                Neighbor.remote_interface,
                Neighbor.remote_platform,
                Neighbor.remote_mgmt_ip,
                Neighbor.remote_device_id,
                Neighbor.protocol,
                Neighbor.is_active,
                Neighbor.last_seen,
            )
            .where(Neighbor.device_id == device_id)
            .order_by(Neighbor.local_interface)
            .limit(500)
        ).all()
    )

    host_counts = db.execute(
        sa_select(
            func.count(HostInventory.id).label("total"),
            func.count(HostInventory.id)
            .filter(HostInventory.is_active.is_(True))
            .label("active"),
            func.count(func.distinct(HostInventory.interface)).label("ports"),
        ).where(HostInventory.device_id == device_id)
    ).one()

    credential_name = None
    if device.credential_id:
        credential_name = db.scalar(
            sa_select(Credential.name).where(Credential.id == device.credential_id)
        )

    return {
        "device": {
            "id": device.id,
            "hostname": device.hostname,
            "ip_address": device.ip_address,
            "device_type": device.device_type,
            "transport": device.transport,
            "port": device.port,
            "username": device.username,
            "location": device.location,
            "description": device.description,
            "tags": device.tags,
            "is_active": device.is_active,
            "discovered": device.discovered,
            "discovery_source": device.discovery_source,
            "last_discovered_at": device.last_discovered_at,
            "last_backup_at": device.last_backup_at,
            "last_backup_status": device.last_backup_status,
            "created_at": device.created_at,
        },
        "authentication": {
            "status": device.last_auth_status,
            "at": device.last_auth_at,
            "error": device.auth_error,
            "credential_id": device.credential_id,
            "credential_name": credential_name,
            "backup_eligible": device.is_active
            and device.last_auth_status == "success",
        },
        # What the device said about itself. SNMP is the usual source; a CLI
        # version command fills in what SNMP did not answer.
        "facts": {
            "model": device.model,
            "serial_number": device.serial_number,
            "os_version": device.os_version,
            "snmp_sysname": device.snmp_sysname,
            "snmp_sysdescr": device.snmp_sysdescr,
            "snmp_location": device.snmp_location,
            "snmp_contact": device.snmp_contact,
            "snmp_uptime_seconds": device.snmp_uptime_seconds,
            "snmp_last_polled_at": device.snmp_last_polled_at,
            "extra": device.discovered_facts or {},
        },
        "probes": [
            {
                "transport": row.transport,
                "result": row.result,
                "credential_name": row.credential_name,
                "attempts": row.attempts,
                "message": row.message,
                "duration_ms": row.duration,
                "probed_at": row.probed_at,
            }
            for row in probes
        ],
        "neighbors": [
            {
                "local_interface": row.local_interface,
                "remote_hostname": row.remote_hostname,
                "remote_interface": row.remote_interface or None,
                "remote_platform": row.remote_platform,
                "remote_mgmt_ip": row.remote_mgmt_ip,
                "remote_device_id": row.remote_device_id,
                "protocol": row.protocol,
                "is_active": row.is_active,
                "last_seen": row.last_seen,
            }
            for row in neighbors
        ],
        "hosts": {
            "total": host_counts.total,
            "active": host_counts.active,
            "ports_in_use": host_counts.ports,
        },
    }


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(
    device_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get device by ID (tenant-scoped)

    Args:
        device_id: Device ID
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        DeviceResponse: Device details

    Raises:
        HTTPException: If device not found or access denied
    """
    device_repo = DeviceRepository(db)
    device = device_repo.get_by_id_and_organization(device_id, organization_id)

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    return device


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def create_device(
    device_in: DeviceCreate,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new device

    Args:
        device_in: Device creation data
        organization_id: Organization ID (from token)
        current_user: Current user
        db: Database session

    Returns:
        DeviceResponse: Created device

    Raises:
        HTTPException: If hostname or IP already exists
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Check if hostname already exists in organization
    existing_device = device_repo.get_by_hostname(device_in.hostname, organization_id)
    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Device with hostname '{device_in.hostname}' already exists",
        )

    # Check if IP already exists in organization
    existing_device = device_repo.get_by_ip(device_in.ip_address, organization_id)
    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Device with IP '{device_in.ip_address}' already exists",
        )

    # Encrypt credentials
    encrypted_password = encryption_service.encrypt(device_in.password)
    encrypted_enable_secret = None
    if device_in.enable_secret:
        encrypted_enable_secret = encryption_service.encrypt(device_in.enable_secret)

    # Prepare device data
    device_data = device_in.dict(exclude={"password", "enable_secret"} | SNMP_SECRETS)
    device_data.update({
        "organization_id": organization_id,
        "encrypted_password": encrypted_password,
        "enable_secret": encrypted_enable_secret,
        "created_by": current_user.id,
    })
    device_data.update(_encrypted_snmp_secrets(device_in))

    # Create device
    device = device_repo.create(device_data)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="device_created",
        resource_type="device",
        resource_id=device.id,
        details={
            "hostname": device.hostname,
            "ip_address": device.ip_address,
            "device_type": device.device_type,
        },
    )

    return device


@router.put("/{device_id}", response_model=DeviceResponse)
def update_device(
    device_id: int,
    device_in: DeviceUpdate,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update a device

    Args:
        device_id: Device ID
        device_in: Device update data
        organization_id: Organization ID (from token)
        current_user: Current user
        db: Database session

    Returns:
        DeviceResponse: Updated device

    Raises:
        HTTPException: If device not found or access denied
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Get existing device
    device = device_repo.get_by_id_and_organization(device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    # Check hostname uniqueness if being updated
    if device_in.hostname and device_in.hostname != device.hostname:
        existing = device_repo.get_by_hostname(device_in.hostname, organization_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Device with hostname '{device_in.hostname}' already exists",
            )

    # Check IP uniqueness if being updated
    if device_in.ip_address and device_in.ip_address != device.ip_address:
        existing = device_repo.get_by_ip(device_in.ip_address, organization_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Device with IP '{device_in.ip_address}' already exists",
            )

    # Prepare update data
    update_data = device_in.dict(
        exclude_unset=True, exclude={"password", "enable_secret"} | SNMP_SECRETS
    )

    # Encrypt new password if provided
    if device_in.password:
        update_data["encrypted_password"] = encryption_service.encrypt(device_in.password)

    # Encrypt new enable secret if provided
    if device_in.enable_secret:
        update_data["enable_secret"] = encryption_service.encrypt(device_in.enable_secret)

    # Same for the SNMP secrets: omitting one leaves the stored value alone.
    update_data.update(_encrypted_snmp_secrets(device_in))

    # Update device
    device = device_repo.update(device, update_data)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="device_updated",
        resource_type="device",
        resource_id=device.id,
        details={"hostname": device.hostname, "updated_fields": list(update_data.keys())},
    )

    return device


@router.delete("/{device_id}", response_model=SuccessResponse)
def delete_device(
    device_id: int,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a device

    Args:
        device_id: Device ID
        organization_id: Organization ID (from token)
        current_user: Current user
        db: Database session

    Returns:
        SuccessResponse: Deletion confirmation

    Raises:
        HTTPException: If device not found or access denied
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Get existing device
    device = device_repo.get_by_id_and_organization(device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    hostname = device.hostname

    # Delete device (cascade will delete related configurations)
    device_repo.delete(device_id)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="device_deleted",
        resource_type="device",
        resource_id=device_id,
        details={"hostname": hostname},
    )

    return {
        "success": True,
        "message": f"Device '{hostname}' deleted successfully",
    }


@router.post("/{device_id}/test", response_model=DeviceTestConnection)
def test_device_connection(
    device_id: int,
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Test connectivity to a device

    Args:
        device_id: Device ID
        organization_id: Organization ID (from token)
        current_user: Current user
        db: Database session

    Returns:
        DeviceTestConnection: Connection test results

    Raises:
        HTTPException: If device not found
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Get device
    device = device_repo.get_by_id_and_organization(device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )

    # Test connection over whichever transport the device is configured for,
    # so a telnet or SNMP device is not tested as if it were SSH.
    try:
        transport = device.transport or "ssh"

        connector = DeviceConnector(
            hostname=device.hostname,
            ip_address=device.ip_address,
            device_type=device.device_type,
            username=device.username,
            encrypted_password=device.encrypted_password,
            port=device.port,
            enable_secret=device.enable_secret,
            ssh_key_path=device.ssh_key_path,
            transport=transport,
            snmp=snmp_params(device) if transport == "snmp" else None,
        )

        result = connector.test_connection()

        # Log test
        audit_repo.log_action(
            user_id=current_user.id,
            action="device_test_connection",
            resource_type="device",
            resource_id=device.id,
            details={"hostname": device.hostname, "result": result["success"]},
            status="success" if result["success"] else "failed",
        )

        return result

    except Exception as e:
        # Log failure
        audit_repo.log_action(
            user_id=current_user.id,
            action="device_test_connection",
            resource_type="device",
            resource_id=device.id,
            details={"hostname": device.hostname},
            status="failed",
            error_message=str(e),
        )

        return {
            "success": False,
            "message": str(e),
            "response_time": None,
            "device_info": {},
        }


@router.post("/bulk-upload")
def bulk_upload_devices(
    file: UploadFile = File(...),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Bulk upload devices from CSV file

    Args:
        file: CSV file upload
        organization_id: Organization ID (from token)
        current_user: Current user
        db: Database session

    Returns:
        dict: Upload results with success/error counts

    Raises:
        HTTPException: If file format is invalid
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported",
        )

    try:
        # Read CSV content, bounded so an oversized upload cannot be pulled
        # into memory in full before being rejected.
        content_bytes = file.file.read(settings.MAX_UPLOAD_BYTES + 1)

        if len(content_bytes) > settings.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"CSV file exceeds the "
                    f"{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit"
                ),
            )

        content = content_bytes.decode("utf-8")
        del content_bytes

        # Parse CSV
        devices, parse_errors = parse_device_csv(content)
        del content

        # Process valid devices
        created_devices = []
        creation_errors = []

        # Resolve every duplicate in two queries rather than two per row, and
        # track candidates seen in this file so a CSV that repeats a hostname
        # is caught too.
        taken_hostnames, taken_ips = device_repo.get_existing_hostnames_and_ips(
            organization_id,
            [d.hostname for d in devices],
            [d.ip_address for d in devices],
        )

        pending_rows = []

        for device_in in devices:
            if device_in.hostname in taken_hostnames:
                creation_errors.append({
                    "hostname": device_in.hostname,
                    "error": "Hostname already exists",
                })
                continue

            if device_in.ip_address in taken_ips:
                creation_errors.append({
                    "hostname": device_in.hostname,
                    "error": f"IP {device_in.ip_address} already exists",
                })
                continue

            taken_hostnames.add(device_in.hostname)
            taken_ips.add(device_in.ip_address)

            # Encrypt credentials
            encrypted_password = encryption_service.encrypt(device_in.password)
            encrypted_enable_secret = None
            if device_in.enable_secret:
                encrypted_enable_secret = encryption_service.encrypt(
                    device_in.enable_secret
                )

            device_data = device_in.dict(
                exclude={"password", "enable_secret"} | SNMP_SECRETS
            )
            device_data.update({
                "organization_id": organization_id,
                "encrypted_password": encrypted_password,
                "enable_secret": encrypted_enable_secret,
                "created_by": current_user.id,
            })
            device_data.update(_encrypted_snmp_secrets(device_in))
            pending_rows.append(device_data)

        if pending_rows:
            try:
                # One multi-row INSERT and one commit for the whole file,
                # instead of a commit and a refresh per device.
                created_devices = device_repo.create_many(pending_rows)
            except Exception as e:
                db.rollback()
                logger.exception("Bulk device insert failed")
                creation_errors.append({
                    "hostname": None,
                    "error": f"Bulk insert failed: {e}",
                })

        # Log bulk upload
        audit_repo.log_action(
            user_id=current_user.id,
            action="devices_bulk_upload",
            resource_type="device",
            details={
                "total_in_file": len(devices) + len(parse_errors),
                "created": len(created_devices),
                "parse_errors": len(parse_errors),
                "creation_errors": len(creation_errors),
            },
        )

        return {
            "success": True,
            "message": f"Bulk upload completed: {len(created_devices)} devices created",
            "total_in_file": len(devices) + len(parse_errors),
            "devices_created": len(created_devices),
            "parse_errors_count": len(parse_errors),
            "creation_errors_count": len(creation_errors),
            "parse_errors": parse_errors[:10],  # Limit to first 10 errors
            "creation_errors": creation_errors[:10],
            "created_devices": [{"id": d.id, "hostname": d.hostname} for d in created_devices],
        }

    except HTTPException:
        raise

    except CSVParseError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        logger.exception("Failed to process device CSV upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process CSV: {str(e)}",
        )


@router.get("/bulk-upload/template")
def download_csv_template():
    """
    Download CSV template for bulk device upload

    Returns:
        Response: CSV file download
    """
    template = generate_csv_template()

    return Response(
        content=template,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=device_upload_template.csv"
        },
    )
