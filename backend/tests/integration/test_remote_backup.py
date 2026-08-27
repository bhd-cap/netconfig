"""
SFTP and FTP export tests against real servers.

An in-process paramiko SSH/SFTP server and a pyftpdlib FTP server are started
on high ports, so the transfers, directory creation, atomic rename and failure
handling are exercised for real rather than mocked.
"""
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from app.services.remote_backup import (
    RemoteBackupError,
    TargetConfig,
    UploadResult,
    open_transport,
    remote_directory,
    check_target_connection,
    upload_files,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# --------------------------------------------------------------------------
# FTP server
# --------------------------------------------------------------------------


@pytest.fixture
def ftp_server(tmp_path):
    """A real FTP server rooted at a temporary directory"""
    pyftpdlib = pytest.importorskip("pyftpdlib")
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer

    root = tmp_path / "ftproot"
    root.mkdir()

    authorizer = DummyAuthorizer()
    authorizer.add_user("archiver", "archivepw", str(root), perm="elradfmwMT")

    handler = FTPHandler
    handler.authorizer = authorizer

    port = _free_port()
    server = FTPServer(("127.0.0.1", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert _wait_for_port(port), "FTP server did not start"

    yield {"port": port, "root": root}

    server.close_all()


@pytest.fixture
def ftp_config(ftp_server):
    return TargetConfig(
        name="archive",
        protocol="ftp",
        host="127.0.0.1",
        port=ftp_server["port"],
        username="archiver",
        password="archivepw",
        remote_path="/backups",
        timeout=10,
    )


@pytest.fixture
def local_config(tmp_path):
    """A stored configuration file to upload"""
    path = tmp_path / "core-01_20250301_120000.cfg"
    path.write_text("hostname core-01\n!\ninterface Gi1/0/1\n description uplink\n")
    return path


def test_ftp_upload_creates_directories_and_file(ftp_server, ftp_config, local_config):
    result = upload_files(
        ftp_config,
        [
            {
                "local_path": str(local_config),
                "filename": local_config.name,
                "hostname": "core-01",
                "organization_id": 7,
            }
        ],
    )

    assert result.ok, result.errors
    assert result.succeeded == 1

    stored = ftp_server["root"] / "backups" / "7" / "core-01" / local_config.name
    assert stored.is_file()
    assert stored.read_text() == local_config.read_text()


def test_ftp_upload_leaves_no_partial_file(ftp_server, ftp_config, local_config):
    """Uploads land under a .part name and are renamed into place."""
    upload_files(
        ftp_config,
        [{"local_path": str(local_config), "filename": local_config.name,
          "hostname": "core-01", "organization_id": 7}],
    )

    directory = ftp_server["root"] / "backups" / "7" / "core-01"
    assert not list(directory.glob("*.part"))


def test_ftp_reupload_replaces_the_file(ftp_server, ftp_config, local_config, tmp_path):
    payload = {"local_path": str(local_config), "filename": "same-name.cfg",
               "hostname": "core-01", "organization_id": 7}
    upload_files(ftp_config, [payload])

    updated = tmp_path / "updated.cfg"
    updated.write_text("hostname core-01\n! changed\n")
    payload["local_path"] = str(updated)
    result = upload_files(ftp_config, [payload])

    assert result.ok, result.errors
    stored = ftp_server["root"] / "backups" / "7" / "core-01" / "same-name.cfg"
    assert "changed" in stored.read_text()


def test_ftp_flat_layout_when_subdirectories_disabled(
    ftp_server, ftp_config, local_config
):
    ftp_config.use_device_subdirectories = False

    result = upload_files(
        ftp_config,
        [{"local_path": str(local_config), "filename": local_config.name,
          "hostname": "core-01", "organization_id": 7}],
    )

    assert result.ok, result.errors
    assert (ftp_server["root"] / "backups" / local_config.name).is_file()


def test_ftp_uploads_several_files_over_one_session(
    ftp_server, ftp_config, tmp_path
):
    files = []
    for index in range(5):
        path = tmp_path / f"device-{index}.cfg"
        path.write_text(f"hostname device-{index}\n")
        files.append(
            {"local_path": str(path), "filename": path.name,
             "hostname": f"device-{index}", "organization_id": 3}
        )

    result = upload_files(ftp_config, files)

    assert result.succeeded == 5
    assert result.failed == 0
    for index in range(5):
        assert (
            ftp_server["root"] / "backups" / "3" / f"device-{index}"
            / f"device-{index}.cfg"
        ).is_file()


def test_ftp_test_connection_succeeds_and_cleans_up(ftp_server, ftp_config):
    outcome = check_target_connection(ftp_config)

    assert outcome["success"] is True, outcome["message"]
    assert "127.0.0.1" in outcome["message"]
    # The probe file must not be left behind.
    leftovers = list((ftp_server["root"] / "backups").glob(".netconfig-test-*"))
    assert not leftovers


def test_ftp_bad_credentials_report_failure(ftp_server, ftp_config):
    ftp_config.password = "wrong"
    outcome = check_target_connection(ftp_config)

    assert outcome["success"] is False
    assert "failed" in outcome["message"].lower()


def test_ftp_unreachable_server_reports_failure(ftp_config):
    ftp_config.port = _free_port()  # nothing listening
    ftp_config.timeout = 2

    outcome = check_target_connection(ftp_config)
    assert outcome["success"] is False


def test_missing_local_file_is_reported_not_raised(ftp_server, ftp_config):
    result = upload_files(
        ftp_config,
        [{"local_path": "/nonexistent/file.cfg", "filename": "file.cfg",
          "hostname": "core-01", "organization_id": 1}],
    )

    assert result.failed == 1
    assert result.succeeded == 0
    assert "local file missing" in result.errors[0]


def test_unreachable_target_never_raises_into_the_backup(ftp_config, local_config):
    """A backup must not fail because an archive server is down."""
    ftp_config.port = _free_port()
    ftp_config.timeout = 2

    result = upload_files(
        ftp_config,
        [{"local_path": str(local_config), "filename": local_config.name,
          "hostname": "core-01", "organization_id": 1}],
    )

    assert isinstance(result, UploadResult)
    assert result.ok is False
    assert result.failed == 1


# --------------------------------------------------------------------------
# SFTP server
# --------------------------------------------------------------------------


@pytest.fixture
def sftp_server(tmp_path):
    """An in-process paramiko SSH server exposing an SFTP subsystem"""
    paramiko = pytest.importorskip("paramiko")

    root = tmp_path / "sftproot"
    root.mkdir()

    host_key = paramiko.RSAKey.generate(2048)
    port = _free_port()
    stop = threading.Event()

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            if username == "archiver" and password == "archivepw":
                return paramiko.AUTH_SUCCESSFUL
            return paramiko.AUTH_FAILED

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

        def get_allowed_auths(self, username):
            return "password"

    def serve():
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(8)
        listener.settimeout(0.5)

        while not stop.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            threading.Thread(
                target=handle, args=(client,), daemon=True
            ).start()

        listener.close()

    def handle(client):
        try:
            transport = paramiko.Transport(client)
            transport.add_server_key(host_key)
            transport.set_subsystem_handler(
                "sftp", paramiko.SFTPServer, paramiko.SFTPServerInterface
            )

            # Serve the temporary directory as the SFTP root.
            from paramiko import SFTPServer, SFTPServerInterface, SFTPAttributes
            from paramiko import SFTP_OK, SFTP_FAILURE

            class StubSFTP(SFTPServerInterface):
                ROOT = str(root)

                def _real(self, path):
                    return os.path.join(self.ROOT, path.lstrip("/"))

                def list_folder(self, path):
                    real = self._real(path)
                    try:
                        out = []
                        for name in os.listdir(real):
                            attr = SFTPAttributes.from_stat(
                                os.stat(os.path.join(real, name))
                            )
                            attr.filename = name
                            out.append(attr)
                        return out
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)

                def stat(self, path):
                    try:
                        return SFTPAttributes.from_stat(os.stat(self._real(path)))
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)

                lstat = stat

                def open(self, path, flags, attr):
                    real = self._real(path)
                    try:
                        binary_flag = getattr(os, "O_BINARY", 0)
                        flags |= binary_flag
                        mode = getattr(attr, "st_mode", None) or 0o666
                        fd = os.open(real, flags, mode)
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)

                    if flags & os.O_WRONLY:
                        fstr = "wb" if flags & os.O_APPEND == 0 else "ab"
                    elif flags & os.O_RDWR:
                        fstr = "r+b"
                    else:
                        fstr = "rb"

                    try:
                        handle_file = os.fdopen(fd, fstr)
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)

                    from paramiko import SFTPHandle

                    class Handle(SFTPHandle):
                        def stat(self):
                            try:
                                return SFTPAttributes.from_stat(
                                    os.fstat(self.readfile.fileno())
                                )
                            except OSError as e:
                                return SFTPServer.convert_errno(e.errno)

                    file_handle = Handle(flags)
                    file_handle.filename = real
                    file_handle.readfile = handle_file
                    file_handle.writefile = handle_file
                    return file_handle

                def remove(self, path):
                    try:
                        os.remove(self._real(path))
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)
                    return SFTP_OK

                def rename(self, oldpath, newpath):
                    try:
                        os.rename(self._real(oldpath), self._real(newpath))
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)
                    return SFTP_OK

                def mkdir(self, path, attr):
                    try:
                        os.mkdir(self._real(path))
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)
                    return SFTP_OK

                def rmdir(self, path):
                    try:
                        os.rmdir(self._real(path))
                    except OSError as e:
                        return SFTPServer.convert_errno(e.errno)
                    return SFTP_OK

                def chattr(self, path, attr):
                    return SFTP_OK

            transport.set_subsystem_handler("sftp", SFTPServer, StubSFTP)
            transport.start_server(server=Server())

            channel = transport.accept(20)
            if channel is None:
                transport.close()
                return

            while transport.is_active() and not stop.is_set():
                time.sleep(0.1)
        except Exception:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert _wait_for_port(port), "SFTP server did not start"

    yield {"port": port, "root": root}

    stop.set()


@pytest.fixture
def sftp_config(sftp_server):
    return TargetConfig(
        name="sftp-archive",
        protocol="sftp",
        host="127.0.0.1",
        port=sftp_server["port"],
        username="archiver",
        password="archivepw",
        remote_path="/backups",
        timeout=15,
    )


def test_sftp_upload_creates_directories_and_file(
    sftp_server, sftp_config, local_config
):
    result = upload_files(
        sftp_config,
        [{"local_path": str(local_config), "filename": local_config.name,
          "hostname": "core-01", "organization_id": 4}],
    )

    assert result.ok, result.errors
    stored = sftp_server["root"] / "backups" / "4" / "core-01" / local_config.name
    assert stored.is_file()
    assert stored.read_text() == local_config.read_text()


def test_sftp_leaves_no_partial_file(sftp_server, sftp_config, local_config):
    upload_files(
        sftp_config,
        [{"local_path": str(local_config), "filename": local_config.name,
          "hostname": "core-01", "organization_id": 4}],
    )
    directory = sftp_server["root"] / "backups" / "4" / "core-01"
    assert not list(directory.glob("*.part"))


def test_sftp_bad_password_reports_failure(sftp_server, sftp_config):
    sftp_config.password = "wrong"
    outcome = check_target_connection(sftp_config)

    assert outcome["success"] is False
    assert "failed" in outcome["message"].lower()


def test_sftp_test_connection_succeeds(sftp_server, sftp_config):
    outcome = check_target_connection(sftp_config)
    assert outcome["success"] is True, outcome["message"]


# --------------------------------------------------------------------------
# Paths and protocol selection
# --------------------------------------------------------------------------


def test_remote_directory_mirrors_the_local_layout():
    config = TargetConfig(
        name="t", protocol="sftp", host="h", port=22, username="u",
        remote_path="/archive", use_device_subdirectories=True,
    )
    assert remote_directory(config, 12, "core-01") == "/archive/12/core-01"


def test_remote_directory_flat_when_disabled():
    config = TargetConfig(
        name="t", protocol="sftp", host="h", port=22, username="u",
        remote_path="/archive", use_device_subdirectories=False,
    )
    assert remote_directory(config, 12, "core-01") == "/archive"


@pytest.mark.parametrize(
    "hostname",
    ["../../etc", "../escape", "a/b/../../c", "/absolute", "..", ""],
)
def test_hostname_cannot_escape_the_remote_path(hostname):
    """A device can be renamed by whoever administers it."""
    config = TargetConfig(
        name="t", protocol="sftp", host="h", port=22, username="u",
        remote_path="/archive", use_device_subdirectories=True,
    )
    path = remote_directory(config, 1, hostname)

    assert path.startswith("/archive/1/")
    assert ".." not in path


def test_unknown_protocol_is_rejected():
    config = TargetConfig(
        name="t", protocol="carrier-pigeon", host="h", port=1, username="u"
    )
    with pytest.raises(RemoteBackupError, match="Unsupported protocol"):
        open_transport(config)


@pytest.mark.parametrize("protocol", ["sftp", "ftp", "ftps"])
def test_supported_protocols_build_a_transport(protocol):
    config = TargetConfig(
        name="t", protocol=protocol, host="h", port=1, username="u"
    )
    assert open_transport(config) is not None
