"""
Credential vault: the ordered credential sets discovery tries

Discovery walks into devices nobody has entered credentials for, so it needs a
list to work through rather than one set inherited from the seed. This module
owns that list, the order it is tried in, and the bookkeeping that records
which entries actually work.

Secrets are Fernet encrypted with ENCRYPTION_KEY and decrypted only into the
plain `CredentialAttempt` handed to a transport. Nothing here returns a
ciphertext to a caller, and no endpoint returns a secret at all.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credential import Credential
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)

CLI = "cli"
SNMP = "snmp"
KINDS = (CLI, SNMP)


class CredentialError(ValueError):
    """Raised when a credential set is malformed"""


@dataclass
class CredentialAttempt:
    """
    One decrypted credential, ready to hand to a transport

    Deliberately a plain dataclass rather than the ORM row: discovery runs
    probes on worker threads, and an ORM instance is not safe to carry across
    them. It also keeps the decrypted secret out of the session's identity
    map.
    """

    id: Optional[int]
    name: str
    kind: str

    # CLI
    username: Optional[str] = None
    password: Optional[str] = None
    enable_secret: Optional[str] = None
    ssh_key_path: Optional[str] = None

    # SNMP
    snmp_version: Optional[str] = None
    community: Optional[str] = None
    v3_user: Optional[str] = None
    v3_auth_key: Optional[str] = None
    v3_priv_key: Optional[str] = None
    v3_auth_protocol: Optional[str] = None
    v3_priv_protocol: Optional[str] = None

    @property
    def label(self) -> str:
        """How this attempt is named in a probe result or a log line"""
        if self.kind == SNMP:
            return f"{self.name} (SNMP v{self.snmp_version or '?'})"
        return f"{self.name} ({self.username})"

    def snmp_params(self, port: int = 161) -> Dict[str, Any]:
        """
        The shape DeviceConnector wants for an SNMP attempt

        Already decrypted, so the connector must be given snmp_plaintext=True
        alongside this.

        Args:
            port: The device's SNMP port

        Returns:
            dict of SNMP parameters, secrets in plaintext
        """
        return {
            "version": self.snmp_version,
            "community": self.community,
            "port": port,
            "v3_user": self.v3_user,
            "v3_auth_key": self.v3_auth_key,
            "v3_priv_key": self.v3_priv_key,
            "v3_auth_protocol": self.v3_auth_protocol,
            "v3_priv_protocol": self.v3_priv_protocol,
        }


def _decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a stored secret, treating a failure as absent"""
    if not value:
        return None
    try:
        return encryption_service.decrypt(value)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not decrypt a stored credential secret: {e}")
        return None


def to_attempt(credential: Credential) -> CredentialAttempt:
    """
    Decrypt a stored credential into something a transport can use

    Args:
        credential: The stored row

    Returns:
        CredentialAttempt
    """
    return CredentialAttempt(
        id=credential.id,
        name=credential.name,
        kind=credential.kind,
        username=credential.username,
        password=_decrypt(credential.encrypted_password),
        enable_secret=_decrypt(credential.encrypted_enable_secret),
        ssh_key_path=credential.ssh_key_path,
        snmp_version=credential.snmp_version,
        community=_decrypt(credential.encrypted_community),
        v3_user=credential.snmp_v3_user,
        v3_auth_key=_decrypt(credential.encrypted_v3_auth_key),
        v3_priv_key=_decrypt(credential.encrypted_v3_priv_key),
        v3_auth_protocol=credential.snmp_v3_auth_protocol,
        v3_priv_protocol=credential.snmp_v3_priv_protocol,
    )


def list_for_kind(
    db: Session,
    organization_id: int,
    kind: str,
    prefer_credential_id: Optional[int] = None,
) -> List[CredentialAttempt]:
    """
    The enabled credentials of one kind, in the order to try them

    Args:
        db: Database session
        organization_id: Tenant scope
        kind: 'cli' or 'snmp'
        prefer_credential_id: Try this one first regardless of priority. Pass
            the credential that last worked for the device: walking the whole
            list again on every run is the difference between a crawl that
            takes seconds and one that takes minutes per device.

    Returns:
        Decrypted attempts, most likely first
    """
    if kind not in KINDS:
        raise CredentialError(f"Unknown credential kind '{kind}'")

    rows = list(
        db.execute(
            select(Credential)
            .where(
                Credential.organization_id == organization_id,
                Credential.kind == kind,
                Credential.is_enabled.is_(True),
            )
            .order_by(Credential.priority, Credential.id)
        ).scalars()
    )

    attempts = [to_attempt(row) for row in rows]

    if prefer_credential_id is not None:
        attempts.sort(key=lambda attempt: attempt.id != prefer_credential_id)

    return attempts


def record_outcome(
    db: Session,
    credential_id: Optional[int],
    success: bool,
    commit: bool = True,
) -> None:
    """
    Note that a credential worked or did not

    Which entries actually work is the most useful thing to know when a crawl
    comes back half empty, so it is counted rather than only logged.

    Args:
        db: Database session
        credential_id: The credential tried; ignored when None
        success: Whether it authenticated
        commit: Commit now. A crawl passes False and commits once.
    """
    if credential_id is None:
        return

    credential = db.get(Credential, credential_id)
    if not credential:
        return

    now = datetime.now(timezone.utc)

    if success:
        credential.success_count = (credential.success_count or 0) + 1
        credential.last_success_at = now
    else:
        credential.failure_count = (credential.failure_count or 0) + 1
        credential.last_failure_at = now

    if commit:
        db.commit()


# --------------------------------------------------------------------------
# Validation and CRUD
# --------------------------------------------------------------------------


def validate(kind: str, payload: Dict[str, Any], creating: bool = True) -> None:
    """
    Check a credential set is usable before storing it

    A credential with no secret is worse than no credential: it is tried
    against every device on every crawl and can never succeed.

    Args:
        kind: 'cli' or 'snmp'
        payload: The supplied fields
        creating: True on create, when the secret must be present

    Raises:
        CredentialError
    """
    if kind not in KINDS:
        raise CredentialError(
            f"kind must be one of {', '.join(KINDS)}, not '{kind}'"
        )

    if kind == CLI:
        if creating and not (payload.get("username") or "").strip():
            raise CredentialError("A CLI credential needs a username")
        if creating and not (payload.get("password") or payload.get("ssh_key_path")):
            raise CredentialError(
                "A CLI credential needs a password or an SSH key path"
            )
        return

    version = (payload.get("snmp_version") or "").strip()
    if creating and version not in ("1", "2c", "3"):
        raise CredentialError("An SNMP credential needs a version of 1, 2c or 3")

    if version in ("1", "2c") and creating and not payload.get("community"):
        raise CredentialError(f"SNMP v{version} needs a community string")

    if version == "3" and creating:
        if not (payload.get("snmp_v3_user") or "").strip():
            raise CredentialError("SNMP v3 needs a username")
        if not payload.get("v3_auth_key"):
            raise CredentialError("SNMP v3 needs an authentication key")


# The request fields that are secrets, and the column each is stored in.
SECRET_FIELDS = {
    "password": "encrypted_password",
    "enable_secret": "encrypted_enable_secret",
    "community": "encrypted_community",
    "v3_auth_key": "encrypted_v3_auth_key",
    "v3_priv_key": "encrypted_v3_priv_key",
}


def apply_secrets(credential: Credential, payload: Dict[str, Any]) -> List[str]:
    """
    Encrypt whichever secrets were supplied onto a credential

    A secret the caller omitted is left alone, matching how a device password
    and the SMTP password behave. Clearing one takes an explicit flag.

    Args:
        credential: The row being written
        payload: The supplied fields

    Returns:
        The names of the secrets that changed, for the audit entry
    """
    changed = []

    for field, column in SECRET_FIELDS.items():
        if payload.get(f"clear_{field}"):
            setattr(credential, column, None)
            changed.append(field)
        elif payload.get(field):
            setattr(credential, column, encryption_service.encrypt(payload[field]))
            changed.append(field)

    return changed


def has_secret(credential: Credential) -> Dict[str, bool]:
    """Which secrets are stored, for a read that must not return their values"""
    return {
        f"has_{field}": bool(getattr(credential, column))
        for field, column in SECRET_FIELDS.items()
    }


def summarise(db: Session, organization_id: int) -> Dict[str, Any]:
    """
    How many credentials of each kind are available to discovery

    Shown wherever a crawl is started: a discovery with no CLI credentials
    will find topology but authenticate nothing, and it is better to say so
    up front than to explain an empty result afterwards.

    Args:
        db: Database session
        organization_id: Tenant scope

    Returns:
        dict of counts by kind
    """
    rows = list(
        db.execute(
            select(Credential.kind, Credential.is_enabled).where(
                Credential.organization_id == organization_id
            )
        ).all()
    )

    return {
        "cli": sum(1 for kind, enabled in rows if kind == CLI and enabled),
        "snmp": sum(1 for kind, enabled in rows if kind == SNMP and enabled),
        "disabled": sum(1 for _, enabled in rows if not enabled),
        "total": len(rows),
    }


# --------------------------------------------------------------------------
# What a device actually connects with
# --------------------------------------------------------------------------


@dataclass
class ResolvedLogin:
    """
    The credentials a device connects with, and where they came from

    A device either holds its own username and password or points at a vault
    entry. Everything that opens a connection has to go through this, or
    choosing a vault credential in the UI would look like it worked and change
    nothing about how the device is reached.

    Every secret here is left encrypted, matching the form the device's own
    columns are in - so a caller substitutes this for the device fields and
    changes nothing else, and nothing is decrypted unless it is used.
    """

    username: Optional[str] = None
    encrypted_password: Optional[str] = None
    enable_secret: Optional[str] = None
    ssh_key_path: Optional[str] = None

    # Same keys and same encrypted form as device_connector.snmp_params().
    snmp: Optional[Dict[str, Any]] = None

    # For the UI and the audit trail: 'device', or the vault entry's name.
    cli_source: str = "device"
    snmp_source: str = "device"


def resolve_for_device(db: Session, device) -> ResolvedLogin:
    """
    Work out which credentials a device logs in with

    Args:
        db: Database session
        device: The Device row

    Returns:
        ResolvedLogin, falling back to the device's own fields whenever it
        names no vault credential, or names one that has since been deleted.
    """
    resolved = ResolvedLogin(
        username=device.username,
        encrypted_password=device.encrypted_password,
        enable_secret=device.enable_secret,
        ssh_key_path=device.ssh_key_path,
    )

    cli = _vault_entry(db, device, getattr(device, "credential_id", None), CLI)
    if cli:
        resolved.username = cli.username
        resolved.encrypted_password = cli.encrypted_password
        resolved.enable_secret = cli.encrypted_enable_secret
        # An SSH key on the device is kept when the vault entry has none: the
        # key is a property of this host, the login is not.
        resolved.ssh_key_path = cli.ssh_key_path or device.ssh_key_path
        resolved.cli_source = cli.name

    snmp = _vault_entry(db, device, getattr(device, "snmp_credential_id", None), SNMP)
    if snmp:
        # The port stays the device's: which community to use and which port it
        # answers on are separate facts.
        resolved.snmp = {
            "version": snmp.snmp_version,
            "community": snmp.encrypted_community,
            "port": device.snmp_port,
            "v3_user": snmp.snmp_v3_user,
            "v3_auth_key": snmp.encrypted_v3_auth_key,
            "v3_priv_key": snmp.encrypted_v3_priv_key,
            "v3_auth_protocol": snmp.snmp_v3_auth_protocol,
            "v3_priv_protocol": snmp.snmp_v3_priv_protocol,
        }
        resolved.snmp_source = snmp.name

    return resolved


def _vault_entry(db: Session, device, credential_id: Optional[int], kind: str):
    """
    Load a device's vault credential, or None

    A credential of the wrong kind is ignored rather than used: an SNMP
    community is not a login, and silently trying it as one would produce an
    authentication failure that looks like a wrong password.
    """
    if not credential_id:
        return None

    credential = db.scalars(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.organization_id == device.organization_id,
        )
    ).first()

    if not credential:
        logger.warning(
            f"Device {device.hostname} points at credential {credential_id}, "
            f"which no longer exists; using the device's own credentials"
        )
        return None

    if credential.kind != kind:
        logger.warning(
            f"Device {device.hostname} points at credential "
            f"'{credential.name}' as its {kind} credential, but it is a "
            f"{credential.kind} entry; ignoring it"
        )
        return None

    return credential


def devices_using(db: Session, credential_id: int) -> List[str]:
    """
    The hostnames of devices that depend on a credential

    Deleting a credential a device logs in with would break that device's
    backups at the next run, with nothing on screen to explain why, so the
    endpoints check this first.

    Args:
        db: Database session
        credential_id: The credential about to be deleted or disabled

    Returns:
        Hostnames, alphabetically
    """
    from app.models.device import Device

    return list(
        db.scalars(
            select(Device.hostname)
            .where(
                (Device.credential_id == credential_id)
                | (Device.snmp_credential_id == credential_id)
            )
            .order_by(Device.hostname)
        ).all()
    )
