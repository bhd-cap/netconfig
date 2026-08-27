"""
Copy stored configurations to a remote SFTP or FTP server

Two transports behind one interface:

* SFTP over SSH, via paramiko (already a dependency through Netmiko)
* FTP and FTPS, via the standard library's ftplib

Uploads are best-effort by design. A configuration is already safely on local
disk and in the database before any of this runs, so an unreachable archive
server records a failure against the target and is retried on the next
backup - it never fails the backup itself or loses a configuration.
"""
import ftplib
import io
import logging
import os
import posixpath
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from app.models.administration import BackupTarget
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOLS = ("sftp", "ftp", "ftps")

DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21}


class RemoteBackupError(RuntimeError):
    """Raised when a remote transfer cannot be completed"""


@dataclass
class UploadResult:
    """Outcome of uploading one or more files"""

    succeeded: int = 0
    failed: int = 0
    errors: List[str] = None
    remote_paths: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.remote_paths is None:
            self.remote_paths = []

    @property
    def ok(self) -> bool:
        return self.failed == 0


@dataclass
class TargetConfig:
    """
    Plain connection details for a remote target

    Decrypted once and passed around, so nothing downstream needs the ORM
    object or the encryption key.
    """

    name: str
    protocol: str
    host: str
    port: int
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None
    private_key_passphrase: Optional[str] = None
    remote_path: str = "/"
    use_device_subdirectories: bool = True
    verify_host_key: bool = False
    known_host_key: Optional[str] = None
    timeout: int = 30

    @classmethod
    def from_model(cls, target: BackupTarget, timeout: int = 30) -> "TargetConfig":
        """
        Build a config from a stored target, decrypting its secrets

        Args:
            target: The stored target
            timeout: Connection timeout

        Returns:
            TargetConfig
        """

        def decrypt(value):
            return encryption_service.decrypt(value) if value else None

        return cls(
            name=target.name,
            protocol=(target.protocol or "sftp").lower(),
            host=target.host,
            port=target.port or DEFAULT_PORTS.get(target.protocol, 22),
            username=target.username,
            password=decrypt(target.encrypted_password),
            private_key=decrypt(target.private_key),
            private_key_passphrase=decrypt(target.private_key_passphrase),
            remote_path=target.remote_path or "/",
            use_device_subdirectories=bool(target.use_device_subdirectories),
            verify_host_key=bool(target.verify_host_key),
            known_host_key=target.known_host_key,
            timeout=timeout,
        )


def remote_directory(config: TargetConfig, organization_id: int, hostname: str) -> str:
    """
    Work out the directory a device's configurations belong in

    Mirrors the local {org}/{hostname}/ layout when the target asks for it, so
    an archive of many organizations stays navigable.

    Args:
        config: Target configuration
        organization_id: Organization the device belongs to
        hostname: Device hostname

    Returns:
        Remote directory path
    """
    base = config.remote_path or "/"

    if not config.use_device_subdirectories:
        return base

    # Never let a hostname escape the configured base directory.
    safe_hostname = _safe_component(hostname)
    return posixpath.join(base, str(organization_id), safe_hostname)


def _safe_component(value: str) -> str:
    """
    Reduce a path component to something safe to send to a remote server

    A hostname comes from a device, and a device can be renamed by whoever
    administers it. Without this, a name containing '../' would write outside
    the configured directory.
    """
    cleaned = (value or "").replace("\\", "/").split("/")[-1]
    cleaned = cleaned.replace("..", "").strip().strip(".")
    return cleaned or "unknown"


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


class SftpTransport:
    """Uploads over SFTP using paramiko"""

    def __init__(self, config: TargetConfig):
        self.config = config
        self._client = None
        self._sftp = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self) -> None:
        """
        Open the SSH session

        Raises:
            RemoteBackupError: On any connection or authentication failure
        """
        import paramiko

        config = self.config
        client = paramiko.SSHClient()

        if config.verify_host_key:
            client.load_system_host_keys()
            if config.known_host_key:
                self._add_known_host(client, config)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # Archive servers are usually on a trusted network and their keys
            # are rarely pre-seeded; verification is opt-in per target.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pkey = None
        if config.private_key:
            pkey = self._load_private_key(config)

        try:
            client.connect(
                hostname=config.host,
                port=config.port,
                username=config.username,
                password=config.password if not pkey else None,
                pkey=pkey,
                timeout=config.timeout,
                banner_timeout=config.timeout,
                auth_timeout=config.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as e:
            raise RemoteBackupError(
                f"SFTP connection to {config.host}:{config.port} failed: {e}"
            )

        self._client = client
        try:
            self._sftp = client.open_sftp()
            self._sftp.get_channel().settimeout(config.timeout)
        except Exception as e:
            self.close()
            raise RemoteBackupError(f"Could not open an SFTP channel: {e}")

    @staticmethod
    def _add_known_host(client, config: TargetConfig) -> None:
        """Trust a specific host key supplied with the target"""
        import paramiko

        try:
            parts = config.known_host_key.split()
            key_type, key_data = (parts[-2], parts[-1]) if len(parts) >= 2 else (None, None)
            if not key_data:
                return

            key = paramiko.PKey.from_type_string(key_type, __import__("base64").b64decode(key_data))
            client.get_host_keys().add(config.host, key_type, key)
        except Exception as e:  # noqa: BLE001 - fall through to system known_hosts
            logger.warning(f"Could not parse the configured host key: {e}")

    @staticmethod
    def _load_private_key(config: TargetConfig):
        """
        Load a private key in whichever format it was given

        Raises:
            RemoteBackupError: If no supported format matches
        """
        import paramiko

        errors = []
        for key_class in (
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.RSAKey,
            paramiko.DSSKey,
        ):
            try:
                return key_class.from_private_key(
                    io.StringIO(config.private_key),
                    password=config.private_key_passphrase or None,
                )
            except Exception as e:  # noqa: BLE001 - try the next key type
                errors.append(f"{key_class.__name__}: {e}")

        raise RemoteBackupError(
            "The configured private key could not be read in any supported "
            f"format ({'; '.join(errors[:2])})"
        )

    def ensure_directory(self, path: str) -> None:
        """
        Create a remote directory and its parents

        Args:
            path: Remote directory
        """
        if not path or path == "/":
            return

        parts = [part for part in path.strip("/").split("/") if part]
        current = "/" if path.startswith("/") else ""

        for part in parts:
            current = posixpath.join(current, part) if current else part
            try:
                self._sftp.stat(current)
            except IOError:
                try:
                    self._sftp.mkdir(current)
                except IOError as e:
                    # Another upload may have created it in the meantime.
                    try:
                        self._sftp.stat(current)
                    except IOError:
                        raise RemoteBackupError(
                            f"Could not create remote directory {current}: {e}"
                        )

    def upload(self, local_path: str, remote_dir: str, filename: str) -> str:
        """
        Upload one file

        Written to a temporary name and renamed into place, so a reader on the
        archive never sees a half-written configuration.

        Args:
            local_path: Local file
            remote_dir: Remote directory
            filename: Remote file name

        Returns:
            The full remote path
        """
        self.ensure_directory(remote_dir)

        remote_path = posixpath.join(remote_dir, filename)
        temporary = f"{remote_path}.part"

        try:
            self._sftp.put(local_path, temporary)
            try:
                self._sftp.remove(remote_path)
            except IOError:
                pass
            self._sftp.rename(temporary, remote_path)
        except Exception as e:
            try:
                self._sftp.remove(temporary)
            except Exception:
                pass
            raise RemoteBackupError(f"Upload of {filename} failed: {e}")

        return remote_path

    def close(self) -> None:
        """Close the SFTP channel and SSH session"""
        for handle in (self._sftp, self._client):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass
        self._sftp = None
        self._client = None


class FtpTransport:
    """Uploads over FTP, optionally with TLS"""

    def __init__(self, config: TargetConfig):
        self.config = config
        self._ftp = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self) -> None:
        """
        Open the FTP session

        Raises:
            RemoteBackupError: On any connection or authentication failure
        """
        config = self.config

        try:
            if config.protocol == "ftps":
                ftp = ftplib.FTP_TLS(timeout=config.timeout)
                if not config.verify_host_key:
                    # Archive servers commonly use a self-signed certificate.
                    ftp.context = ssl._create_unverified_context()
            else:
                ftp = ftplib.FTP(timeout=config.timeout)

            ftp.connect(config.host, config.port, timeout=config.timeout)
            ftp.login(config.username, config.password or "")

            if config.protocol == "ftps":
                ftp.prot_p()

            # Passive mode: the server is usually behind a firewall and the
            # active-mode back-connection would not get through.
            ftp.set_pasv(True)
        except (*ftplib.all_errors, OSError) as e:
            # ftplib.all_errors is a tuple, so it has to be unpacked here:
            # nesting it would raise TypeError instead of catching anything.
            raise RemoteBackupError(
                f"FTP connection to {config.host}:{config.port} failed: {e}"
            )

        self._ftp = ftp

    def ensure_directory(self, path: str) -> None:
        """
        Create a remote directory and its parents

        Args:
            path: Remote directory
        """
        if not path or path == "/":
            return

        parts = [part for part in path.strip("/").split("/") if part]
        self._ftp.cwd("/")

        for part in parts:
            try:
                self._ftp.cwd(part)
            except ftplib.error_perm:
                try:
                    self._ftp.mkd(part)
                    self._ftp.cwd(part)
                except ftplib.error_perm as e:
                    raise RemoteBackupError(
                        f"Could not create remote directory {part}: {e}"
                    )

    def upload(self, local_path: str, remote_dir: str, filename: str) -> str:
        """
        Upload one file

        Args:
            local_path: Local file
            remote_dir: Remote directory
            filename: Remote file name

        Returns:
            The full remote path
        """
        self.ensure_directory(remote_dir)

        temporary = f"{filename}.part"

        try:
            with open(local_path, "rb") as handle:
                self._ftp.storbinary(f"STOR {temporary}", handle)

            try:
                self._ftp.delete(filename)
            except ftplib.error_perm:
                pass

            self._ftp.rename(temporary, filename)
        except (*ftplib.all_errors, OSError) as e:
            try:
                self._ftp.delete(temporary)
            except Exception:
                pass
            raise RemoteBackupError(f"Upload of {filename} failed: {e}")

        return posixpath.join(remote_dir, filename)

    def close(self) -> None:
        """Close the FTP session"""
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
        self._ftp = None


def open_transport(config: TargetConfig):
    """
    Build the transport for a target's protocol

    Args:
        config: Target configuration

    Returns:
        SftpTransport or FtpTransport

    Raises:
        RemoteBackupError: For an unknown protocol
    """
    protocol = (config.protocol or "").lower()

    if protocol == "sftp":
        return SftpTransport(config)
    if protocol in ("ftp", "ftps"):
        return FtpTransport(config)

    raise RemoteBackupError(
        f"Unsupported protocol '{config.protocol}'; "
        f"expected one of {', '.join(SUPPORTED_PROTOCOLS)}"
    )


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------


def check_target_connection(config: TargetConfig) -> dict:
    """
    Check a target is reachable and writable

    Writes and removes a probe file, because a target that accepts a login but
    rejects writes is the failure people actually hit.

    Args:
        config: Target configuration

    Returns:
        dict with success, message and elapsed time
    """
    import tempfile
    import time

    started = time.perf_counter()

    try:
        with open_transport(config) as transport:
            with tempfile.NamedTemporaryFile("w", suffix=".probe", delete=False) as handle:
                handle.write("netconfig-backup connection test\n")
                probe_path = handle.name

            try:
                remote_dir = config.remote_path or "/"
                filename = f".netconfig-test-{int(datetime.now().timestamp())}"
                transport.upload(probe_path, remote_dir, filename)

                # Clean up; leaving probe files behind on an archive is rude.
                try:
                    if isinstance(transport, SftpTransport):
                        transport._sftp.remove(posixpath.join(remote_dir, filename))
                    else:
                        transport._ftp.delete(filename)
                except Exception:
                    pass
            finally:
                os.unlink(probe_path)

        elapsed = round(time.perf_counter() - started, 2)
        return {
            "success": True,
            "message": f"Connected to {config.host} and wrote a test file",
            "elapsed": elapsed,
        }

    except RemoteBackupError as e:
        return {
            "success": False,
            "message": str(e),
            "elapsed": round(time.perf_counter() - started, 2),
        }
    except Exception as e:  # noqa: BLE001 - a test must always answer
        return {
            "success": False,
            "message": f"Unexpected error: {e}",
            "elapsed": round(time.perf_counter() - started, 2),
        }


def upload_files(
    config: TargetConfig,
    files: Sequence[dict],
) -> UploadResult:
    """
    Upload several configurations over one session

    Args:
        config: Target configuration
        files: dicts with 'local_path', 'filename', 'hostname' and
            'organization_id'

    Returns:
        UploadResult
    """
    result = UploadResult()

    if not files:
        return result

    try:
        with open_transport(config) as transport:
            for item in files:
                local_path = item.get("local_path")

                if not local_path or not Path(local_path).is_file():
                    result.failed += 1
                    result.errors.append(f"{item.get('filename')}: local file missing")
                    continue

                try:
                    remote_dir = remote_directory(
                        config,
                        item.get("organization_id", 0),
                        item.get("hostname", "unknown"),
                    )
                    remote_path = transport.upload(
                        local_path, remote_dir, item["filename"]
                    )
                    result.succeeded += 1
                    result.remote_paths.append(remote_path)
                except RemoteBackupError as e:
                    result.failed += 1
                    result.errors.append(str(e))

    except RemoteBackupError as e:
        # The session itself could not be established: nothing was uploaded.
        result.failed += len(files) - result.succeeded
        result.errors.append(str(e))
    except Exception as e:  # noqa: BLE001 - never propagate into a backup
        logger.exception(f"Unexpected error uploading to {config.host}")
        result.failed += len(files) - result.succeeded
        result.errors.append(f"Unexpected error: {e}")

    return result


def record_result(db, target: BackupTarget, result: UploadResult) -> None:
    """
    Write an upload outcome back onto the target

    Args:
        db: Database session
        target: The stored target
        result: What happened
    """
    target.last_run_at = datetime.now(timezone.utc)
    target.last_status = "success" if result.ok else "failed"
    target.last_error = "; ".join(result.errors[:3]) if result.errors else None
    target.uploads_succeeded = (target.uploads_succeeded or 0) + result.succeeded
    target.uploads_failed = (target.uploads_failed or 0) + result.failed
    db.commit()


# Named check_target_connection rather than test_connection so that importing
# it does not make pytest collect it as a test case.
