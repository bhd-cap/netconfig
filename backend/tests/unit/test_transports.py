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


def test_an_error_carrying_a_read_buffer_is_storable(encrypted_password):
    """
    Netmiko puts the channel buffer into the text of a timeout

    That message is not only logged: it becomes the device's auth_error, a row
    in device_probes and a discovery run's error_message. A telnet buffer is
    full of the NULs RFC 854 pairs with a bare CR, and PostgreSQL holds none
    of them.
    """
    from unittest import mock

    from app.services.device_connector import DeviceCommandError

    connector = DeviceConnector(
        "sw1", "10.0.0.1", "cisco_ios", "admin", encrypted_password,
        transport="telnet",
    )
    connector.connection = mock.Mock()
    connector.connection.send_command.side_effect = OSError(
        "Search pattern never detected: \r\x00sw1>\r\x00"
    )

    with pytest.raises(DeviceCommandError) as raised:
        connector.send_command("show lldp neighbors detail")

    assert "\x00" not in str(raised.value)
    assert "Search pattern never detected" in str(raised.value)


def test_command_output_is_cleaned_on_the_way_out(encrypted_password):
    """The other half: what the command returned, not what the error said"""
    from unittest import mock

    connector = DeviceConnector(
        "sw1", "10.0.0.1", "cisco_ios", "admin", encrypted_password,
        transport="telnet",
    )
    connector.connection = mock.Mock()
    connector.connection.send_command.return_value = "Port: Gi1/0/24\r\x00\n"

    assert connector.send_command("show lldp neighbors") == "Port: Gi1/0/24\r\n"


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
# Rendering one SNMP value
#
# An OctetString holds bytes. What str() makes of them decides whether the
# value is storable at all - PostgreSQL holds no NUL - and whether it is
# usable, which for a chassis ID means being recognisable as a MAC.
# --------------------------------------------------------------------------


def _octets(raw: bytes):
    from pyasn1.type.univ import OctetString

    return OctetString(raw)


def test_a_padded_name_is_the_name():
    """Agents pad text to a fixed width; the padding is not part of the name"""
    assert SnmpClient._text(_octets(b"sw-core-01\x00\x00\x00")) == "sw-core-01"
    assert SnmpClient._text(_octets(b"\x00FOC2137L0AB")) == "FOC2137L0AB"


def test_descriptive_text_survives_intact():
    """A sysDescr is multi-line free text and must come back unchanged"""
    descr = b"Cisco IOS Software, C3850\nVersion 17.9\tRELEASE"
    assert SnmpClient._text(_octets(descr)) == descr.decode()


def test_binary_bytes_are_rendered_as_hex():
    """
    An LLDP chassis ID of the MAC-address subtype is six raw bytes

    Decoded as characters that is mojibake with a NUL in it - unstorable and
    useless. As hex it is the MAC the object was read for.
    """
    from app.services.parsers import normalize_mac

    rendered = SnmpClient._text(_octets(b"\x00\x1a\x2b\x3c\x4d\x5e"))

    assert rendered == "0x001a2b3c4d5e"
    assert normalize_mac(rendered) == "00:1a:2b:3c:4d:5e"


def test_a_value_that_is_neither_pads_nor_binary_is_left_alone():
    assert SnmpClient._text(_octets(b"GigabitEthernet1/0/24")) == (
        "GigabitEthernet1/0/24"
    )
    # Non-OctetString values render as themselves.
    assert SnmpClient._text(42) == "42"


def test_nothing_unstorable_survives_whatever_the_shape():
    """The property that matters, over every shape an agent can send"""
    for raw in (
        b"sw\x00-01",
        b"\x00\x1a\x2b\x3c\x4d\x5e",
        b"\xff\xfe binary junk \x00",
        b"plain",
        b"",
    ):
        rendered = SnmpClient._text(_octets(raw))
        assert "\x00" not in rendered
        # And it can actually be sent to PostgreSQL.
        rendered.encode("utf-8")


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
