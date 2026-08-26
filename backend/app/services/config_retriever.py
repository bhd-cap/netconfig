"""
Configuration retrieval orchestration service
Coordinates the backup process: connect, retrieve, save, log
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Sequence
import logging

from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.device import Device
from app.repositories.device import DeviceRepository
from app.repositories.configuration import ConfigurationRepository
from app.repositories.audit_log import AuditLogRepository
from app.services.device_connector import DeviceConnector, DeviceConnectionError, DeviceCommandError
from app.services.storage import storage_service

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceSnapshot:
    """
    Plain copy of the device fields a backup needs

    ORM instances and their session are not thread safe, so the concurrent
    retrieval workers are handed one of these instead of a live Device.
    """

    id: int
    organization_id: int
    hostname: str
    ip_address: str
    device_type: str
    username: str
    encrypted_password: str
    port: int
    enable_secret: Optional[str]
    ssh_key_path: Optional[str]

    @classmethod
    def from_device(cls, device: Device) -> "DeviceSnapshot":
        return cls(
            id=device.id,
            organization_id=device.organization_id,
            hostname=device.hostname,
            ip_address=device.ip_address,
            device_type=device.device_type,
            username=device.username,
            encrypted_password=device.encrypted_password,
            port=device.port,
            enable_secret=device.enable_secret,
            ssh_key_path=device.ssh_key_path,
        )


@dataclass
class RetrievalOutcome:
    """Result of the network/disk half of a single device backup"""

    snapshot: DeviceSnapshot
    timestamp: datetime
    duration: float
    storage_result: Optional[dict] = None
    error: Optional[str] = None
    deduplicated: bool = False


class ConfigurationRetriever:
    """Orchestrates device configuration backup process"""

    def __init__(self, db: Session):
        """
        Initialize configuration retriever

        Args:
            db: Database session
        """
        self.db = db
        self.device_repo = DeviceRepository(db)
        self.config_repo = ConfigurationRepository(db)
        self.audit_repo = AuditLogRepository(db)

    # ------------------------------------------------------------------
    # Retrieval (no database access - safe to run on a worker thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _retrieve_config(snapshot: DeviceSnapshot) -> str:
        """
        Connect to a device and return its running configuration

        Args:
            snapshot: Device connection details

        Returns:
            str: Configuration text

        Raises:
            DeviceConnectionError, DeviceCommandError, ValueError
        """
        connector = DeviceConnector(
            hostname=snapshot.hostname,
            ip_address=snapshot.ip_address,
            device_type=snapshot.device_type,
            username=snapshot.username,
            encrypted_password=snapshot.encrypted_password,
            port=snapshot.port,
            enable_secret=snapshot.enable_secret,
            ssh_key_path=snapshot.ssh_key_path,
        )

        with connector:  # Uses context manager for auto-cleanup
            config_text = connector.get_running_config()

        if not config_text:
            raise ValueError("Retrieved configuration is empty")

        return config_text

    def _fetch_and_store(
        self, snapshot: DeviceSnapshot, previous_hash: Optional[str] = None
    ) -> RetrievalOutcome:
        """
        Retrieve a device's configuration and write it to storage

        Runs entirely off the database session so it can be executed
        concurrently for many devices.

        Args:
            snapshot: Device connection details
            previous_hash: Hash of the device's last successful backup, used to
                skip writing a file that would be byte-identical

        Returns:
            RetrievalOutcome: What happened, for the caller to persist
        """
        start = time.perf_counter()
        timestamp = datetime.utcnow()

        try:
            config_text = self._retrieve_config(snapshot)
            logger.info(
                f"Retrieved {len(config_text)} bytes from {snapshot.hostname}"
            )
        except (DeviceConnectionError, DeviceCommandError, ValueError) as e:
            return RetrievalOutcome(
                snapshot=snapshot,
                timestamp=timestamp,
                duration=time.perf_counter() - start,
                error=f"Failed to retrieve configuration: {str(e)}",
            )
        except Exception as e:  # noqa: BLE001 - a worker must never escape
            logger.exception(f"Unexpected error retrieving {snapshot.hostname}")
            return RetrievalOutcome(
                snapshot=snapshot,
                timestamp=timestamp,
                duration=time.perf_counter() - start,
                error=f"Unexpected error during backup: {str(e)}",
            )

        if settings.BACKUP_DEDUPLICATE and previous_hash:
            digest = storage_service.calculate_checksum(config_text)
            if digest == previous_hash:
                logger.info(
                    f"Configuration for {snapshot.hostname} is unchanged; "
                    f"skipping duplicate write"
                )
                return RetrievalOutcome(
                    snapshot=snapshot,
                    timestamp=timestamp,
                    duration=time.perf_counter() - start,
                    deduplicated=True,
                )

        try:
            storage_result = storage_service.save_config(
                organization_id=snapshot.organization_id,
                hostname=snapshot.hostname,
                config_text=config_text,
                timestamp=timestamp,
            )
        except Exception as e:
            return RetrievalOutcome(
                snapshot=snapshot,
                timestamp=timestamp,
                duration=time.perf_counter() - start,
                error=f"Failed to save configuration: {str(e)}",
            )

        return RetrievalOutcome(
            snapshot=snapshot,
            timestamp=timestamp,
            duration=time.perf_counter() - start,
            storage_result=storage_result,
        )

    # ------------------------------------------------------------------
    # Persistence (single-threaded, one transaction per batch)
    # ------------------------------------------------------------------

    def _persist(
        self, outcome: RetrievalOutcome, user_id: Optional[int], commit: bool = True
    ) -> Dict[str, Any]:
        """
        Write the database records for one completed retrieval

        Args:
            outcome: Result of the retrieval
            user_id: User ID triggering the backup
            commit: Commit at the end. Bulk callers pass False and commit once
                for the whole batch instead of three times per device.

        Returns:
            Dict: Backup result for the API/task response
        """
        snapshot = outcome.snapshot
        result = {
            "success": False,
            "device_id": snapshot.id,
            "device_hostname": snapshot.hostname,
            "configuration_id": None,
            "message": "",
            "duration": round(outcome.duration, 2),
            "file_size": 0,
            "deduplicated": outcome.deduplicated,
        }

        if outcome.error:
            result["message"] = outcome.error
            logger.error(f"{snapshot.hostname}: {outcome.error}")

            self._log_backup_failure(snapshot, user_id, outcome.error, commit=False)
            self.device_repo.update_last_backup(
                device_id=snapshot.id,
                status="failed",
                timestamp=outcome.timestamp,
                commit=False,
            )

            if commit:
                self.db.commit()

            return result

        if outcome.deduplicated:
            # Nothing new on disk, so nothing new in the configurations table;
            # the device's backup status is still current as of now.
            self.device_repo.update_last_backup(
                device_id=snapshot.id,
                status="success",
                timestamp=outcome.timestamp,
                commit=False,
            )

            if commit:
                self.db.commit()

            result["success"] = True
            result["message"] = (
                f"{snapshot.hostname} configuration unchanged since last backup"
            )
            return result

        storage_result = outcome.storage_result

        try:
            config_record = self.config_repo.create(
                {
                    "device_id": snapshot.id,
                    "filename": storage_result["filename"],
                    "file_path": storage_result["file_path"],
                    "file_size": storage_result["file_size"],
                    "checksum": storage_result["checksum"],
                    "backed_up_at": outcome.timestamp,
                    "backup_duration": outcome.duration,
                    "status": "success",
                    "config_hash": storage_result["config_hash"],
                },
                commit=False,
            )
        except Exception:
            # Let the caller decide how to unwind: a single backup rolls the
            # whole transaction back, a batch only its own savepoint.
            logger.exception(
                f"Failed to create configuration record for {snapshot.hostname}"
            )
            raise

        self.device_repo.update_last_backup(
            device_id=snapshot.id,
            status="success",
            timestamp=outcome.timestamp,
            commit=False,
        )

        self.audit_repo.log_action(
            user_id=user_id,
            action="device_backup_success",
            resource_type="configuration",
            resource_id=config_record.id,
            details={
                "device_id": snapshot.id,
                "device_hostname": snapshot.hostname,
                "file_size": storage_result["file_size"],
                "duration": round(outcome.duration, 2),
            },
            commit=False,
        )

        if commit:
            self.db.commit()

        result.update(
            {
                "success": True,
                "configuration_id": config_record.id,
                "file_size": storage_result["file_size"],
                "message": f"Successfully backed up {snapshot.hostname}",
            }
        )

        logger.info(
            f"Backup completed for {snapshot.hostname}: "
            f"{storage_result['file_size']} bytes in {outcome.duration:.2f}s"
        )

        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def backup_device(
        self,
        device_id: int,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Backup a single device configuration

        Args:
            device_id: Device ID to backup
            user_id: User ID triggering the backup (optional)

        Returns:
            Dict: Backup result with status, message, and configuration info
        """
        device = self.device_repo.get(device_id)
        if not device:
            message = f"Device with ID {device_id} not found"
            logger.error(message)
            return {
                "success": False,
                "device_id": device_id,
                "device_hostname": None,
                "configuration_id": None,
                "message": message,
                "duration": 0,
                "file_size": 0,
            }

        snapshot = DeviceSnapshot.from_device(device)
        logger.info(f"Starting backup for device: {snapshot.hostname} (ID: {device_id})")

        previous_hash = (
            self.config_repo.get_latest_hash(device_id)
            if settings.BACKUP_DEDUPLICATE
            else None
        )

        outcome = self._fetch_and_store(snapshot, previous_hash)

        try:
            return self._persist(outcome, user_id)
        except Exception as e:
            self.db.rollback()
            self._log_backup_failure(snapshot, user_id, str(e))
            return {
                "success": False,
                "device_id": device_id,
                "device_hostname": snapshot.hostname,
                "configuration_id": None,
                "message": f"Failed to create database record: {str(e)}",
                "duration": round(outcome.duration, 2),
                "file_size": 0,
            }

    def backup_multiple_devices(
        self,
        device_ids: Sequence[int],
        user_id: Optional[int] = None,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Backup multiple devices concurrently

        A backup is almost entirely waiting on an SSH session, so devices are
        retrieved in parallel while all database writes stay on this thread and
        land in one transaction. Previously this ran strictly one device at a
        time, making a bulk backup take the sum of every device's SSH latency.

        Args:
            device_ids: List of device IDs to backup
            user_id: User ID triggering the backups
            max_workers: Concurrent SSH sessions (default MAX_CONCURRENT_BACKUPS)

        Returns:
            Dict: Summary of backup results
        """
        device_ids = list(dict.fromkeys(device_ids))  # de-duplicate, keep order

        results = {
            "total": len(device_ids),
            "successful": 0,
            "failed": 0,
            "unchanged": 0,
            "devices": [],
        }

        if not device_ids:
            return results

        # One query for the devices, one for their previous hashes.
        devices = {d.id: d for d in self.device_repo.get_many(device_ids)}
        snapshots = [
            DeviceSnapshot.from_device(devices[device_id])
            for device_id in device_ids
            if device_id in devices
        ]

        for device_id in device_ids:
            if device_id not in devices:
                results["failed"] += 1
                results["devices"].append(
                    {
                        "device_id": device_id,
                        "hostname": None,
                        "success": False,
                        "message": f"Device with ID {device_id} not found",
                        "duration": 0,
                    }
                )

        if not snapshots:
            return results

        previous_hashes = (
            self.config_repo.get_latest_hashes([s.id for s in snapshots])
            if settings.BACKUP_DEDUPLICATE
            else {}
        )

        workers = max_workers or settings.MAX_CONCURRENT_BACKUPS
        workers = max(1, min(workers, len(snapshots)))

        logger.info(
            f"Starting bulk backup for {len(snapshots)} devices "
            f"({workers} concurrent)"
        )

        outcomes: List[RetrievalOutcome] = []

        if workers == 1:
            outcomes = [
                self._fetch_and_store(s, previous_hashes.get(s.id)) for s in snapshots
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="backup"
            ) as pool:
                futures = [
                    pool.submit(self._fetch_and_store, s, previous_hashes.get(s.id))
                    for s in snapshots
                ]

                for future in as_completed(futures):
                    outcomes.append(future.result())

        # Persist every outcome in a single transaction. Each device gets a
        # savepoint so one bad row cannot discard the whole batch's work.
        for outcome in outcomes:
            try:
                with self.db.begin_nested():
                    device_result = self._persist(outcome, user_id, commit=False)
            except Exception as e:
                device_result = {
                    "success": False,
                    "device_id": outcome.snapshot.id,
                    "device_hostname": outcome.snapshot.hostname,
                    "message": f"Failed to create database record: {str(e)}",
                    "duration": round(outcome.duration, 2),
                }

            if device_result["success"]:
                results["successful"] += 1
                if device_result.get("deduplicated"):
                    results["unchanged"] += 1
            else:
                results["failed"] += 1

            results["devices"].append(
                {
                    "device_id": device_result["device_id"],
                    "hostname": device_result["device_hostname"],
                    "success": device_result["success"],
                    "message": device_result["message"],
                    "duration": device_result["duration"],
                }
            )

        self.audit_repo.log_action(
            user_id=user_id,
            action="bulk_backup_completed",
            resource_type="device",
            details={
                "total": results["total"],
                "successful": results["successful"],
                "failed": results["failed"],
                "unchanged": results["unchanged"],
            },
            commit=False,
        )

        self.db.commit()

        logger.info(
            f"Bulk backup completed: {results['successful']}/{results['total']} successful"
        )

        return results

    def apply_retention_policy(
        self,
        device_id: int,
        keep_count: Optional[int] = None,
    ) -> int:
        """
        Apply retention policy to device configurations

        Args:
            device_id: Device ID
            keep_count: Number of configs to keep (uses default if None)

        Returns:
            int: Number of configurations deleted
        """
        if keep_count is None:
            # Convert retention days to approximate config count (1 per day)
            keep_count = settings.DEFAULT_RETENTION_DAYS

        logger.debug(
            f"Applying retention policy to device {device_id}: keeping {keep_count} configs"
        )

        # The delete returns the paths it removed, so the files can be unlinked
        # without listing the device's directory again.
        deleted_paths = self.config_repo.delete_old_configs(device_id, keep_count)

        for file_path in deleted_paths:
            storage_service.delete_file(file_path)

        if deleted_paths:
            logger.info(
                f"Deleted {len(deleted_paths)} old configurations for device {device_id}"
            )

        return len(deleted_paths)

    def _log_backup_failure(
        self,
        snapshot: DeviceSnapshot,
        user_id: Optional[int],
        error_message: str,
        commit: bool = True,
    ):
        """
        Log backup failure to audit log

        Args:
            snapshot: Device that failed
            user_id: User ID (if applicable)
            error_message: Error message
            commit: Commit immediately
        """
        try:
            self.audit_repo.log_action(
                user_id=user_id,
                action="device_backup_failed",
                resource_type="device",
                resource_id=snapshot.id,
                details={
                    "device_id": snapshot.id,
                    "device_hostname": snapshot.hostname,
                },
                status="failed",
                error_message=error_message,
                commit=commit,
            )
        except Exception as e:
            logger.error(f"Failed to log backup failure: {e}")
