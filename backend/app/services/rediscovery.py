"""
Re-probe devices that are already in the inventory

Discovery decides what a device is at the moment it is found, from whatever
answered at the time. That answer goes stale: credentials are rotated, SSH is
enabled on a switch that only spoke SNMP, a device is replaced with a different
model behind the same address, or the vault gains the credential that would
have worked. Rediscovery asks all the questions again for a device that already
exists and updates what it finds.

What it can change on a device:

- ``transport``: whichever CLI transport actually answered, or snmp when only
  SNMP did
- ``device_type``: the platform, identified from SNMP sysDescr, the SSH server
  string, the pre-auth banner, a version command, the prompt, the MAC's OUI
  vendor, or by trying each vendor's configuration command until one answers
- ``is_active``: on the backup schedule only when a CLI login actually
  succeeded
- ``credential_id``: the vault entry that worked, so the next run tries it first
- the discovered facts, and one ``device_probes`` row per transport

What it never changes: the hostname, the address, the location, the tags, or
anything else a person entered. A rediscovery that renamed devices would be
unusable.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.credential import DeviceProbe
from app.models.device import Device
from app.models.network import HostInventory
from app.services import credentials as vault
from app.services import discovery_probe as probe
from app.services import oui as oui_service

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DeviceOutcome:
    """What rediscovery decided about one device"""

    device_id: int
    hostname: str
    ip_address: str

    reachable: bool = False
    authenticated: bool = False
    transport: Optional[str] = None
    device_type: Optional[str] = None
    credential_name: Optional[str] = None
    identified_by: Optional[str] = None

    # Only the fields that actually moved, for the summary and the audit log.
    changes: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "transport": self.transport,
            "device_type": self.device_type,
            "credential_name": self.credential_name,
            "identified_by": self.identified_by,
            "changes": self.changes,
            "message": self.message,
            "error": self.error,
        }


@dataclass
class RediscoverySummary:
    """The result of one rediscovery pass"""

    probed: int = 0
    reachable: int = 0
    authenticated: int = 0
    changed: int = 0
    failed: int = 0
    devices: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "probed": self.probed,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "changed": self.changed,
            "failed": self.failed,
            "devices": self.devices,
        }


class RediscoveryService:
    """Re-probes existing devices and updates what it learns"""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def rediscover(
        self,
        organization_id: int,
        device_ids: Optional[Sequence[int]] = None,
        include_inactive: bool = True,
    ) -> RediscoverySummary:
        """
        Re-probe some or all of an organization's devices

        Args:
            organization_id: Tenant scope
            device_ids: Devices to probe; every device when omitted
            include_inactive: Probe devices that are off the backup list too.
                On by default, because those are exactly the ones worth
                re-checking - a device that could not be logged into last time
                is the reason this exists.

        Returns:
            RediscoverySummary
        """
        devices = self._devices(organization_id, device_ids, include_inactive)
        summary = RediscoverySummary()

        if not devices:
            return summary

        # Loaded once for the whole pass rather than per device: the vault is
        # small and every device tries the same list.
        cli_attempts = vault.list_for_kind(self.db, organization_id, vault.CLI)
        snmp_attempts = vault.list_for_kind(self.db, organization_id, vault.SNMP)

        for device in devices:
            outcome = self._rediscover_one(
                organization_id, device, cli_attempts, snmp_attempts
            )

            summary.probed += 1
            if outcome.error:
                summary.failed += 1
            if outcome.reachable:
                summary.reachable += 1
            if outcome.authenticated:
                summary.authenticated += 1
            if outcome.changes:
                summary.changed += 1

            summary.devices.append(outcome.as_dict())

        self.db.commit()
        return summary

    # ------------------------------------------------------------------
    # One device
    # ------------------------------------------------------------------

    def _rediscover_one(
        self,
        organization_id: int,
        device: Device,
        cli_attempts,
        snmp_attempts,
    ) -> DeviceOutcome:
        """
        Probe one device and apply what came back

        Never raises: one device that blows up must not abandon the rest of the
        pass, so a failure becomes an outcome with an error on it.
        """
        outcome = DeviceOutcome(
            device_id=device.id,
            hostname=device.hostname,
            ip_address=device.ip_address,
        )

        # The device's own credentials go last, after the vault, so a rotated
        # vault entry wins over a stale one stored on the row.
        attempts = list(cli_attempts) + self._own_attempts(device)
        snmp = list(snmp_attempts) + self._own_snmp(device)

        hint = self._oui_hint(device)

        try:
            assessment = probe.assess(
                device.ip_address,
                cli_attempts=attempts,
                snmp_attempts=snmp,
                device_type=device.device_type,
                platform_hint=hint,
                ssh_port=device.port if device.transport != "telnet" else 22,
                telnet_port=device.port if device.transport == "telnet" else 23,
                snmp_port=device.snmp_port or 161,
            )
        except Exception as e:  # noqa: BLE001 - one device must not stop the pass
            logger.exception(f"Rediscovery of {device.hostname} failed")
            outcome.error = f"{type(e).__name__}: {e}"
            outcome.message = "The probe itself failed; nothing was changed"
            return outcome

        self._record_probes(organization_id, device.id, assessment)
        self._apply(device, assessment, outcome, hint)

        return outcome

    def _apply(
        self,
        device: Device,
        assessment,
        outcome: DeviceOutcome,
        hint: Optional[str],
    ) -> None:
        """Write an assessment onto the device, recording what moved"""
        successful = next((p for p in assessment.probes if p.ok), None)
        cli_success = next(
            (p for p in assessment.probes if p.ok and p.transport in ("ssh", "telnet")),
            None,
        )

        outcome.reachable = bool(successful)
        outcome.authenticated = bool(cli_success)
        outcome.identified_by = assessment.facts.get("identified_by")

        def change(attribute: str, value: Any) -> None:
            """Set an attribute, remembering the old value when it moves"""
            current = getattr(device, attribute)
            if value is None or value == current:
                return
            outcome.changes[attribute] = {"from": current, "to": value}
            setattr(device, attribute, value)

        # --- what it is ------------------------------------------------
        identified = assessment.device_type or assessment.facts.get("device_type")
        if identified:
            change("device_type", identified)
        outcome.device_type = device.device_type

        # --- how to reach it -------------------------------------------
        if cli_success:
            change("transport", cli_success.transport)
        elif successful and successful.transport == "snmp":
            # Nothing answered on the CLI, but SNMP did. Say so rather than
            # leaving a device pointed at an SSH port that refused: an SNMP
            # device is inventoried and crawled, and never backed up.
            change("transport", "snmp")
        outcome.transport = device.transport

        # --- whether it can be backed up -------------------------------
        change("last_auth_status", assessment.auth_status)
        device.last_auth_at = _now()
        device.auth_error = assessment.auth_error
        device.last_discovered_at = _now()

        eligible = bool(assessment.backup_eligible)
        if device.is_active != eligible:
            outcome.changes["is_active"] = {"from": device.is_active, "to": eligible}
            device.is_active = eligible

        if cli_success and cli_success.credential_id:
            change("credential_id", cli_success.credential_id)
            outcome.credential_name = cli_success.credential_name
        elif cli_success:
            # The device's own credentials worked, so it keeps them.
            outcome.credential_name = cli_success.credential_name

        # --- what it said about itself ---------------------------------
        facts = assessment.facts or {}
        for attribute, key in (
            ("model", "model"),
            ("serial_number", "serial_number"),
            ("os_version", "os_version"),
            ("snmp_sysname", "sysname"),
            ("snmp_sysdescr", "sysdescr"),
            ("snmp_location", "location"),
            ("snmp_contact", "contact"),
        ):
            if facts.get(key):
                change(attribute, facts[key])

        if facts.get("uptime_seconds"):
            device.snmp_uptime_seconds = facts["uptime_seconds"]
        if any(key in facts for key in ("sysdescr", "sysname")):
            device.snmp_last_polled_at = _now()

        extra = {
            key: value
            for key, value in facts.items()
            if key not in ("device_type", "identified_by")
        }
        if extra:
            device.discovered_facts = {**(device.discovered_facts or {}), **extra}

        outcome.message = self._describe(outcome, assessment, hint)

    @staticmethod
    def _describe(outcome: DeviceOutcome, assessment, hint: Optional[str]) -> str:
        """One line an operator can read in the results table"""
        if outcome.authenticated:
            how = {
                "ssh": "Logged in over SSH",
                "version": "Logged in",
                "telnet": "Logged in over telnet",
            }.get(outcome.transport, "Logged in")

            identified = ""
            if outcome.identified_by == "collection":
                identified = ", platform identified by trying each vendor's command"
            elif outcome.identified_by == "ssh":
                identified = ", platform identified from the SSH banner"
            elif outcome.identified_by == "prompt":
                identified = ", platform identified from the prompt"
            elif outcome.identified_by == "version":
                identified = ", platform identified from its version output"
            elif hint and outcome.device_type == hint:
                identified = ", platform matched the MAC vendor"

            return f"{how} as {outcome.credential_name or 'the stored user'}{identified}"

        if outcome.reachable:
            return (
                "Answered SNMP but no CLI login succeeded; inventoried and "
                "crawled, kept off the backup schedule"
            )

        return assessment.auth_error or "Nothing answered on SSH, telnet or SNMP"

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def _devices(
        self,
        organization_id: int,
        device_ids: Optional[Sequence[int]],
        include_inactive: bool,
    ) -> List[Device]:
        statement = select(Device).where(Device.organization_id == organization_id)

        if device_ids:
            statement = statement.where(Device.id.in_(list(device_ids)))
        if not include_inactive:
            statement = statement.where(Device.is_active.is_(True))

        return list(self.db.scalars(statement.order_by(Device.hostname)).all())

    def _own_attempts(self, device: Device) -> List[vault.CredentialAttempt]:
        """The device's own login, as a last-resort attempt"""
        if not device.username or not device.encrypted_password:
            return []

        password = _safe_decrypt(device.encrypted_password)
        if not password:
            return []

        return [
            vault.CredentialAttempt(
                id=None,
                name=f"stored on {device.hostname}",
                kind=vault.CLI,
                username=device.username,
                password=password,
                enable_secret=_safe_decrypt(device.enable_secret),
                ssh_key_path=device.ssh_key_path,
            )
        ]

    def _own_snmp(self, device: Device) -> List[vault.CredentialAttempt]:
        """The device's own SNMP details, as a last-resort attempt"""
        if not device.snmp_version:
            return []

        return [
            vault.CredentialAttempt(
                id=None,
                name=f"stored on {device.hostname}",
                kind=vault.SNMP,
                snmp_version=device.snmp_version,
                community=_safe_decrypt(device.snmp_community),
                v3_user=device.snmp_v3_user,
                v3_auth_key=_safe_decrypt(device.snmp_v3_auth_key),
                v3_priv_key=_safe_decrypt(device.snmp_v3_priv_key),
                v3_auth_protocol=device.snmp_v3_auth_protocol,
                v3_priv_protocol=device.snmp_v3_priv_protocol,
            )
        ]

    def _oui_hint(self, device: Device) -> Optional[str]:
        """
        A platform guess from the device's MAC address vendor

        The inventory records a MAC per address seen on a switch port, so a
        managed device that is itself cabled to another switch usually appears
        there. The OUI gives a vendor name, and a vendor name is exactly what
        identify_platform already reads - "Cisco Systems, Inc" resolves the
        same way "cisco ios" does.

        Weak on its own: an HP-badged switch could be running ProCurve or
        Comware, and a vendor sells more than one CLI. So this is a hint, used
        to order the collection probes and to fill in a platform when nothing
        better answered - never to overrule the device's own account of itself.
        """
        vendor = self.db.scalars(
            select(HostInventory.vendor)
            .where(
                HostInventory.organization_id == device.organization_id,
                HostInventory.ip_address == device.ip_address,
                HostInventory.vendor.isnot(None),
            )
            .order_by(HostInventory.last_seen.desc())
            .limit(1)
        ).first()

        if not vendor:
            mac = self.db.scalars(
                select(HostInventory.mac_address)
                .where(
                    HostInventory.organization_id == device.organization_id,
                    HostInventory.ip_address == device.ip_address,
                )
                .order_by(HostInventory.last_seen.desc())
                .limit(1)
            ).first()

            if mac:
                vendor = oui_service.oui_lookup.lookup(mac, self.db)

        if not vendor:
            return None

        return probe.identify_platform(vendor)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _record_probes(
        self, organization_id: int, device_id: int, assessment
    ) -> None:
        """
        Store the latest outcome per transport

        Upserted on (device, transport), so the detail view shows the current
        state of each - "SSH refused, telnet timed out, 4 credentials tried" -
        rather than an ever-growing log.

        Each credential's success and failure counts are updated at the same
        time, which is what keeps the vault's ordering worth trusting.
        """
        if not assessment.probes:
            return

        rows = [
            {
                "organization_id": organization_id,
                "device_id": device_id,
                "transport": outcome.transport,
                "result": outcome.result,
                "credential_id": outcome.credential_id,
                "credential_name": outcome.credential_name,
                "attempts": outcome.attempts,
                "message": (outcome.message or "")[:2000],
                "duration": outcome.duration_ms,
                "probed_at": _now(),
            }
            for outcome in assessment.probes
        ]

        statement = pg_insert(DeviceProbe).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[DeviceProbe.device_id, DeviceProbe.transport],
            set_={
                "result": statement.excluded.result,
                "credential_id": statement.excluded.credential_id,
                "credential_name": statement.excluded.credential_name,
                "attempts": statement.excluded.attempts,
                "message": statement.excluded.message,
                "duration": statement.excluded.duration,
                "probed_at": statement.excluded.probed_at,
            },
        )
        self.db.execute(statement)

        for outcome in assessment.probes:
            if outcome.credential_id is not None:
                vault.record_outcome(
                    self.db, outcome.credential_id, outcome.ok, commit=False
                )


def _safe_decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a stored secret, treating a failure as absent"""
    if not value:
        return None

    from app.utils.encryption import encryption_service

    try:
        return encryption_service.decrypt(value)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not decrypt a stored secret: {e}")
        return None
