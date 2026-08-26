"""
Configuration retrieval orchestration service
Coordinates the backup process: connect, retrieve, save, log
"""
import time
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from sqlalchemy.orm import Session
from app.models.device import Device
from app.models.configuration import Configuration
from app.repositories.device import DeviceRepository
from app.repositories.configuration import ConfigurationRepository
from app.repositories.audit_log import AuditLogRepository
from app.services.device_connector import DeviceConnector, DeviceConnectionError, DeviceCommandError
from app.services.storage import storage_service

logger = logging.getLogger(__name__)


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
        start_time = time.time()
        result = {
            "success": False,
            "device_id": device_id,
            "device_hostname": None,
            "configuration_id": None,
            "message": "",
            "duration": 0,
            "file_size": 0,
        }

        try:
            # Get device
            device = self.device_repo.get(device_id)
            if not device:
                result["message"] = f"Device with ID {device_id} not found"
                logger.error(result["message"])
                return result

            result["device_hostname"] = device.hostname
            logger.info(f"Starting backup for device: {device.hostname} (ID: {device_id})")

            # Create device connector
            connector = DeviceConnector(
                hostname=device.hostname,
                ip_address=device.ip_address,
                device_type=device.device_type,
                username=device.username,
                encrypted_password=device.encrypted_password,
                port=device.port,
                enable_secret=device.enable_secret,
                ssh_key_path=device.ssh_key_path,
            )

            # Connect and retrieve configuration
            config_text = None
            try:
                with connector:  # Uses context manager for auto-cleanup
                    config_text = connector.get_running_config()

                if not config_text:
                    raise ValueError("Retrieved configuration is empty")

                logger.info(f"Retrieved {len(config_text)} bytes from {device.hostname}")

            except (DeviceConnectionError, DeviceCommandError) as e:
                result["message"] = f"Failed to retrieve configuration: {str(e)}"
                logger.error(result["message"])

                # Log failure
                self._log_backup_failure(device, user_id, str(e))

                # Update device last backup status
                self.device_repo.update_last_backup(
                    device_id=device_id,
                    status="failed",
                    timestamp=datetime.utcnow(),
                )

                return result

            # Save configuration to storage
            backup_timestamp = datetime.utcnow()
            try:
                storage_result = storage_service.save_config(
                    organization_id=device.organization_id,
                    hostname=device.hostname,
                    config_text=config_text,
                    timestamp=backup_timestamp,
                )

                logger.info(
                    f"Saved configuration to: {storage_result['file_path']} "
                    f"({storage_result['file_size']} bytes)"
                )

            except Exception as e:
                result["message"] = f"Failed to save configuration: {str(e)}"
                logger.error(result["message"])

                # Log failure
                self._log_backup_failure(device, user_id, str(e))

                return result

            # Save configuration record to database
            duration = time.time() - start_time
            try:
                config_record = self.config_repo.create({
                    "device_id": device_id,
                    "filename": storage_result["filename"],
                    "file_path": storage_result["file_path"],
                    "file_size": storage_result["file_size"],
                    "checksum": storage_result["checksum"],
                    "backed_up_at": backup_timestamp,
                    "backup_duration": duration,
                    "status": "success",
                    "config_hash": storage_result["config_hash"],
                })

                logger.info(
                    f"Created configuration record (ID: {config_record.id}) "
                    f"for {device.hostname}"
                )

                result["configuration_id"] = config_record.id
                result["file_size"] = storage_result["file_size"]

            except Exception as e:
                result["message"] = f"Failed to create database record: {str(e)}"
                logger.error(result["message"])

                # Log failure
                self._log_backup_failure(device, user_id, str(e))

                return result

            # Update device last backup info
            self.device_repo.update_last_backup(
                device_id=device_id,
                status="success",
                timestamp=backup_timestamp,
            )

            # Log successful backup
            self.audit_repo.log_action(
                user_id=user_id,
                action="device_backup_success",
                resource_type="configuration",
                resource_id=config_record.id,
                details={
                    "device_id": device_id,
                    "device_hostname": device.hostname,
                    "file_size": storage_result["file_size"],
                    "duration": round(duration, 2),
                },
            )

            # Success!
            result["success"] = True
            result["duration"] = round(duration, 2)
            result["message"] = f"Successfully backed up {device.hostname}"

            logger.info(
                f"Backup completed for {device.hostname}: "
                f"{storage_result['file_size']} bytes in {duration:.2f}s"
            )

        except Exception as e:
            result["message"] = f"Unexpected error during backup: {str(e)}"
            logger.exception(f"Unexpected error backing up device {device_id}")

            # Try to log failure
            try:
                if result["device_hostname"]:
                    self._log_backup_failure(device, user_id, str(e))
            except Exception:
                pass  # Don't fail if logging fails

        finally:
            result["duration"] = round(time.time() - start_time, 2)

        return result

    def backup_multiple_devices(
        self,
        device_ids: list[int],
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Backup multiple devices sequentially

        Args:
            device_ids: List of device IDs to backup
            user_id: User ID triggering the backups

        Returns:
            Dict: Summary of backup results
        """
        logger.info(f"Starting bulk backup for {len(device_ids)} devices")

        results = {
            "total": len(device_ids),
            "successful": 0,
            "failed": 0,
            "devices": [],
        }

        for device_id in device_ids:
            backup_result = self.backup_device(device_id, user_id)

            if backup_result["success"]:
                results["successful"] += 1
            else:
                results["failed"] += 1

            results["devices"].append({
                "device_id": device_id,
                "hostname": backup_result["device_hostname"],
                "success": backup_result["success"],
                "message": backup_result["message"],
                "duration": backup_result["duration"],
            })

        # Log bulk backup completion
        self.audit_repo.log_action(
            user_id=user_id,
            action="bulk_backup_completed",
            resource_type="device",
            details={
                "total": results["total"],
                "successful": results["successful"],
                "failed": results["failed"],
            },
        )

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
        from app.core.config import settings

        if keep_count is None:
            # Convert retention days to approximate config count (1 per day)
            keep_count = settings.DEFAULT_RETENTION_DAYS

        logger.info(f"Applying retention policy to device {device_id}: keeping {keep_count} configs")

        # Delete from database
        deleted_count = self.config_repo.delete_old_configs(device_id, keep_count)

        if deleted_count > 0:
            # Also clean up files from storage
            device = self.device_repo.get(device_id)
            if device:
                storage_service.apply_retention_policy(
                    organization_id=device.organization_id,
                    hostname=device.hostname,
                    keep_count=keep_count,
                )

            logger.info(f"Deleted {deleted_count} old configurations for device {device_id}")

        return deleted_count

    def _log_backup_failure(
        self,
        device: Device,
        user_id: Optional[int],
        error_message: str,
    ):
        """
        Log backup failure to audit log

        Args:
            device: Device that failed
            user_id: User ID (if applicable)
            error_message: Error message
        """
        try:
            self.audit_repo.log_action(
                user_id=user_id,
                action="device_backup_failed",
                resource_type="device",
                resource_id=device.id,
                details={
                    "device_id": device.id,
                    "device_hostname": device.hostname,
                },
                status="failed",
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"Failed to log backup failure: {e}")
