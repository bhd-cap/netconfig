"""
Application settings and remote backup target endpoints

Settings are per-organization and created on first read, so a fresh tenant
gets sensible defaults rather than a 404.

Secrets (the SMTP password, target passwords and private keys) are write-only:
they can be set and replaced, but no endpoint ever returns them. Reads return
a boolean saying whether one is stored.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_organization_id, require_permission
from app.core.database import get_db
from app.models.administration import AppSettings, BackupTarget
from app.models.user import User
from app.repositories.audit_log import AuditLogRepository
from app.services import app_settings, remote_backup
from app.services.app_settings import SettingsError
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class MaintenanceWindowSchema(BaseModel):
    """One recurring maintenance window, in the organization's timezone"""

    name: str = Field(..., min_length=1, max_length=100)
    days: List[int] = Field(
        ..., description="Weekdays the window starts on, Monday=0 to Sunday=6"
    )
    start: str = Field(..., description="Local start time, HH:MM")
    end: str = Field(..., description="Local end time, HH:MM; may wrap midnight")
    suppress_backups: bool = True
    suppress_notifications: bool = False


class RetentionSettings(BaseModel):
    """How long stored configurations are kept"""

    retention_days: int
    retention_max_per_device: Optional[int]
    retention_enabled: bool


class ScheduleSettings(BaseModel):
    """Defaults applied to newly created backup jobs"""

    default_schedule_cron: str
    default_schedule_enabled: bool
    max_concurrent_backups: int


class EmailSettings(BaseModel):
    """SMTP and which events are worth an email"""

    smtp_host: Optional[str]
    smtp_port: int
    smtp_username: Optional[str]
    smtp_password_set: bool
    smtp_use_tls: bool
    smtp_from_address: Optional[str]
    notifications_enabled: bool
    notify_recipients: List[str]
    notify_on_backup_failure: bool
    notify_on_backup_success: bool
    notify_on_config_change: bool
    notify_on_new_host: bool


class MaintenanceSettings(BaseModel):
    """Maintenance windows and the timezone they are expressed in"""

    maintenance_timezone: str
    maintenance_windows: List[MaintenanceWindowSchema]
    currently_open: List[str] = Field(
        default_factory=list, description="Names of the windows open right now"
    )
    backups_suppressed: bool = False
    notifications_suppressed: bool = False
    next_window_start: Optional[datetime] = None


class SettingsResponse(BaseModel):
    """Every settings group for one organization"""

    organization_id: int
    retention: RetentionSettings
    schedule: ScheduleSettings
    email: EmailSettings
    maintenance: MaintenanceSettings
    updated_at: Optional[datetime]


class SettingsUpdate(BaseModel):
    """
    Change any subset of the settings

    Every field is optional; only the ones supplied are written, so a client
    editing one group cannot clobber another.
    """

    retention_days: Optional[int] = Field(None, ge=1, le=3650)
    retention_max_per_device: Optional[int] = Field(None, ge=1)
    clear_retention_max: bool = False
    retention_enabled: Optional[bool] = None

    default_schedule_cron: Optional[str] = None
    default_schedule_enabled: Optional[bool] = None
    max_concurrent_backups: Optional[int] = Field(None, ge=1, le=100)

    smtp_host: Optional[str] = Field(None, max_length=255)
    smtp_port: Optional[int] = Field(None, ge=1, le=65535)
    smtp_username: Optional[str] = Field(None, max_length=255)
    smtp_password: Optional[str] = Field(
        None, description="Write-only; omit to leave the stored password alone"
    )
    clear_smtp_password: bool = False
    smtp_use_tls: Optional[bool] = None
    smtp_from_address: Optional[str] = Field(None, max_length=255)

    notifications_enabled: Optional[bool] = None
    notify_recipients: Optional[List[EmailStr]] = None
    notify_on_backup_failure: Optional[bool] = None
    notify_on_backup_success: Optional[bool] = None
    notify_on_config_change: Optional[bool] = None
    notify_on_new_host: Optional[bool] = None

    maintenance_timezone: Optional[str] = Field(None, max_length=64)
    maintenance_windows: Optional[List[MaintenanceWindowSchema]] = None


class TestEmailRequest(BaseModel):
    """Send a test message to one address"""

    recipient: EmailStr


class TargetResponse(BaseModel):
    """A remote backup target, without its secrets"""

    id: int
    name: str
    protocol: str
    host: str
    port: int
    username: str
    remote_path: str
    use_device_subdirectories: bool
    is_enabled: bool
    upload_on_backup: bool
    verify_host_key: bool
    has_password: bool
    has_private_key: bool
    last_status: Optional[str]
    last_run_at: Optional[datetime]
    last_error: Optional[str]
    uploads_succeeded: int
    uploads_failed: int
    created_at: datetime
    updated_at: Optional[datetime]


class TargetCreate(BaseModel):
    """Create a remote backup target"""

    name: str = Field(..., min_length=1, max_length=255)
    protocol: str = Field("sftp", pattern="^(sftp|ftp|ftps)$")
    host: str = Field(..., min_length=1, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=255)
    password: Optional[str] = None
    private_key: Optional[str] = Field(None, description="PEM private key, SFTP only")
    private_key_passphrase: Optional[str] = None
    remote_path: str = "/"
    use_device_subdirectories: bool = True
    is_enabled: bool = True
    upload_on_backup: bool = True
    verify_host_key: bool = False
    known_host_key: Optional[str] = None


class TargetUpdate(BaseModel):
    """Change a remote backup target"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    protocol: Optional[str] = Field(None, pattern="^(sftp|ftp|ftps)$")
    host: Optional[str] = Field(None, min_length=1, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = Field(None, min_length=1, max_length=255)
    password: Optional[str] = None
    clear_password: bool = False
    private_key: Optional[str] = None
    clear_private_key: bool = False
    private_key_passphrase: Optional[str] = None
    remote_path: Optional[str] = None
    use_device_subdirectories: Optional[bool] = None
    is_enabled: Optional[bool] = None
    upload_on_backup: Optional[bool] = None
    verify_host_key: Optional[bool] = None
    known_host_key: Optional[str] = None


class TargetUploadRequest(BaseModel):
    """Push stored configurations to a target"""

    configuration_ids: Optional[List[int]] = Field(
        None, description="Specific configurations; the latest per device when omitted"
    )
    device_ids: Optional[List[int]] = Field(
        None, description="Restrict to these devices"
    )
    limit: int = Field(200, ge=1, le=2000)
    run_async: bool = True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _client_ip(request: Request) -> Optional[str]:
    """Best-effort client address for the audit log"""
    return request.client.host if request.client else None


def _serialise_settings(settings: AppSettings) -> SettingsResponse:
    """Build the response, including the live maintenance-window state"""
    open_windows = app_settings.active_windows(settings)
    backups_off, _ = app_settings.backups_suppressed(settings)
    notifications_off, _ = app_settings.notifications_suppressed(settings)

    return SettingsResponse(
        organization_id=settings.organization_id,
        retention=RetentionSettings(
            retention_days=settings.retention_days,
            retention_max_per_device=settings.retention_max_per_device,
            retention_enabled=settings.retention_enabled,
        ),
        schedule=ScheduleSettings(
            default_schedule_cron=settings.default_schedule_cron,
            default_schedule_enabled=settings.default_schedule_enabled,
            max_concurrent_backups=settings.max_concurrent_backups,
        ),
        email=EmailSettings(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password_set=bool(settings.smtp_password_encrypted),
            smtp_use_tls=settings.smtp_use_tls,
            smtp_from_address=settings.smtp_from_address,
            notifications_enabled=settings.notifications_enabled,
            notify_recipients=list(settings.notify_recipients or []),
            notify_on_backup_failure=settings.notify_on_backup_failure,
            notify_on_backup_success=settings.notify_on_backup_success,
            notify_on_config_change=settings.notify_on_config_change,
            notify_on_new_host=settings.notify_on_new_host,
        ),
        maintenance=MaintenanceSettings(
            maintenance_timezone=settings.maintenance_timezone,
            maintenance_windows=[
                MaintenanceWindowSchema(**window)
                for window in (settings.maintenance_windows or [])
            ],
            currently_open=[window.name for window in open_windows],
            backups_suppressed=backups_off,
            notifications_suppressed=notifications_off,
            next_window_start=app_settings.next_window_start(settings),
        ),
        updated_at=settings.updated_at,
    )


def _serialise_target(target: BackupTarget) -> TargetResponse:
    """Build a target response with the secrets reduced to booleans"""
    return TargetResponse(
        id=target.id,
        name=target.name,
        protocol=target.protocol,
        host=target.host,
        port=target.port,
        username=target.username,
        remote_path=target.remote_path,
        use_device_subdirectories=target.use_device_subdirectories,
        is_enabled=target.is_enabled,
        upload_on_backup=target.upload_on_backup,
        verify_host_key=target.verify_host_key,
        has_password=bool(target.encrypted_password),
        has_private_key=bool(target.private_key),
        last_status=target.last_status,
        last_run_at=target.last_run_at,
        last_error=target.last_error,
        uploads_succeeded=target.uploads_succeeded or 0,
        uploads_failed=target.uploads_failed or 0,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _get_target_or_404(
    db: Session, target_id: int, organization_id: int
) -> BackupTarget:
    """Fetch a target inside the caller's organization or raise 404"""
    target = db.execute(
        select(BackupTarget).where(
            BackupTarget.id == target_id,
            BackupTarget.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Backup target not found"
        )

    return target


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@router.get("", response_model=SettingsResponse)
def read_settings(
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("settings:read")),
):
    """
    The organization's settings

    Created with defaults on first read, so this never 404s.
    """
    return _serialise_settings(app_settings.get_or_create(db, organization_id))


@router.put("", response_model=SettingsResponse)
def update_settings(
    payload: SettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("settings:write")),
):
    """
    Change any subset of the settings

    Values are validated before anything is written, so a bad cron expression
    or a malformed maintenance window leaves the stored settings untouched.
    """
    settings = app_settings.get_or_create(db, organization_id)

    try:
        if payload.retention_days is not None or payload.retention_max_per_device is not None:
            app_settings.validate_retention(
                payload.retention_days, payload.retention_max_per_device
            )

        if payload.default_schedule_cron is not None:
            app_settings.validate_cron(payload.default_schedule_cron)

        windows = None
        if payload.maintenance_windows is not None:
            windows = app_settings.validate_windows(
                [window.model_dump() for window in payload.maintenance_windows]
            )

    except SettingsError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        )

    if payload.maintenance_timezone is not None:
        # resolve_timezone falls back to UTC on an unknown name, which is the
        # right behaviour at read time but would silently discard a typo here.
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(payload.maintenance_timezone)
        except ImportError:
            pass
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown timezone '{payload.maintenance_timezone}'",
            )
        settings.maintenance_timezone = payload.maintenance_timezone

    simple_fields = (
        "retention_days",
        "retention_enabled",
        "default_schedule_cron",
        "default_schedule_enabled",
        "max_concurrent_backups",
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_use_tls",
        "smtp_from_address",
        "notifications_enabled",
        "notify_on_backup_failure",
        "notify_on_backup_success",
        "notify_on_config_change",
        "notify_on_new_host",
    )

    changed = []

    for field in simple_fields:
        value = getattr(payload, field)
        if value is not None:
            setattr(settings, field, value)
            changed.append(field)

    if payload.clear_retention_max:
        settings.retention_max_per_device = None
        changed.append("retention_max_per_device")
    elif payload.retention_max_per_device is not None:
        settings.retention_max_per_device = payload.retention_max_per_device
        changed.append("retention_max_per_device")

    if payload.notify_recipients is not None:
        settings.notify_recipients = [str(address) for address in payload.notify_recipients]
        changed.append("notify_recipients")

    if payload.clear_smtp_password:
        settings.smtp_password_encrypted = None
        changed.append("smtp_password")
    elif payload.smtp_password:
        settings.smtp_password_encrypted = encryption_service.encrypt(
            payload.smtp_password
        )
        changed.append("smtp_password")

    if windows is not None:
        settings.maintenance_windows = windows
        changed.append("maintenance_windows")

    if payload.maintenance_timezone is not None:
        changed.append("maintenance_timezone")

    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="update_settings",
        resource_type="settings",
        resource_id=settings.id,
        details={"fields": changed},
        ip_address=_client_ip(request),
    )

    return _serialise_settings(settings)


@router.post("/test-email")
def test_email(
    payload: TestEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("settings:write")),
):
    """
    Send a test message with the stored SMTP settings

    Returns what happened rather than raising, so the UI can show the mail
    server's own error text.
    """
    settings = app_settings.get_or_create(db, organization_id)
    result = app_settings.send_test_email(settings, str(payload.recipient))

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="test_email",
        resource_type="settings",
        resource_id=settings.id,
        details={"recipient": str(payload.recipient)},
        status="success" if result.get("success") else "failed",
        error_message=None if result.get("success") else result.get("message"),
        ip_address=_client_ip(request),
    )

    return result


@router.get("/maintenance/status")
def maintenance_status(
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("settings:read")),
):
    """
    Whether a maintenance window is open right now

    Scheduled backups consult the same helpers, so this is exactly what the
    scheduler sees.
    """
    settings = app_settings.get_or_create(db, organization_id)

    backups_off, backups_window = app_settings.backups_suppressed(settings)
    notifications_off, notifications_window = app_settings.notifications_suppressed(
        settings
    )

    return {
        "now": datetime.now(timezone.utc),
        "timezone": settings.maintenance_timezone,
        "open_windows": [window.name for window in app_settings.active_windows(settings)],
        "backups_suppressed": backups_off,
        "backups_suppressed_by": backups_window,
        "notifications_suppressed": notifications_off,
        "notifications_suppressed_by": notifications_window,
        "next_window_start": app_settings.next_window_start(settings),
    }


# --------------------------------------------------------------------------
# Remote backup targets
# --------------------------------------------------------------------------


@router.get("/targets", response_model=List[TargetResponse])
def list_targets(
    enabled_only: bool = Query(False),
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:read")),
):
    """Remote SFTP/FTP servers configurations are copied to"""
    statement = select(BackupTarget).where(
        BackupTarget.organization_id == organization_id
    )
    if enabled_only:
        statement = statement.where(BackupTarget.is_enabled.is_(True))

    targets = list(db.execute(statement.order_by(BackupTarget.name)).scalars())
    return [_serialise_target(target) for target in targets]


@router.post(
    "/targets", response_model=TargetResponse, status_code=status.HTTP_201_CREATED
)
def create_target(
    payload: TargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:write")),
):
    """Add a remote backup target"""
    clash = db.execute(
        select(BackupTarget.id).where(
            BackupTarget.organization_id == organization_id,
            BackupTarget.name == payload.name,
        )
    ).scalar()
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A target named '{payload.name}' already exists",
        )

    protocol = payload.protocol.lower()

    if protocol != "sftp" and payload.private_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A private key can only be used with SFTP",
        )

    if not payload.password and not payload.private_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supply a password or, for SFTP, a private key",
        )

    target = BackupTarget(
        organization_id=organization_id,
        name=payload.name,
        protocol=protocol,
        host=payload.host,
        port=payload.port or DEFAULT_PORTS.get(protocol, 22),
        username=payload.username,
        encrypted_password=encryption_service.encrypt(payload.password)
        if payload.password
        else None,
        private_key=encryption_service.encrypt(payload.private_key)
        if payload.private_key
        else None,
        private_key_passphrase=encryption_service.encrypt(
            payload.private_key_passphrase
        )
        if payload.private_key_passphrase
        else None,
        remote_path=payload.remote_path or "/",
        use_device_subdirectories=payload.use_device_subdirectories,
        is_enabled=payload.is_enabled,
        upload_on_backup=payload.upload_on_backup,
        verify_host_key=payload.verify_host_key,
        known_host_key=payload.known_host_key,
        created_by=current_user.id,
    )

    db.add(target)
    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="create_backup_target",
        resource_type="backup_target",
        resource_id=target.id,
        details={"name": target.name, "protocol": target.protocol, "host": target.host},
        ip_address=_client_ip(request),
    )

    return _serialise_target(target)


@router.get("/targets/{target_id}", response_model=TargetResponse)
def read_target(
    target_id: int,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:read")),
):
    """One remote backup target"""
    return _serialise_target(_get_target_or_404(db, target_id, organization_id))


@router.put("/targets/{target_id}", response_model=TargetResponse)
def update_target(
    target_id: int,
    payload: TargetUpdate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:write")),
):
    """
    Change a remote backup target

    Omitting a secret leaves the stored one in place; the explicit clear flags
    remove it.
    """
    target = _get_target_or_404(db, target_id, organization_id)

    if payload.name and payload.name != target.name:
        clash = db.execute(
            select(BackupTarget.id).where(
                BackupTarget.organization_id == organization_id,
                BackupTarget.name == payload.name,
                BackupTarget.id != target.id,
            )
        ).scalar()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A target named '{payload.name}' already exists",
            )

    for field in (
        "name",
        "host",
        "port",
        "username",
        "remote_path",
        "use_device_subdirectories",
        "is_enabled",
        "upload_on_backup",
        "verify_host_key",
        "known_host_key",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(target, field, value)

    if payload.protocol:
        target.protocol = payload.protocol.lower()
        if payload.port is None:
            target.port = DEFAULT_PORTS.get(target.protocol, target.port)

    if payload.clear_password:
        target.encrypted_password = None
    elif payload.password:
        target.encrypted_password = encryption_service.encrypt(payload.password)

    if payload.clear_private_key:
        target.private_key = None
        target.private_key_passphrase = None
    elif payload.private_key:
        target.private_key = encryption_service.encrypt(payload.private_key)

    if payload.private_key_passphrase:
        target.private_key_passphrase = encryption_service.encrypt(
            payload.private_key_passphrase
        )

    if not target.encrypted_password and not target.private_key:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A target needs a password or, for SFTP, a private key",
        )

    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="update_backup_target",
        resource_type="backup_target",
        resource_id=target.id,
        details={"name": target.name},
        ip_address=_client_ip(request),
    )

    return _serialise_target(target)


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:delete")),
):
    """Delete a remote backup target"""
    target = _get_target_or_404(db, target_id, organization_id)
    name = target.name

    db.delete(target)
    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="delete_backup_target",
        resource_type="backup_target",
        resource_id=target_id,
        details={"name": name},
        ip_address=_client_ip(request),
    )


@router.post("/targets/{target_id}/test")
def test_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:write")),
):
    """
    Connect to a target and check the remote directory is writable

    Returns the outcome rather than raising, so the UI can show the server's
    own error text.
    """
    target = _get_target_or_404(db, target_id, organization_id)

    config = remote_backup.TargetConfig.from_model(target)
    result = remote_backup.check_target_connection(config)

    target.last_status = "success" if result.get("success") else "failed"
    target.last_run_at = datetime.now(timezone.utc)
    target.last_error = None if result.get("success") else result.get("message")
    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="test_backup_target",
        resource_type="backup_target",
        resource_id=target.id,
        details={"host": target.host},
        status="success" if result.get("success") else "failed",
        error_message=None if result.get("success") else result.get("message"),
        ip_address=_client_ip(request),
    )

    return result


@router.post("/targets/{target_id}/upload")
def upload_to_target(
    target_id: int,
    payload: TargetUploadRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("targets:write")),
):
    """
    Push stored configurations to a target

    With no configuration ids the latest backup of each device is sent, which
    is what "seed the archive" means in practice.
    """
    target = _get_target_or_404(db, target_id, organization_id)

    if payload.run_async:
        from app.tasks.remote_backup import upload_to_target_task

        task = upload_to_target_task.delay(
            organization_id=organization_id,
            target_id=target.id,
            configuration_ids=payload.configuration_ids,
            device_ids=payload.device_ids,
            limit=payload.limit,
            user_id=current_user.id,
        )

        return {
            "success": True,
            "queued": True,
            "task_id": task.id,
            "message": f"Upload to '{target.name}' queued",
        }

    from app.tasks.remote_backup import run_upload

    result = run_upload(
        db,
        organization_id=organization_id,
        target=target,
        configuration_ids=payload.configuration_ids,
        device_ids=payload.device_ids,
        limit=payload.limit,
    )

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="upload_to_backup_target",
        resource_type="backup_target",
        resource_id=target.id,
        details=result,
        status="success" if result.get("success") else "failed",
        ip_address=_client_ip(request),
    )

    return result
