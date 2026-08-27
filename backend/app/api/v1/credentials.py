"""
Credential vault endpoints

An ordered list of logins discovery tries against a device. Secrets are
write-only: a read says whether one is stored, never what it is, and an update
that omits a secret leaves the stored value alone.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_organization_id, require_permission
from app.core.database import get_db
from app.models.credential import Credential
from app.models.user import User
from app.repositories.audit_log import AuditLogRepository
from app.services import credentials as vault
from app.services.credentials import CredentialError

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class CredentialResponse(BaseModel):
    """A credential set, without its secrets"""

    id: int
    name: str
    description: Optional[str]
    kind: str
    priority: int
    is_enabled: bool

    username: Optional[str]
    ssh_key_path: Optional[str]

    snmp_version: Optional[str]
    snmp_v3_user: Optional[str]
    snmp_v3_auth_protocol: Optional[str]
    snmp_v3_priv_protocol: Optional[str]

    has_password: bool = False
    has_enable_secret: bool = False
    has_community: bool = False
    has_v3_auth_key: bool = False
    has_v3_priv_key: bool = False

    success_count: int
    failure_count: int
    last_success_at: Optional[datetime]
    last_failure_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]


class CredentialCreate(BaseModel):
    """Create a credential set"""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    kind: str = Field("cli", pattern="^(cli|snmp)$")
    priority: int = Field(100, ge=1, le=9999)
    is_enabled: bool = True

    # CLI
    username: Optional[str] = Field(None, max_length=100)
    password: Optional[str] = None
    enable_secret: Optional[str] = None
    ssh_key_path: Optional[str] = None

    # SNMP
    snmp_version: Optional[str] = Field(None, pattern="^(1|2c|3)$")
    community: Optional[str] = None
    snmp_v3_user: Optional[str] = Field(None, max_length=100)
    v3_auth_key: Optional[str] = None
    v3_priv_key: Optional[str] = None
    snmp_v3_auth_protocol: Optional[str] = Field(None, max_length=20)
    snmp_v3_priv_protocol: Optional[str] = Field(None, max_length=20)


class CredentialUpdate(BaseModel):
    """
    Change a credential set

    Omitting a secret keeps the stored one; the clear_* flags remove it.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=9999)
    is_enabled: Optional[bool] = None

    username: Optional[str] = Field(None, max_length=100)
    password: Optional[str] = None
    clear_password: bool = False
    enable_secret: Optional[str] = None
    clear_enable_secret: bool = False
    ssh_key_path: Optional[str] = None

    snmp_version: Optional[str] = Field(None, pattern="^(1|2c|3)$")
    community: Optional[str] = None
    clear_community: bool = False
    snmp_v3_user: Optional[str] = Field(None, max_length=100)
    v3_auth_key: Optional[str] = None
    clear_v3_auth_key: bool = False
    v3_priv_key: Optional[str] = None
    clear_v3_priv_key: bool = False
    snmp_v3_auth_protocol: Optional[str] = Field(None, max_length=20)
    snmp_v3_priv_protocol: Optional[str] = Field(None, max_length=20)


class ReorderRequest(BaseModel):
    """Set the order credentials are tried in"""

    credential_ids: List[int] = Field(
        ..., description="Ids in the order to try them, first tried first"
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _serialise(credential: Credential) -> CredentialResponse:
    """Build a response with the secrets reduced to booleans"""
    return CredentialResponse(
        id=credential.id,
        name=credential.name,
        description=credential.description,
        kind=credential.kind,
        priority=credential.priority,
        is_enabled=credential.is_enabled,
        username=credential.username,
        ssh_key_path=credential.ssh_key_path,
        snmp_version=credential.snmp_version,
        snmp_v3_user=credential.snmp_v3_user,
        snmp_v3_auth_protocol=credential.snmp_v3_auth_protocol,
        snmp_v3_priv_protocol=credential.snmp_v3_priv_protocol,
        success_count=credential.success_count or 0,
        failure_count=credential.failure_count or 0,
        last_success_at=credential.last_success_at,
        last_failure_at=credential.last_failure_at,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
        **vault.has_secret(credential),
    )


def _get_or_404(db: Session, credential_id: int, organization_id: int) -> Credential:
    credential = db.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found"
        )

    return credential


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.get("", response_model=List[CredentialResponse])
def list_credentials(
    kind: Optional[str] = Query(None, pattern="^(cli|snmp)$"),
    enabled_only: bool = Query(False),
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:read")),
):
    """
    The credential sets discovery will try, in the order it tries them

    Never returns a secret.
    """
    statement = select(Credential).where(
        Credential.organization_id == organization_id
    )
    if kind:
        statement = statement.where(Credential.kind == kind)
    if enabled_only:
        statement = statement.where(Credential.is_enabled.is_(True))

    rows = list(
        db.execute(
            statement.order_by(Credential.kind, Credential.priority, Credential.id)
        ).scalars()
    )

    return [_serialise(row) for row in rows]


@router.get("/summary")
def credential_summary(
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:read")),
):
    """
    How many credentials of each kind discovery has available

    Declared before /{credential_id} so the literal path wins. Shown where a
    crawl is started: with no CLI credentials a crawl maps the topology but
    authenticates nothing, which is worth saying before the run rather than
    after.
    """
    return vault.summarise(db, organization_id)


@router.post("", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
def create_credential(
    payload: CredentialCreate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:write")),
):
    """Add a credential set"""
    data = payload.model_dump()

    try:
        vault.validate(payload.kind, data, creating=True)
    except CredentialError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )

    clash = db.execute(
        select(Credential.id).where(
            Credential.organization_id == organization_id,
            Credential.name == payload.name,
        )
    ).scalar()
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A credential named '{payload.name}' already exists",
        )

    credential = Credential(
        organization_id=organization_id,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        priority=payload.priority,
        is_enabled=payload.is_enabled,
        username=payload.username,
        ssh_key_path=payload.ssh_key_path,
        snmp_version=payload.snmp_version,
        snmp_v3_user=payload.snmp_v3_user,
        snmp_v3_auth_protocol=payload.snmp_v3_auth_protocol,
        snmp_v3_priv_protocol=payload.snmp_v3_priv_protocol,
        created_by=current_user.id,
    )
    vault.apply_secrets(credential, data)

    db.add(credential)
    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="create_credential",
        resource_type="credential",
        resource_id=credential.id,
        details={"name": credential.name, "kind": credential.kind},
        ip_address=_client_ip(request),
    )

    return _serialise(credential)


@router.get("/{credential_id}", response_model=CredentialResponse)
def read_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:read")),
):
    """One credential set, without its secrets"""
    return _serialise(_get_or_404(db, credential_id, organization_id))


@router.put("/{credential_id}", response_model=CredentialResponse)
def update_credential(
    credential_id: int,
    payload: CredentialUpdate,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:write")),
):
    """
    Change a credential set

    A secret left out is kept; the clear_* flags remove one.
    """
    credential = _get_or_404(db, credential_id, organization_id)
    data = payload.model_dump(exclude_unset=True)

    if payload.name and payload.name != credential.name:
        clash = db.execute(
            select(Credential.id).where(
                Credential.organization_id == organization_id,
                Credential.name == payload.name,
                Credential.id != credential.id,
            )
        ).scalar()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A credential named '{payload.name}' already exists",
            )

    for field in (
        "name",
        "description",
        "priority",
        "is_enabled",
        "username",
        "ssh_key_path",
        "snmp_version",
        "snmp_v3_user",
        "snmp_v3_auth_protocol",
        "snmp_v3_priv_protocol",
    ):
        if field in data and data[field] is not None:
            setattr(credential, field, data[field])

    changed_secrets = vault.apply_secrets(credential, data)

    # A credential with nothing to authenticate with is tried against every
    # device on every crawl and can never succeed, so refuse to leave one in
    # that state.
    if credential.kind == vault.CLI:
        if not (credential.encrypted_password or credential.ssh_key_path):
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A CLI credential needs a password or an SSH key path",
            )
    else:
        has_snmp_secret = (
            credential.encrypted_community or credential.encrypted_v3_auth_key
        )
        if not has_snmp_secret:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An SNMP credential needs a community or a v3 auth key",
            )

    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="update_credential",
        resource_type="credential",
        resource_id=credential.id,
        details={"name": credential.name, "secrets_changed": changed_secrets},
        ip_address=_client_ip(request),
    )

    return _serialise(credential)


@router.post("/reorder", response_model=List[CredentialResponse])
def reorder_credentials(
    payload: ReorderRequest,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:write")),
):
    """
    Set the order credentials are tried in

    Order matters for more than tidiness: every failed attempt against a
    device costs a connection timeout, so the credential most likely to work
    belongs first.
    """
    rows = {
        row.id: row
        for row in db.execute(
            select(Credential).where(
                Credential.organization_id == organization_id,
                Credential.id.in_(payload.credential_ids),
            )
        ).scalars()
    }

    missing = [cid for cid in payload.credential_ids if cid not in rows]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No such credential(s) in this organization: {missing}",
        )

    for position, credential_id in enumerate(payload.credential_ids, start=1):
        rows[credential_id].priority = position * 10

    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="reorder_credentials",
        resource_type="credential",
        details={"order": payload.credential_ids},
        ip_address=_client_ip(request),
    )

    ordered = list(
        db.execute(
            select(Credential)
            .where(Credential.organization_id == organization_id)
            .order_by(Credential.kind, Credential.priority, Credential.id)
        ).scalars()
    )

    return [_serialise(row) for row in ordered]


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: int,
    request: Request,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:delete")),
):
    """
    Delete a credential set

    Devices that authenticated with it keep working - they hold their own copy
    of the credentials that succeeded - but they lose the hint that made the
    next crawl try this one first.
    """
    credential = _get_or_404(db, credential_id, organization_id)
    name = credential.name

    db.delete(credential)
    db.commit()

    AuditLogRepository(db).log_action(
        user_id=current_user.id,
        action="delete_credential",
        resource_type="credential",
        resource_id=credential_id,
        details={"name": name},
        ip_address=_client_ip(request),
    )


@router.post("/{credential_id}/test")
def test_credential(
    credential_id: int,
    device_id: int = Query(..., description="Device to try it against"),
    request: Request = None,
    db: Session = Depends(get_db),
    organization_id: int = Depends(get_organization_id),
    current_user: User = Depends(require_permission("credentials:write")),
):
    """
    Try one credential against one device, and report what happened

    Returns the outcome rather than raising, so the UI can show the device's
    own refusal text.
    """
    from app.models.device import Device
    from app.services.discovery_probe import try_credential

    credential = _get_or_404(db, credential_id, organization_id)

    device = db.execute(
        select(Device).where(
            Device.id == device_id, Device.organization_id == organization_id
        )
    ).scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
        )

    attempt = vault.to_attempt(credential)
    result = try_credential(device, attempt)

    vault.record_outcome(db, credential.id, result.ok)

    return {
        "success": result.ok,
        "credential": credential.name,
        "device": device.hostname,
        "transport": result.transport,
        "result": result.result,
        "message": result.message,
        "duration_ms": result.duration_ms,
        "facts": result.facts,
    }
