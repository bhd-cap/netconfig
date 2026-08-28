"""
Polling devices for hardware inventory and environmental readings, and storing
what comes back.

The SNMP half lives in ``snmp_inventory``; this is the database half. Kept
apart so the parsing can be tested against captured MIB output with no session
in sight, and so this can be read without knowing what an entPhysicalIndex is.

Two rules worth stating:

- **Components and sensors are aged, not deleted.** A power supply that stops
  being reported has been removed, and the serial number of the one that used
  to be in that slot is precisely what an inventory is for. Same for a sensor:
  a module pulled out takes its temperature sensor with it, and the last
  reading before it went is worth keeping.
- **History is only written when there is a value.** A power supply reporting
  "failed" and no number is a row in device_sensors and nothing in the chart
  table, because a chart of nulls is noise.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.telemetry import DeviceComponent, DeviceSensor, SensorReading
from app.services import credentials as vault
from app.services import snmp_inventory
from app.services.device_connector import snmp_params

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DevicePollOutcome:
    """What one device's poll produced"""

    device_id: int
    hostname: str
    components: int = 0
    sensors: int = 0
    sources: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "hostname": self.hostname,
            "components": self.components,
            "sensors": self.sensors,
            "sources": self.sources,
            "error": self.error,
        }


@dataclass
class PollSummary:
    """The result of one polling pass"""

    polled: int = 0
    answered: int = 0
    failed: int = 0
    components: int = 0
    sensors: int = 0
    devices: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "polled": self.polled,
            "answered": self.answered,
            "failed": self.failed,
            "components": self.components,
            "sensors": self.sensors,
            "devices": self.devices,
        }


class TelemetryService:
    """Polls devices over SNMP and stores their hardware and readings"""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(
        self,
        organization_id: int,
        device_ids: Optional[Sequence[int]] = None,
        keep_history: bool = True,
    ) -> PollSummary:
        """
        Poll devices for hardware inventory and readings

        Args:
            organization_id: Tenant scope
            device_ids: Devices to poll; every device with SNMP configured
                when omitted
            keep_history: Write a row per numeric reading for the trend charts

        Returns:
            PollSummary
        """
        devices = self._pollable(organization_id, device_ids)
        summary = PollSummary()

        for device in devices:
            outcome = self._poll_one(organization_id, device, keep_history)

            summary.polled += 1
            if outcome.error:
                summary.failed += 1
            else:
                summary.answered += 1
                summary.components += outcome.components
                summary.sensors += outcome.sensors

            summary.devices.append(outcome.as_dict())

        self.db.commit()
        return summary

    def _poll_one(
        self, organization_id: int, device: Device, keep_history: bool
    ) -> DevicePollOutcome:
        """
        Poll one device

        Never raises: one device that will not answer must not abandon a
        sweep, so a failure becomes an outcome with an error on it.
        """
        outcome = DevicePollOutcome(device_id=device.id, hostname=device.hostname)

        try:
            client = self._client_for(device)
        except Exception as e:  # noqa: BLE001
            outcome.error = f"{type(e).__name__}: {e}"
            return outcome

        if client is None:
            outcome.error = "No SNMP credentials configured for this device"
            return outcome

        result = snmp_inventory.poll(client)

        if result.error:
            outcome.error = result.error
            return outcome

        if not result.components and not result.readings:
            outcome.error = "SNMP answered but reported no hardware or sensors"
            return outcome

        outcome.sources = result.sources
        outcome.components = self._store_components(
            organization_id, device.id, result.components
        )
        outcome.sensors = self._store_readings(
            organization_id, device.id, result.readings, keep_history
        )

        device.snmp_last_polled_at = _now()

        return outcome

    def _client_for(self, device: Device):
        """
        Build an SNMP client for a device, or None when it has no credentials

        Goes through the credential vault the same way every other connection
        path does, so a device pointed at a shared community is polled with it
        rather than with whatever is stored on the row.
        """
        from app.services.snmp_client import SnmpClient

        login = vault.resolve_for_device(self.db, device)
        params = login.snmp or snmp_params(device)

        if not params.get("version"):
            return None

        community = params.get("community")
        v3_user = params.get("v3_user")
        if not community and not v3_user:
            return None

        def decrypt(value):
            if not value:
                return None
            from app.utils.encryption import encryption_service

            try:
                return encryption_service.decrypt(value)
            except Exception:  # noqa: BLE001 - treat an unreadable secret as absent
                logger.error(f"Could not decrypt an SNMP secret for {device.hostname}")
                return None

        return SnmpClient(
            host=device.ip_address,
            port=params.get("port") or 161,
            version=params.get("version") or "2c",
            community=decrypt(community),
            v3_user=v3_user,
            v3_auth_key=decrypt(params.get("v3_auth_key")),
            v3_priv_key=decrypt(params.get("v3_priv_key")),
            v3_auth_protocol=params.get("v3_auth_protocol"),
            v3_priv_protocol=params.get("v3_priv_protocol"),
        )

    def _pollable(
        self, organization_id: int, device_ids: Optional[Sequence[int]]
    ) -> List[Device]:
        """
        The devices worth polling

        A device with no SNMP version configured and no vault SNMP credential
        has nothing to answer with, and walking it would cost a full timeout
        per table for nothing.
        """
        statement = select(Device).where(Device.organization_id == organization_id)

        if device_ids:
            statement = statement.where(Device.id.in_(list(device_ids)))
        else:
            statement = statement.where(
                (Device.snmp_version.isnot(None))
                | (Device.snmp_credential_id.isnot(None))
            )

        return list(self.db.scalars(statement.order_by(Device.hostname)).all())

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _store_components(
        self,
        organization_id: int,
        device_id: int,
        components: Sequence[snmp_inventory.Component],
    ) -> int:
        """Upsert the hardware, and age anything that stopped being reported"""
        seen = _now()

        if components:
            rows = [
                {
                    "organization_id": organization_id,
                    "device_id": device_id,
                    **component.as_dict(),
                    "is_active": True,
                    "first_seen": seen,
                    "last_seen": seen,
                }
                for component in components
            ]

            statement = pg_insert(DeviceComponent).values(rows)
            statement = statement.on_conflict_do_update(
                constraint="uq_component_device_index",
                set_={
                    "name": statement.excluded.name,
                    "description": statement.excluded.description,
                    "component_class": statement.excluded.component_class,
                    "model_name": statement.excluded.model_name,
                    "serial_number": statement.excluded.serial_number,
                    "hardware_rev": statement.excluded.hardware_rev,
                    "firmware_rev": statement.excluded.firmware_rev,
                    "software_rev": statement.excluded.software_rev,
                    "parent_index": statement.excluded.parent_index,
                    "is_active": True,
                    "last_seen": seen,
                },
            )
            self.db.execute(statement)

        # Anything not in this poll has been removed from the device. Marked
        # inactive rather than deleted: the serial number of the part that used
        # to be in that slot is the point of keeping an inventory.
        indexes = [component.index for component in components]
        stale = (
            update(DeviceComponent)
            .where(
                DeviceComponent.device_id == device_id,
                DeviceComponent.is_active.is_(True),
            )
            .values(is_active=False)
        )
        if indexes:
            stale = stale.where(DeviceComponent.entity_index.notin_(indexes))
        self.db.execute(stale)

        return len(components)

    def _store_readings(
        self,
        organization_id: int,
        device_id: int,
        readings: Sequence[snmp_inventory.Reading],
        keep_history: bool,
    ) -> int:
        """Upsert the current readings and, optionally, add to the history"""
        if not readings:
            return 0

        seen = _now()
        rows = [
            {
                "organization_id": organization_id,
                "device_id": device_id,
                **reading.as_dict(),
                "is_active": True,
                "first_seen": seen,
                "last_reading_at": seen,
            }
            for reading in readings
        ]

        statement = pg_insert(DeviceSensor).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_sensor_device_key",
            set_={
                "name": statement.excluded.name,
                "sensor_type": statement.excluded.sensor_type,
                "unit": statement.excluded.unit,
                "value": statement.excluded.value,
                "status": statement.excluded.status,
                "source": statement.excluded.source,
                "is_active": True,
                "last_reading_at": seen,
            },
        )
        self.db.execute(statement)

        keys = [reading.key for reading in readings]
        self.db.execute(
            update(DeviceSensor)
            .where(
                DeviceSensor.device_id == device_id,
                DeviceSensor.is_active.is_(True),
                DeviceSensor.sensor_key.notin_(keys),
            )
            .values(is_active=False)
        )

        if keep_history:
            self._append_history(device_id, readings, seen)

        return len(readings)

    def _append_history(
        self,
        device_id: int,
        readings: Sequence[snmp_inventory.Reading],
        recorded_at: datetime,
    ) -> None:
        """
        Add one history row per numeric reading

        A sensor with no number - a power supply reporting only "failed" -
        contributes nothing here: a chart of nulls is noise, and its state is
        already on the sensor row.
        """
        numeric = {
            reading.key: reading for reading in readings if reading.value is not None
        }
        if not numeric:
            return

        # One query for the ids, rather than one per reading.
        ids = dict(
            self.db.execute(
                select(DeviceSensor.sensor_key, DeviceSensor.id).where(
                    DeviceSensor.device_id == device_id,
                    DeviceSensor.sensor_key.in_(list(numeric)),
                )
            ).all()
        )

        rows = [
            {
                "sensor_id": ids[key],
                "value": reading.value,
                "status": reading.status,
                "recorded_at": recorded_at,
            }
            for key, reading in numeric.items()
            if key in ids
        ]

        if rows:
            self.db.execute(pg_insert(SensorReading).values(rows))

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def prune_history(self, older_than_days: int = 30) -> int:
        """
        Drop readings past the retention window

        sensor_readings is the only table here that grows without bound: every
        sensor on every device, every poll. At a poll every thirty minutes a
        single switch with twenty sensors writes about a million rows a year.

        Args:
            older_than_days: Keep this many days of history

        Returns:
            Rows deleted
        """
        cutoff = _now() - timedelta(days=older_than_days)

        result = self.db.execute(
            delete(SensorReading).where(SensorReading.recorded_at < cutoff)
        )
        self.db.commit()

        return result.rowcount or 0
