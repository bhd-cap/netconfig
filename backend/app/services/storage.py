"""
Configuration storage service for managing backup files
"""
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Exception raised for storage errors"""
    pass


class ConfigurationStorage:
    """Manages configuration file storage on filesystem"""

    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize storage service

        Args:
            base_path: Base directory for backups (default from settings)
        """
        self.base_path = Path(base_path or settings.BACKUP_BASE_PATH)

        # Ensure base path exists
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Storage initialized at {self.base_path}")
        except Exception as e:
            logger.error(f"Failed to create storage directory: {str(e)}")
            raise StorageError(f"Storage initialization failed: {str(e)}")

    def get_device_directory(self, organization_id: int, hostname: str) -> Path:
        """
        Get the storage directory for a specific device

        Args:
            organization_id: Organization ID for multi-tenant isolation
            hostname: Device hostname

        Returns:
            Path: Device-specific directory path
        """
        # Structure: /backups/{org_id}/{hostname}/
        device_dir = self.base_path / str(organization_id) / hostname
        device_dir.mkdir(parents=True, exist_ok=True)
        return device_dir

    def generate_filename(self, hostname: str, timestamp: Optional[datetime] = None) -> str:
        """
        Generate filename for configuration backup

        Args:
            hostname: Device hostname
            timestamp: Backup timestamp (default: now)

        Returns:
            str: Generated filename
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Format: hostname_YYYYMMDD_HHMMSS.cfg
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
        return f"{hostname}_{timestamp_str}.cfg"

    def save_config(
        self,
        organization_id: int,
        hostname: str,
        config_text: str,
        timestamp: Optional[datetime] = None,
    ) -> dict:
        """
        Save configuration to filesystem

        Args:
            organization_id: Organization ID
            hostname: Device hostname
            config_text: Configuration content
            timestamp: Backup timestamp (default: now)

        Returns:
            dict: Storage metadata (path, size, checksum, etc.)

        Raises:
            StorageError: If save operation fails
        """
        try:
            # Get device directory
            device_dir = self.get_device_directory(organization_id, hostname)

            # Generate filename
            filename = self.generate_filename(hostname, timestamp)
            file_path = device_dir / filename

            # Write configuration to file
            logger.info(f"Saving configuration to {file_path}")
            file_path.write_text(config_text, encoding="utf-8")

            # Calculate metadata
            file_size = file_path.stat().st_size
            checksum = self.calculate_checksum(config_text)
            config_hash = self.calculate_config_hash(config_text)

            logger.info(
                f"Saved configuration for {hostname}: "
                f"{file_size} bytes, checksum={checksum[:8]}..."
            )

            return {
                "filename": filename,
                "file_path": str(file_path),
                "file_size": file_size,
                "checksum": checksum,
                "config_hash": config_hash,
            }

        except Exception as e:
            error_msg = f"Failed to save configuration for {hostname}: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    def get_config(self, file_path: str) -> str:
        """
        Retrieve configuration content from file

        Args:
            file_path: Path to configuration file

        Returns:
            str: Configuration content

        Raises:
            StorageError: If file not found or read fails
        """
        try:
            path = Path(file_path)
            if not path.exists():
                raise StorageError(f"Configuration file not found: {file_path}")

            logger.info(f"Reading configuration from {file_path}")
            return path.read_text(encoding="utf-8")

        except Exception as e:
            error_msg = f"Failed to read configuration: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    def list_configs(self, organization_id: int, hostname: str) -> List[Path]:
        """
        List all configuration files for a device

        Args:
            organization_id: Organization ID
            hostname: Device hostname

        Returns:
            List[Path]: List of configuration file paths, sorted by modification time (newest first)
        """
        device_dir = self.get_device_directory(organization_id, hostname)

        if not device_dir.exists():
            return []

        # Get all .cfg files and sort by modification time (newest first)
        configs = list(device_dir.glob("*.cfg"))
        configs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        return configs

    def calculate_checksum(self, content: str) -> str:
        """
        Calculate SHA256 checksum of content

        Args:
            content: Content to hash

        Returns:
            str: SHA256 checksum (hex)
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def calculate_config_hash(self, content: str) -> str:
        """
        Calculate hash for configuration deduplication

        This removes whitespace and comments before hashing to detect
        functional config changes vs formatting changes

        Args:
            content: Configuration content

        Returns:
            str: Configuration hash
        """
        # Simple implementation - can be enhanced with better normalization
        # For now, just use the full content hash
        return self.calculate_checksum(content)

    def apply_retention_policy(
        self,
        organization_id: int,
        hostname: str,
        keep_count: Optional[int] = None,
    ) -> int:
        """
        Apply retention policy to delete old configurations

        Args:
            organization_id: Organization ID
            hostname: Device hostname
            keep_count: Number of configurations to keep (default from settings)

        Returns:
            int: Number of files deleted

        Raises:
            StorageError: If deletion fails
        """
        if keep_count is None:
            # Calculate keep_count from retention days (assume 1 backup per day)
            keep_count = settings.DEFAULT_RETENTION_DAYS

        try:
            configs = self.list_configs(organization_id, hostname)

            if len(configs) <= keep_count:
                logger.info(
                    f"Retention policy check for {hostname}: "
                    f"{len(configs)} configs, keeping {keep_count} - no deletion needed"
                )
                return 0

            # Delete oldest configs beyond keep_count
            to_delete = configs[keep_count:]
            deleted_count = 0

            for config_path in to_delete:
                try:
                    config_path.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old configuration: {config_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete {config_path}: {str(e)}")

            logger.info(
                f"Retention policy applied to {hostname}: "
                f"deleted {deleted_count} of {len(to_delete)} old configs"
            )

            return deleted_count

        except Exception as e:
            error_msg = f"Failed to apply retention policy for {hostname}: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    def get_storage_stats(self) -> dict:
        """
        Get storage statistics

        Returns:
            dict: Storage statistics (total size, file count, etc.)
        """
        total_size = 0
        total_files = 0

        try:
            for file_path in self.base_path.rglob("*.cfg"):
                total_size += file_path.stat().st_size
                total_files += 1

            return {
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "total_files": total_files,
                "base_path": str(self.base_path),
            }

        except Exception as e:
            logger.error(f"Failed to get storage stats: {str(e)}")
            return {
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "total_files": 0,
                "base_path": str(self.base_path),
                "error": str(e),
            }


# Global storage instance
storage_service = ConfigurationStorage()
