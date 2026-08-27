"""
Transport selection and SNMP client tests.

The SNMP tests run against a real net-snmp agent on a high port when one can
be started; they skip rather than fail where snmpd is unavailable, so the
suite still runs on a machine without it.
"""
import shutil
import socket
import subprocess
import time

import pytest

from app.config.discovery_commands import (
    SUPPORTED_TRANSPORTS,
    get_capabilities,
    get_discovery_command,
    get_telnet_device_type,
    supports_discovery,
)
from app.services.device_connector import DeviceConnector, DeviceConnectionError
from app.services.snmp_client import OID, SnmpClient, SnmpError, snmp_available
from app.utils.encryption import encryption_service


@pytest.fixture(scope="module")
def encrypted_password():
    return encryption_service.encrypt("secret")


# --------------------------------------------------------------------------
# Transport selection
# --------------------------------------------------------------------------


def test_default_transport_is_ssh(encrypted_password):
    connector = DeviceConnector(
        "sw1", "10.0.0.1", "cisco_ios", "admin", encrypted_password
    )
    assert connector.transport == "ssh"
    assert connector._cli_driver() == "cisco_ios"
    assert connector.supports_cli()


@pytest.mark.parametrize(
    "device_type,expected",
    [
        ("cisco_ios", "cisco_ios_telnet"),
        ("cisco_nxos", "cisco_ios_telnet"),
        ("arista_eos", "arista_eos_telnet"),
        ("juniper_junos", "juniper_junos_telnet"),
        ("hp_comware", "hp_comware_telnet"),
        ("hp_procurve", "hp_procurve_telnet"),
    ],
)
def test_telnet_uses_the_platform_driver(device_type, expected, encrypted_password):
    connector = DeviceConnector(
        "sw1", "10.0.0.1", device_type, "admin", encrypted_password, transport="telnet"
    )
    assert connector._cli_driver() == expected
    assert get_telnet_device_type(device_type) == expected


def test_unknown_device_type_falls_back_to_generic_telnet():
    assert get_telnet_device_type("something_new") == "generic_telnet"


def test_invalid_transport_is_rejected(encrypted_password):
    with pytest.raises(DeviceConnectionError, match="Unsupported transport"):
        DeviceConnector(
            "sw1", "10.0.0.1", "cisco_ios", "admin", encrypted_password,
            transport="carrier-pigeon",
        )


def test_snmp_transport_cannot_run_cli_commands():
    connector = DeviceConnector(
        "sw1", "10.0.0.1", "cisco_ios", "", "", transport="snmp",
        snmp={"version": "2c", "community": encryption_service.encrypt("public")},
    )
    assert not connector.supports_cli()
    # Discovery asks for CLI output and must get nothing rather than an error.
    assert connector.get_discovery_output("lldp") is None


def test_snmp_credentials_are_decrypted():
    connector = DeviceConnector(
        "sw1", "10.0.0.1", "cisco_ios", "", "", transport="snmp",
        snmp={
            "version": "3",
            "v3_user": "netops",
            "v3_auth_key": encryption_service.encrypt("authpass"),
            "v3_priv_key": encryption_service.encrypt("privpass"),
        },
    )
    assert connector.snmp["v3_auth_key"] == "authpass"
    assert connector.snmp["v3_priv_key"] == "privpass"


def test_all_transports_declared():
    assert SUPPORTED_TRANSPORTS == ("ssh", "telnet", "snmp")


# --------------------------------------------------------------------------
# Discovery command matrix
# --------------------------------------------------------------------------


def test_every_vendor_supports_discovery():
    for device_type in (
        "cisco_ios", "cisco_ios_xe", "cisco_nxos", "arista_eos", "fortinet",
        "juniper_junos", "aruba_os", "hp_comware", "hp_procurve",
    ):
        assert supports_discovery(device_type), device_type
        assert "lldp" in get_capabilities(device_type)


def test_cisco_platforms_also_have_cdp():
    for device_type in ("cisco_ios", "cisco_ios_xe", "cisco_nxos", "arista_eos"):
        assert get_discovery_command(device_type, "cdp") is not None


def test_unknown_capability_returns_none():
    assert get_discovery_command("cisco_ios", "teleportation") is None
    assert get_discovery_command("no_such_device", "lldp") is None


# --------------------------------------------------------------------------
# SNMP against a real agent
# --------------------------------------------------------------------------

SNMP_PORT = 16162
COMMUNITY = "pytestcommunity"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


@pytest.fixture(scope="module")
def snmp_agent(tmp_path_factory):
    """Run a read-only net-snmp agent on a high port for the duration"""
    if not snmp_available():
        pytest.skip("pysnmp is not installed")
    if not shutil.which("snmpd"):
        pytest.skip("snmpd is not installed")

    config = tmp_path_factory.mktemp("snmp") / "snmpd.conf"
    config.write_text(
        f"agentaddress udp:127.0.0.1:{SNMP_PORT}\n"
        f"rocommunity {COMMUNITY} 127.0.0.1\n"
        "sysName pytest-switch\n"
        "sysLocation Test Lab\n"
        "sysContact netops@example.com\n"
    )

    process = subprocess.Popen(
        ["snmpd", "-C", "-c", str(config), "-f", "-Lf", "/dev/null"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(30):
        if _port_open(SNMP_PORT):
            break
        time.sleep(0.2)
    else:
        process.terminate()
        pytest.skip("snmpd did not start")

    yield

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture
def client(snmp_agent):
    return SnmpClient(
        host="127.0.0.1", port=SNMP_PORT, version="2c",
        community=COMMUNITY, timeout=2, retries=1,
    )


def test_snmp_get_scalar(client):
    assert client.get(OID["sysName"]) == "pytest-switch"


def test_snmp_system_info(client):
    info = client.system_info()
    assert info["sysName"] == "pytest-switch"
    assert info["sysLocation"] == "Test Lab"
    assert info["sysContact"] == "netops@example.com"
    assert info["sysDescr"]


def test_snmp_walk_returns_rows(client):
    rows = client.walk(OID["ifDescr"])
    assert rows
    assert all(isinstance(oid, str) and isinstance(value, str) for oid, value in rows)


def test_snmp_interface_names(client):
    names = client.interface_names()
    assert names
    assert all(index.isdigit() for index in names)


def test_snmp_walk_respects_the_row_cap(client):
    assert len(client.walk(OID["ifDescr"], max_rows=1)) <= 1


def test_snmp_missing_object_returns_none(client):
    assert client.get("1.3.6.1.2.1.1.99.0") is None


def test_snmp_repeated_calls_work(client):
    """Each call builds its own loop and engine; reuse must not break."""
    for _ in range(3):
        assert client.get(OID["sysName"]) == "pytest-switch"


def test_snmp_concurrent_calls_from_threads(client):
    """Discovery probes devices from a thread pool."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.get(OID["sysName"]), range(6)))

    assert results == ["pytest-switch"] * 6


def test_snmp_wrong_community_returns_none(snmp_agent):
    bad = SnmpClient(
        host="127.0.0.1", port=SNMP_PORT, version="2c",
        community="wrong", timeout=1, retries=0,
    )
    assert bad.get(OID["sysName"]) is None


def test_snmp_unreachable_device_returns_none(snmp_agent):
    dead = SnmpClient(
        host="127.0.0.1", port=SNMP_PORT + 500, version="2c",
        community=COMMUNITY, timeout=1, retries=0,
    )
    assert dead.get(OID["sysName"]) is None


def test_connector_snmp_transport_end_to_end(snmp_agent):
    connector = DeviceConnector(
        "snmp-switch", "127.0.0.1", "cisco_ios", "", "", transport="snmp",
        snmp={
            "version": "2c",
            "community": encryption_service.encrypt(COMMUNITY),
            "port": SNMP_PORT,
        },
        timeout=2,
    )
    assert connector.connect() is True
    assert connector.snmp_sysname == "pytest-switch"
    connector.disconnect()


def test_connector_snmp_unreachable_raises(snmp_agent):
    connector = DeviceConnector(
        "dead", "127.0.0.1", "cisco_ios", "", "", transport="snmp",
        snmp={
            "version": "2c",
            "community": encryption_service.encrypt(COMMUNITY),
            "port": SNMP_PORT + 501,
        },
        timeout=1,
    )
    with pytest.raises(DeviceConnectionError, match="No SNMP response"):
        connector.connect()


def test_snmp_requires_credentials():
    if not snmp_available():
        pytest.skip("pysnmp is not installed")

    with pytest.raises(SnmpError, match="community"):
        SnmpClient(host="127.0.0.1", version="2c")

    with pytest.raises(SnmpError, match="user name"):
        SnmpClient(host="127.0.0.1", version="3")

    with pytest.raises(SnmpError, match="Unsupported SNMP version"):
        SnmpClient(host="127.0.0.1", version="9")
