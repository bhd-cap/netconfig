"""
Configuration storage service for managing backup files
"""
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, List
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

# Read/hash files in chunks so a large configuration never has to exist in
# memory more than a block at a time.
_CHUNK_SIZE = 1 << 20  # 1 MiB


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

    def get_device_directory(
        self, organization_id: int, hostname: str, create: bool = True
    ) -> Path:
        """
        Get the storage directory for a specific device

        Args:
            organization_id: Organization ID for multi-tenant isolation
            hostname: Device hostname
            create: Create the directory if missing. Read-only callers pass
                False so that listing a device's backups does not have the
                side effect of creating directories.

        Returns:
            Path: Device-specific directory path
        """
        # Structure: /backups/{org_id}/{hostname}/
        device_dir = self.base_path / str(organization_id) / hostname

        if create:
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

            # Encode once and reuse. The previous implementation encoded the
            # configuration three times (write_text, checksum, config hash)
            # and then stat()ed the file for a size it already knew.
            payload = config_text.encode("utf-8")
            checksum = hashlib.sha256(payload).hexdigest()
            file_size = len(payload)

            logger.info(f"Saving configuration to {file_path}")
            self._write_atomic(file_path, payload)

            logger.info(
                f"Saved configuration for {hostname}: "
                f"{file_size} bytes, checksum={checksum[:8]}..."
            )

            return {
                "filename": filename,
                "file_path": str(file_path),
                "file_size": file_size,
                "checksum": checksum,
                "config_hash": checksum,
            }

        except Exception as e:
            error_msg = f"Failed to save configuration for {hostname}: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    @staticmethod
    def _write_atomic(file_path: Path, payload: bytes) -> None:
        """
        Write bytes to a path atomically

        A crash mid-write would otherwise leave a truncated backup that looks
        valid to every later reader.

        Args:
            file_path: Destination path
            payload: Bytes to write
        """
        tmp_path = file_path.with_name(f".{file_path.name}.tmp")

        try:
            with open(tmp_path, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(tmp_path, file_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

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
        path = Path(file_path)

        try:
            logger.debug(f"Reading configuration from {file_path}")
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise StorageError(f"Configuration file not found: {file_path}")
        except Exception as e:
            error_msg = f"Failed to read configuration: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    def read_lines(self, file_path: str, max_bytes: Optional[int] = None) -> List[str]:
        """
        Read a configuration as a list of lines

        Reads line by line rather than loading the whole file and then
        splitting it, which would hold the text and the split copy at once.

        Args:
            file_path: Path to configuration file
            max_bytes: Refuse files larger than this

        Returns:
            List[str]: Lines, newlines retained

        Raises:
            StorageError: If the file is missing, unreadable or too large
        """
        path = Path(file_path)

        try:
            if max_bytes is not None:
                size = path.stat().st_size
                if size > max_bytes:
                    raise StorageError(
                        f"Configuration file is too large to process "
                        f"({size} bytes > {max_bytes} byte limit): {file_path}"
                    )

            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.readlines()

        except StorageError:
            raise
        except FileNotFoundError:
            raise StorageError(f"Configuration file not found: {file_path}")
        except Exception as e:
            error_msg = f"Failed to read configuration: {str(e)}"
            logger.error(error_msg)
            raise StorageError(error_msg)

    def exists(self, file_path: str) -> bool:
        """
        Check whether a stored configuration file is present

        Args:
            file_path: Path to configuration file

        Returns:
            bool: True if the path is an existing file
        """
        try:
            return Path(file_path).is_file()
        except OSError:
            return False

    def delete_file(self, file_path: str) -> bool:
        """
        Delete a stored configuration file

        Args:
            file_path: Path to configuration file

        Returns:
            bool: True if a file was removed
        """
        try:
            Path(file_path).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.warning(f"Failed to delete {file_path}: {e}")
            return False

    def list_configs(self, organization_id: int, hostname: str) -> List[Path]:
        """
        List all configuration files for a device

        Args:
            organization_id: Organization ID
            hostname: Device hostname

        Returns:
            List[Path]: List of configuration file paths, sorted by modification time (newest first)
        """
        device_dir = self.get_device_directory(organization_id, hostname, create=False)

        # scandir exposes the stat data the directory read already returned, so
        # sorting by mtime costs no extra syscall per file. The previous
        # glob + p.stat() sort issued one stat() per file, twice over.
        try:
            with os.scandir(device_dir) as entries:
                configs = [
                    (entry.stat().st_mtime, Path(entry.path))
                    for entry in entries
                    if entry.is_file() and entry.name.endswith(".cfg")
                ]
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f"Failed to list configs in {device_dir}: {e}")
            return []

        configs.sort(key=lambda item: item[0], reverse=True)

        return [path for _, path in configs]

    def calculate_checksum(self, content: str) -> str:
        """
        Calculate SHA256 checksum of content

        Args:
            content: Content to hash

        Returns:
            str: SHA256 checksum (hex)
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def calculate_file_checksum(self, file_path: str) -> str:
        """
        Calculate SHA256 checksum of a file without loading it into memory

        Args:
            file_path: Path to the file

        Returns:
            str: SHA256 checksum (hex)

        Raises:
            StorageError: If the file cannot be read
        """
        digest = hashlib.sha256()

        try:
            with open(file_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
                    digest.update(chunk)
        except FileNotFoundError:
            raise StorageError(f"Configuration file not found: {file_path}")
        except OSError as e:
            raise StorageError(f"Failed to read configuration: {e}")

        return digest.hexdigest()

    def calculate_config_hash(self, content: str) -> str:
        """
        Calculate hash for configuration deduplication

        Args:
            content: Configuration content

        Returns:
            str: Configuration hash
        """
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
                logger.debug(
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
                except FileNotFoundError:
                    continue
                except OSError as e:
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

    def _iter_config_files(self, root: Path) -> Iterator[os.DirEntry]:
        """
        Walk the storage tree yielding .cfg entries

        Args:
            root: Directory to walk

        Yields:
            os.DirEntry: Directory entry for each configuration file
        """
        try:
            with os.scandir(root) as entries:
                # Materialise the entries before recursing so the directory
                # handle is released promptly at each level.
                for entry in list(entries):
                    if entry.is_dir(follow_symlinks=False):
                        yield from self._iter_config_files(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(
                        ".cfg"
                    ):
                        yield entry
        except FileNotFoundError:
            return
        except OSError as e:
            logger.warning(f"Failed to scan {root}: {e}")
            return

    def get_storage_stats(self) -> dict:
        """
        Get storage statistics

        Returns:
            dict: Storage statistics (total size, file count, etc.)
        """
        total_size = 0
        total_files = 0

        try:
            # DirEntry.stat() is served from the cached directory read on
            # Linux, so this walk costs roughly one syscall per directory
            # rather than one per file as rglob() + stat() did.
            for entry in self._iter_config_files(self.base_path):
                total_size += entry.stat().st_size
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
