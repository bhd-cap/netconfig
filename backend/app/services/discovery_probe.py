"""
Authenticate against a device, work out what it is, and collect its facts

This is what decides whether a device belongs on a backup schedule. Before it
existed, a discovered device was registered as active with the seed's own
credentials and the seed's own device type, so a crawl from one Cisco switch
labelled every neighbour cisco_ios/ssh and put them all on the schedule -
where they failed nightly.

The order of operations matters:

1. SNMP first, when a community is available. It is read-only, cheap, and
   sysDescr identifies the platform better than anything else available
   without logging in. It also reaches devices that answer nothing else.
2. Then the CLI, trying each vault credential over SSH. A credential that is
   refused means try the next one; a connection that is refused outright means
   SSH is not listening, so stop and try telnet.
3. Only a device that actually authenticated is marked eligible for backup.
   Everything else stays in the inventory with a recorded reason.

Nothing here touches the database. The caller persists, so probes can run on
worker threads.
"""
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.config.device_types import DEVICE_TYPE_CONFIG
from app.services.credentials import CLI, SNMP, CredentialAttempt

logger = logging.getLogger(__name__)

# Results, narrowest cause first. 'auth_failed' means the device answered and
# rejected us, which is a very different problem from 'unreachable'.
SUCCESS = "success"
AUTH_FAILED = "auth_failed"
UNREACHABLE = "unreachable"
ERROR = "error"

# How long to wait for a bare TCP connect before deciding nothing is
# listening. Short on purpose: a crawl tries this against every neighbour, and
# a closed port answers immediately while a filtered one never will.
PORT_PROBE_TIMEOUT = 3.0

# Per-credential CLI login timeout. A device that is listening but slow to
# present a prompt needs more than the port probe.
LOGIN_TIMEOUT = 20

DEFAULT_PORTS = {"ssh": 22, "telnet": 23}


@dataclass
class ProbeOutcome:
    """What one transport probe found"""

    transport: str
    result: str
    credential_id: Optional[int] = None
    credential_name: Optional[str] = None
    attempts: int = 0
    message: str = ""
    duration_ms: int = 0
    # Anything learned about the device: sysName, model, serial, os_version,
    # and the device_type these imply.
    facts: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result == SUCCESS


@dataclass
class DeviceAssessment:
    """
    The whole picture for one device, across every transport tried

    `backup_eligible` is the answer the Devices page needs: a device is only
    worth scheduling if a CLI login succeeded, because SNMP cannot retrieve a
    configuration.
    """

    probes: List[ProbeOutcome] = field(default_factory=list)
    device_type: Optional[str] = None
    transport: Optional[str] = None
    credential_id: Optional[int] = None
    facts: Dict[str, Any] = field(default_factory=dict)
    backup_eligible: bool = False
    auth_status: str = "never"
    auth_error: Optional[str] = None

    def probe_for(self, transport: str) -> Optional[ProbeOutcome]:
        for probe in self.probes:
            if probe.transport == transport:
                return probe
        return None


# --------------------------------------------------------------------------
# Platform identification
# --------------------------------------------------------------------------

# Matched case-insensitively against sysDescr, an LLDP/CDP platform string or
# CLI version output. Ordered most specific first: "IOS-XE" has to beat
# "cisco", and "NX-OS" has to beat both.
PLATFORM_PATTERNS = (
    (r"nx-?os|nexus", "cisco_nxos"),
    (r"ios[\s-]?xe", "cisco_ios_xe"),
    (r"adaptive security|cisco asa", "cisco_ios"),
    (r"arista|veos", "arista_eos"),
    (r"fortigate|fortios|fortinet", "fortinet"),
    (r"junos|juniper", "juniper_junos"),
    (r"arubaos|aruba", "aruba_os"),
    (r"procurve|provision", "hp_procurve"),
    (r"comware|h3c|hpe? comware", "hp_comware"),
    (r"hewlett[\s-]?packard|\bhpe\b", "hp_procurve"),
    (r"cisco ios|internetwork operating system|\bcisco\b", "cisco_ios"),
)


def identify_platform(*sources: Optional[str]) -> Optional[str]:
    """
    Work out a device type from whatever text describes the device

    Args:
        *sources: sysDescr, an LLDP/CDP platform string, CLI version output -
            in the order they should be trusted

    Returns:
        A device type from the catalogue, or None when nothing matched. None
        matters: guessing cisco_ios for an unidentified device is what put
        every discovered device on the wrong driver.
    """
    for source in sources:
        if not source:
            continue

        lowered = str(source).lower()
        for pattern, device_type in PLATFORM_PATTERNS:
            if re.search(pattern, lowered):
                if device_type in DEVICE_TYPE_CONFIG:
                    return device_type

    return None


# Fields worth pulling out of a sysDescr, per platform. Best effort: firmware
# strings are not a format anyone promised to keep.
_VERSION_PATTERNS = (
    r"version[:\s]+([0-9][0-9a-zA-Z._()\-]+)",
    r"\bv([0-9]+\.[0-9][0-9a-zA-Z._\-]*)",
    r"\b([0-9]+\.[0-9]+\.[0-9]+[0-9a-zA-Z._\-]*)",
)


def parse_facts(sysdescr: Optional[str]) -> Dict[str, Any]:
    """
    Pull a model and an OS version out of a system description

    Args:
        sysdescr: The device's own description of itself

    Returns:
        dict with whatever could be recognised
    """
    facts: Dict[str, Any] = {}
    if not sysdescr:
        return facts

    text = " ".join(str(sysdescr).split())

    for pattern in _VERSION_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            facts["os_version"] = match.group(1).rstrip(".,;")
            break

    # A model number is a token carrying both letters and digits: C2960,
    # DCS-7050SX, ex4300-48t, J9146A, FortiGate-60F. Matching the first
    # capitalised word after "running on" instead picks up the vendor
    # ("Arista Networks DCS-7050SX" gives "Arista"), so look for the shape of
    # a model rather than its position.
    candidates = re.findall(r"\b(?=[A-Za-z0-9\-/]*\d)[A-Za-z][A-Za-z0-9\-/]{3,}\b", text)

    for candidate in candidates:
        # Skip anything that is really a version, a date or a build string.
        if re.fullmatch(r"[vV]?[\d.\-()]+", candidate):
            continue
        if candidate.lower().startswith(("version", "build", "revision", "release")):
            continue
        # A model has at least one letter and one digit, and is not purely a
        # decimal version like 15.0.
        if re.search(r"[A-Za-z]", candidate) and re.search(r"\d", candidate):
            facts["model"] = candidate
            break

    return facts


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------


def port_open(host: str, port: int, timeout: float = PORT_PROBE_TIMEOUT) -> bool:
    """
    Whether a TCP port accepts a connection

    Checked before spending a login timeout per credential: if SSH is not
    listening, trying six credentials against it wastes six timeouts to learn
    what one connect already said.

    Args:
        host: Address to test
        port: TCP port
        timeout: Seconds to wait

    Returns:
        bool
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# SNMP
# --------------------------------------------------------------------------


def probe_snmp(
    host: str,
    attempts: Sequence[CredentialAttempt],
    port: int = 161,
    timeout: int = 5,
) -> ProbeOutcome:
    """
    Read the system group over SNMP, trying each community in turn

    SNMP is used for identification and inventory only. It cannot retrieve a
    configuration, so a device that answers only SNMP is inventoried but never
    scheduled for backup.

    Args:
        host: Address to poll
        attempts: SNMP credentials, in the order to try
        port: SNMP port
        timeout: Per-attempt timeout in seconds

    Returns:
        ProbeOutcome with the facts read, or why nothing answered
    """
    from app.services.snmp_client import SnmpClient, SnmpError, SnmpUnavailable

    started = time.perf_counter()
    tried = 0
    last_message = "No SNMP credentials configured"

    for attempt in attempts:
        if attempt.kind != SNMP:
            continue
        tried += 1

        try:
            client = SnmpClient(
                host=host,
                port=port,
                version=attempt.snmp_version or "2c",
                community=attempt.community,
                v3_user=attempt.v3_user,
                v3_auth_key=attempt.v3_auth_key,
                v3_priv_key=attempt.v3_priv_key,
                v3_auth_protocol=attempt.v3_auth_protocol,
                v3_priv_protocol=attempt.v3_priv_protocol,
                timeout=timeout,
            )
            info = client.system_info()

        except SnmpUnavailable as e:
            # pysnmp missing: no credential will help, so stop.
            return ProbeOutcome(
                transport="snmp",
                result=ERROR,
                attempts=tried,
                message=str(e),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except (SnmpError, Exception) as e:  # noqa: BLE001 - try the next community
            last_message = str(e)
            logger.debug(f"SNMP probe of {host} with '{attempt.name}' failed: {e}")
            continue

        sysdescr = info.get("sysDescr")
        if not (sysdescr or info.get("sysName")):
            # An agent that answers but tells us nothing is no better than
            # silence for identification purposes.
            last_message = "SNMP answered but returned no system information"
            continue

        facts = {
            "snmp_sysname": info.get("sysName"),
            "snmp_sysdescr": sysdescr,
            "snmp_location": info.get("sysLocation"),
            "snmp_contact": info.get("sysContact"),
        }
        facts.update(parse_facts(sysdescr))

        device_type = identify_platform(sysdescr, info.get("sysName"))
        if device_type:
            facts["device_type"] = device_type

        return ProbeOutcome(
            transport="snmp",
            result=SUCCESS,
            credential_id=attempt.id,
            credential_name=attempt.name,
            attempts=tried,
            message=f"SNMP v{attempt.snmp_version} answered",
            duration_ms=int((time.perf_counter() - started) * 1000),
            facts={key: value for key, value in facts.items() if value},
        )

    return ProbeOutcome(
        transport="snmp",
        result=AUTH_FAILED if tried else UNREACHABLE,
        attempts=tried,
        message=last_message,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

# Commands that identify a platform, tried in order until one is understood.
# Run after a login succeeds, only when SNMP did not already identify it.
_VERSION_COMMANDS = (
    "show version",
    "display version",
    "get system status",
)


def _netmiko_driver(device_type: Optional[str], transport: str) -> str:
    """
    The Netmiko driver to log in with

    An unidentified device gets 'terminal_server', which opens the session and
    reads the banner without sending any platform-specific command. Guessing
    cisco_ios instead means Netmiko waits for a Cisco prompt that a Juniper
    box will never print, and the probe times out rather than reporting the
    truth.
    """
    from app.config.discovery_commands import get_telnet_device_type

    if not device_type or device_type not in DEVICE_TYPE_CONFIG:
        return "generic_telnet" if transport == "telnet" else "terminal_server"

    if transport == "telnet":
        return get_telnet_device_type(device_type) or "generic_telnet"

    from app.config.device_types import get_netmiko_device_type

    return get_netmiko_device_type(device_type)


def _login(
    host: str,
    port: int,
    transport: str,
    attempt: CredentialAttempt,
    device_type: Optional[str],
    timeout: int = LOGIN_TIMEOUT,
):
    """
    Open one CLI session

    Returns:
        The Netmiko connection

    Raises:
        NetmikoAuthenticationException on a rejected credential, and other
        Netmiko or OS errors when the device did not answer.
    """
    from netmiko import ConnectHandler

    params = {
        "device_type": _netmiko_driver(device_type, transport),
        "host": host,
        "username": attempt.username,
        "password": attempt.password or "",
        "port": port,
        "timeout": timeout,
        "auth_timeout": timeout,
        "banner_timeout": timeout,
        "fast_cli": False,
    }

    if attempt.enable_secret:
        params["secret"] = attempt.enable_secret
    if attempt.ssh_key_path and transport == "ssh":
        params["key_file"] = attempt.ssh_key_path

    return ConnectHandler(**params)


def _identify_over_cli(connection) -> Dict[str, Any]:
    """
    Ask an authenticated device what it is

    Args:
        connection: An open Netmiko session

    Returns:
        dict of facts, including device_type when it could be recognised
    """
    for command in _VERSION_COMMANDS:
        try:
            output = connection.send_command(
                command, read_timeout=15, expect_string=None
            )
        except Exception:  # noqa: BLE001 - the next command may be the right one
            continue

        if not output or len(output) < 20:
            continue
        if re.search(r"invalid|unknown command|syntax error|%\s*bad", output, re.I):
            continue

        facts: Dict[str, Any] = {"version_output": output[:2000]}
        facts.update(parse_facts(output))

        device_type = identify_platform(output)
        if device_type:
            facts["device_type"] = device_type

        serial = re.search(
            r"(?:serial number|system serial|serial)[:\s]+([A-Z0-9\-]{5,})",
            output,
            re.IGNORECASE,
        )
        if serial:
            facts["serial_number"] = serial.group(1)

        return facts

    return {}


def probe_cli(
    host: str,
    attempts: Sequence[CredentialAttempt],
    device_type: Optional[str] = None,
    ssh_port: int = 22,
    telnet_port: int = 23,
    identify: bool = True,
) -> List[ProbeOutcome]:
    """
    Try to log in, over SSH first and then telnet

    Every credential is tried over SSH before telnet is considered, because a
    device that accepts SSH should be reached that way. A credential the
    device *rejects* means try the next one; a device that refuses the
    connection outright means the port is not usable and further credentials
    would only repeat the same timeout.

    Args:
        host: Address to reach
        attempts: CLI credentials, in the order to try
        device_type: Known type, if any; drives which Netmiko driver is used
        ssh_port: SSH port
        telnet_port: Telnet port
        identify: Run a version command after logging in

    Returns:
        One outcome per transport tried, in the order tried
    """
    from netmiko.exceptions import NetmikoAuthenticationException

    cli_attempts = [attempt for attempt in attempts if attempt.kind == CLI]
    outcomes: List[ProbeOutcome] = []

    for transport, port in (("ssh", ssh_port), ("telnet", telnet_port)):
        started = time.perf_counter()

        if not port_open(host, port):
            outcomes.append(
                ProbeOutcome(
                    transport=transport,
                    result=UNREACHABLE,
                    message=f"Nothing listening on {transport} port {port}",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            continue

        if not cli_attempts:
            outcomes.append(
                ProbeOutcome(
                    transport=transport,
                    result=AUTH_FAILED,
                    message=(
                        f"{transport} is open but no CLI credentials are "
                        f"configured to try"
                    ),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            continue

        tried = 0
        last_message = ""

        for attempt in cli_attempts:
            tried += 1
            connection = None

            try:
                connection = _login(host, port, transport, attempt, device_type)

                facts = _identify_over_cli(connection) if identify else {}

                outcomes.append(
                    ProbeOutcome(
                        transport=transport,
                        result=SUCCESS,
                        credential_id=attempt.id,
                        credential_name=attempt.name,
                        attempts=tried,
                        message=f"Logged in over {transport} as {attempt.username}",
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        facts=facts,
                    )
                )
                return outcomes

            except NetmikoAuthenticationException as e:
                # The device answered and said no. Another credential might
                # work, so keep going.
                last_message = f"{attempt.label} rejected: {e}"
                logger.debug(f"{host} {transport}: {last_message}")
                continue

            except Exception as e:  # noqa: BLE001
                # Anything else - a timeout, a closed connection, a driver
                # mismatch. The port answered a TCP connect but the session
                # did not come up, and a different password will not change
                # that, so stop trying this transport.
                last_message = f"{transport} session failed: {e}"
                logger.debug(f"{host} {transport}: {last_message}")
                break

            finally:
                if connection is not None:
                    try:
                        connection.disconnect()
                    except Exception:  # noqa: BLE001
                        pass

        outcomes.append(
            ProbeOutcome(
                transport=transport,
                result=AUTH_FAILED,
                attempts=tried,
                message=last_message or "No credential was accepted",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )

    return outcomes


# --------------------------------------------------------------------------
# The whole assessment
# --------------------------------------------------------------------------


def assess(
    host: str,
    cli_attempts: Sequence[CredentialAttempt],
    snmp_attempts: Sequence[CredentialAttempt] = (),
    device_type: Optional[str] = None,
    platform_hint: Optional[str] = None,
    ssh_port: int = 22,
    telnet_port: int = 23,
    snmp_port: int = 161,
) -> DeviceAssessment:
    """
    Identify a device and decide whether it can be backed up

    Args:
        host: Address to probe
        cli_attempts: CLI credentials, in order
        snmp_attempts: SNMP credentials, in order
        device_type: A type already believed, if any
        platform_hint: What a neighbour said this device is, from LLDP/CDP
        ssh_port: SSH port
        telnet_port: Telnet port
        snmp_port: SNMP port

    Returns:
        DeviceAssessment
    """
    assessment = DeviceAssessment()

    # SNMP first: read-only, quick, and sysDescr identifies the platform
    # better than anything short of logging in.
    if snmp_attempts:
        snmp_outcome = probe_snmp(host, snmp_attempts, port=snmp_port)
        assessment.probes.append(snmp_outcome)

        if snmp_outcome.ok:
            assessment.facts.update(snmp_outcome.facts)

    # What we believe so far, best evidence first.
    identified = (
        assessment.facts.get("device_type")
        or identify_platform(platform_hint)
        or device_type
    )

    cli_outcomes = probe_cli(
        host,
        cli_attempts,
        device_type=identified,
        ssh_port=ssh_port,
        telnet_port=telnet_port,
    )
    assessment.probes.extend(cli_outcomes)

    successful = next((probe for probe in cli_outcomes if probe.ok), None)

    if successful:
        # A version command from an authenticated session beats every other
        # source, so it wins over the SNMP guess.
        assessment.facts.update(
            {key: value for key, value in successful.facts.items() if value}
        )
        identified = successful.facts.get("device_type") or identified

        assessment.transport = successful.transport
        assessment.credential_id = successful.credential_id
        assessment.backup_eligible = True
        assessment.auth_status = SUCCESS
    else:
        assessment.backup_eligible = False

        # Say which it was: nothing listening is a routing or firewall
        # problem, while a rejected login is a credentials problem.
        if all(probe.result == UNREACHABLE for probe in cli_outcomes):
            assessment.auth_status = UNREACHABLE
        else:
            assessment.auth_status = AUTH_FAILED

        assessment.auth_error = "; ".join(
            f"{probe.transport}: {probe.message}"
            for probe in cli_outcomes
            if probe.message
        )[:1000]

        # An SNMP-only device is still worth having: it is inventoried and can
        # be crawled, just never backed up.
        snmp_probe = assessment.probe_for("snmp")
        if snmp_probe and snmp_probe.ok:
            assessment.transport = "snmp"

    assessment.device_type = identified
    if identified:
        assessment.facts["device_type"] = identified

    return assessment


def try_credential(device, attempt: CredentialAttempt) -> ProbeOutcome:
    """
    Try one credential against one stored device

    Used by the "test this credential" endpoint, so an operator can find out
    which entry works without running a whole crawl.

    Args:
        device: A Device row
        attempt: The decrypted credential to try

    Returns:
        ProbeOutcome
    """
    if attempt.kind == SNMP:
        return probe_snmp(
            device.ip_address, [attempt], port=device.snmp_port or 161
        )

    outcomes = probe_cli(
        device.ip_address,
        [attempt],
        device_type=device.device_type,
        ssh_port=device.port or 22,
        telnet_port=device.port if device.transport == "telnet" else 23,
    )

    # Report the one that succeeded, or the most informative failure: a
    # rejected login says more than "nothing was listening".
    for outcome in outcomes:
        if outcome.ok:
            return outcome
    for outcome in outcomes:
        if outcome.result == AUTH_FAILED:
            return outcome

    return outcomes[0] if outcomes else ProbeOutcome(
        transport="ssh", result=ERROR, message="Nothing was attempted"
    )
