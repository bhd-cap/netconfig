"""
Parsers for network device command output

Turns the text a device prints into structured neighbour, MAC and ARP records.

Two deliberate choices run through this module:

* MAC and ARP tables are parsed by anchoring on the MAC address rather than on
  column positions. Every vendor - and often every software release - lays
  these tables out differently, but they all contain a recognisable MAC on the
  row, so anchoring on it survives column changes and extra fields that strict
  column parsing does not.
* Nothing raises on unparseable input. A device that prints an error, a pager
  prompt or an unexpected release format yields no rows rather than failing a
  whole discovery run.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class Neighbor:
    """One link-layer adjacency learned from LLDP or CDP"""

    local_interface: str
    remote_hostname: str
    protocol: str  # 'lldp' or 'cdp'
    remote_interface: Optional[str] = None
    remote_platform: Optional[str] = None
    remote_mgmt_ip: Optional[str] = None
    remote_chassis_id: Optional[str] = None
    capabilities: Optional[str] = None


@dataclass
class MacEntry:
    """One MAC address seen on a switch port"""

    mac: str
    interface: str
    vlan: Optional[int] = None
    entry_type: Optional[str] = None


@dataclass
class ArpEntry:
    """One IP-to-MAC binding"""

    ip_address: str
    mac: str
    interface: Optional[str] = None


@dataclass
class ParseResult:
    """Everything one command's output produced"""

    neighbors: List[Neighbor] = field(default_factory=list)
    mac_entries: List[MacEntry] = field(default_factory=list)
    arp_entries: List[ArpEntry] = field(default_factory=list)


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

# aabb.ccdd.eeff | aa:bb:cc:dd:ee:ff | aa-bb-cc-dd-ee-ff | aabb-ccdd-eeff |
# aa bb cc dd ee ff | aabbccddeeff
_MAC_PATTERNS = [
    re.compile(r"\b([0-9a-fA-F]{4}[.\-][0-9a-fA-F]{4}[.\-][0-9a-fA-F]{4})\b"),
    re.compile(r"\b((?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2})\b"),
    re.compile(r"\b((?:[0-9a-fA-F]{2} ){5}[0-9a-fA-F]{2})\b"),
    re.compile(r"\b([0-9a-fA-F]{6}-[0-9a-fA-F]{6})\b"),
    re.compile(r"\b([0-9a-fA-F]{12})\b"),
]

_IPV4 = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")

# Interfaces look like Gi1/0/1, ge-0/0/1.0, Ethernet1/1, port3, 1/1/1, A12, 24
_INTERFACE_HINT = re.compile(
    r"^(?:[A-Za-z][A-Za-z\-]*\d[\d/:.\-]*|\d+(?:/\d+)+(?:\.\d+)?|[A-Za-z]\d+|\d+)$"
)


def normalize_mac(value: Optional[str]) -> Optional[str]:
    """
    Normalise any vendor MAC notation to aa:bb:cc:dd:ee:ff

    Args:
        value: MAC in any common notation

    Returns:
        Canonical lowercase colon-separated MAC, or None if not a MAC
    """
    if not value:
        return None

    digits = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(digits) != 12:
        return None

    digits = digits.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def find_mac(text: str) -> Optional[str]:
    """
    Find the first MAC address anywhere in a line

    Args:
        text: Line of device output

    Returns:
        Canonical MAC, or None
    """
    for pattern in _MAC_PATTERNS:
        match = pattern.search(text)
        if match:
            mac = normalize_mac(match.group(1))
            if mac:
                return mac
    return None


def find_ipv4(text: str) -> Optional[str]:
    """
    Find the first syntactically valid IPv4 address in a line

    Args:
        text: Line of device output

    Returns:
        Dotted-quad address, or None
    """
    for candidate in _IPV4.findall(text):
        octets = candidate.split(".")
        if all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
            return candidate
    return None


# Abbreviation to canonical prefix. Devices print the same port as "Gi1/0/1"
# in one command and "GigabitEthernet1/0/1" in another; inventory has to join
# those together.
_INTERFACE_PREFIXES = [
    ("tengigabitethernet", "TenGigabitEthernet"),
    ("fortygigabitethernet", "FortyGigabitEthernet"),
    ("hundredgige", "HundredGigE"),
    ("gigabitethernet", "GigabitEthernet"),
    ("fastethernet", "FastEthernet"),
    ("twentyfivegige", "TwentyFiveGigE"),
    ("port-channel", "Port-channel"),
    ("ethernet", "Ethernet"),
    ("management", "Management"),
    ("vlan", "Vlan"),
    ("loopback", "Loopback"),
    ("tunnel", "Tunnel"),
    ("bridge-aggregation", "Bridge-Aggregation"),
    ("ten-gigabitethernet", "Ten-GigabitEthernet"),
    ("xgigabitethernet", "XGigabitEthernet"),
    ("te", "TenGigabitEthernet"),
    ("gi", "GigabitEthernet"),
    ("fa", "FastEthernet"),
    ("eth", "Ethernet"),
    ("et", "Ethernet"),
    ("po", "Port-channel"),
    ("mgmt", "Management"),
    ("lo", "Loopback"),
    ("ge", "GigabitEthernet"),
    ("xe", "TenGigabitEthernet"),
]


def canonical_interface(name: Optional[str]) -> Optional[str]:
    """
    Expand an abbreviated interface name to a canonical form

    Used only for matching, never for display: the name the device printed is
    what gets stored and shown.

    Args:
        name: Interface name as printed

    Returns:
        Canonical name, or None
    """
    if not name:
        return None

    cleaned = name.strip().strip(",")
    if not cleaned:
        return None

    # Juniper style (ge-0/0/1.0) is already unambiguous; leave it alone apart
    # from dropping the logical unit.
    if re.match(r"^[a-z]{2}-\d+/\d+/\d+", cleaned):
        return cleaned.split(".")[0]

    match = re.match(r"^([A-Za-z\-]+)\s*(.*)$", cleaned)
    if not match:
        return cleaned

    prefix, remainder = match.group(1).lower(), match.group(2)
    if not remainder:
        return cleaned

    for abbrev, expanded in _INTERFACE_PREFIXES:
        if prefix == abbrev:
            return f"{expanded}{remainder}"

    return cleaned


def _looks_like_interface(token: str) -> bool:
    """Whether a token could plausibly be an interface name"""
    if not token or len(token) > 40:
        return False
    return bool(_INTERFACE_HINT.match(token))


def _clean_lines(output: str) -> List[str]:
    """Split output into lines, dropping pager artefacts and blank lines"""
    lines = []
    for raw in output.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("--More--") or stripped == "<--- More --->":
            continue
        lines.append(line)
    return lines


def _split_blocks(output: str) -> List[List[str]]:
    """
    Split output into records separated by lines of dashes or equals

    Cisco's detail commands delimit each neighbour that way.
    """
    blocks: List[List[str]] = []
    current: List[str] = []

    for line in output.splitlines():
        if re.match(r"^\s*[-=]{4,}\s*$", line):
            if current:
                blocks.append(current)
                current = []
            continue
        if line.strip():
            current.append(line)

    if current:
        blocks.append(current)

    return blocks


def _field(block: List[str], *labels: str) -> Optional[str]:
    """
    Read a 'Label: value' field out of a block, trying each label in order

    Args:
        block: Lines of one record
        labels: Label names, matched case-insensitively

    Returns:
        The value, or None
    """
    for label in labels:
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*[:=]\s*(.+)$", re.IGNORECASE)
        for line in block:
            match = pattern.match(line)
            if match:
                value = match.group(1).strip()
                if value and value not in ("-", "N/A", "not advertised"):
                    return value
    return None


def _shorten_hostname(name: Optional[str]) -> Optional[str]:
    """
    Trim a device identifier down to a hostname

    CDP reports fully qualified names and sometimes appends a serial number in
    parentheses; both make the same device look like two nodes on a diagram.
    """
    if not name:
        return None

    cleaned = name.strip().strip('"')
    cleaned = re.sub(r"\(.*?\)\s*$", "", cleaned).strip()

    # Keep an IP or a MAC intact; only split real hostnames on the domain.
    if find_ipv4(cleaned) == cleaned or normalize_mac(cleaned):
        return cleaned

    return cleaned.split(".")[0] if "." in cleaned else cleaned


# --------------------------------------------------------------------------
# LLDP / CDP parsers
# --------------------------------------------------------------------------


def parse_cisco_lldp_detail(output: str) -> ParseResult:
    """Parse 'show lldp neighbors detail' (Cisco IOS/NX-OS, Arista)"""
    result = ParseResult()

    for block in _split_blocks(output):
        local = _field(block, "Local Intf", "Local Interface", "Local Port id")
        system_name = _field(block, "System Name", "SysName")
        port_id = _field(block, "Port id", "Port ID")
        chassis = _field(block, "Chassis id", "Chassis ID")

        if not local and not system_name:
            continue

        # The management address sits on its own line under a header.
        mgmt_ip = _field(block, "Management Addresses", "Management Address", "IP")
        if not mgmt_ip:
            for index, line in enumerate(block):
                if re.search(r"management address", line, re.IGNORECASE):
                    for following in block[index : index + 4]:
                        mgmt_ip = find_ipv4(following)
                        if mgmt_ip:
                            break
                    break
        else:
            mgmt_ip = find_ipv4(mgmt_ip) or None

        hostname = _shorten_hostname(system_name) or _shorten_hostname(chassis)
        if not hostname:
            continue

        result.neighbors.append(
            Neighbor(
                local_interface=local or "",
                remote_hostname=hostname,
                protocol="lldp",
                remote_interface=port_id,
                remote_platform=_field(block, "System Description", "System Descr"),
                remote_mgmt_ip=mgmt_ip,
                remote_chassis_id=normalize_mac(chassis) or chassis,
                capabilities=_field(
                    block, "Enabled Capabilities", "System Capabilities"
                ),
            )
        )

    return result


def parse_cisco_cdp_detail(output: str) -> ParseResult:
    """Parse 'show cdp neighbors detail' (Cisco, Arista)"""
    result = ParseResult()

    for block in _split_blocks(output):
        device_id = _field(block, "Device ID")
        if not device_id:
            continue

        local = None
        remote_port = None
        for line in block:
            # "Interface: Gi0/1,  Port ID (outgoing port): Gi1/0/24"
            match = re.search(
                r"Interface\s*:\s*([^,]+),\s*Port ID\s*\(outgoing port\)\s*:\s*(.+)$",
                line,
                re.IGNORECASE,
            )
            if match:
                local = match.group(1).strip()
                remote_port = match.group(2).strip()
                break

        platform = None
        capabilities = None
        for line in block:
            match = re.search(
                r"Platform\s*:\s*(.+?),\s*Capabilities\s*:\s*(.*)$", line, re.IGNORECASE
            )
            if match:
                platform = match.group(1).strip()
                capabilities = match.group(2).strip()
                break
        if platform is None:
            platform = _field(block, "Platform")

        mgmt_ip = None
        for index, line in enumerate(block):
            if re.search(r"(entry address|management address)", line, re.IGNORECASE):
                for following in block[index : index + 4]:
                    mgmt_ip = find_ipv4(following)
                    if mgmt_ip:
                        break
                if mgmt_ip:
                    break
        if not mgmt_ip:
            ip_line = _field(block, "IP address", "IPv4 Address")
            mgmt_ip = find_ipv4(ip_line) if ip_line else None

        result.neighbors.append(
            Neighbor(
                local_interface=local or "",
                remote_hostname=_shorten_hostname(device_id) or device_id,
                protocol="cdp",
                remote_interface=remote_port,
                remote_platform=platform,
                remote_mgmt_ip=mgmt_ip,
                capabilities=capabilities,
            )
        )

    return result


def parse_juniper_lldp(output: str) -> ParseResult:
    """Parse 'show lldp neighbors' (Junos, tabular)"""
    result = ParseResult()

    for line in _clean_lines(output):
        if re.search(r"local\s+interface", line, re.IGNORECASE):
            continue

        tokens = line.split()
        if len(tokens) < 3:
            continue

        local = tokens[0]
        if not _looks_like_interface(local):
            continue

        # Columns: Local Interface | Parent | Chassis Id | Port info | System Name
        # Parent is often "-", and System Name can be missing.
        chassis_index = next(
            (i for i, token in enumerate(tokens) if normalize_mac(token)), None
        )

        if chassis_index is None:
            continue

        remaining = tokens[chassis_index + 1 :]
        remote_port = remaining[0] if remaining else None
        hostname = remaining[1] if len(remaining) > 1 else None

        if not hostname:
            hostname = normalize_mac(tokens[chassis_index])

        result.neighbors.append(
            Neighbor(
                local_interface=local,
                remote_hostname=_shorten_hostname(hostname) or hostname,
                protocol="lldp",
                remote_interface=remote_port,
                remote_chassis_id=normalize_mac(tokens[chassis_index]),
            )
        )

    return result


def parse_comware_lldp_verbose(output: str) -> ParseResult:
    """Parse 'display lldp neighbor-information verbose' (HPE Comware)"""
    result = ParseResult()

    # Each port's neighbours start with a header naming the local port.
    port_header = re.compile(
        r"LLDP\s+neighbor-information\s+of\s+port\s+\d+\s*\[([^\]]+)\]", re.IGNORECASE
    )

    current_port: Optional[str] = None
    block: List[str] = []
    blocks: List[tuple] = []

    for line in output.splitlines():
        match = port_header.search(line)
        if match:
            if current_port and block:
                blocks.append((current_port, block))
            current_port = match.group(1).strip()
            block = []
            continue
        if line.strip():
            block.append(line)

    if current_port and block:
        blocks.append((current_port, block))

    for local, lines in blocks:
        system_name = _field(lines, "System name", "System Name")
        chassis = _field(lines, "Chassis ID")
        hostname = _shorten_hostname(system_name) or _shorten_hostname(chassis)
        if not hostname:
            continue

        mgmt = _field(lines, "Management address", "Management Address")

        result.neighbors.append(
            Neighbor(
                local_interface=local,
                remote_hostname=hostname,
                protocol="lldp",
                remote_interface=_field(lines, "Port ID"),
                remote_platform=_field(lines, "System description"),
                remote_mgmt_ip=find_ipv4(mgmt) if mgmt else None,
                remote_chassis_id=normalize_mac(chassis) or chassis,
                capabilities=_field(lines, "System capabilities enabled"),
            )
        )

    return result


def _parse_tabular_lldp(output: str, port_first: bool = True) -> ParseResult:
    """
    Parse a tabular LLDP listing

    Shared by HPE ProCurve and ArubaOS, whose layouts differ in column order
    and separators but not in structure: one row per neighbour, containing a
    local port, a chassis id and a system name.
    """
    result = ParseResult()

    for line in _clean_lines(output):
        if re.match(r"^\s*[-+|\s]+$", line):
            continue
        if re.search(r"(local\s*-?\s*port|localport)", line, re.IGNORECASE):
            continue

        row = line.replace("|", " ")
        tokens = row.split()
        if len(tokens) < 2:
            continue

        local = tokens[0] if port_first else None
        if port_first and not _looks_like_interface(local):
            continue

        # ProCurve prints chassis ids as space separated octet pairs, which
        # split() breaks apart; rejoin before looking for a MAC.
        chassis = find_mac(row)

        # The system name is the last token that is not a MAC fragment or a
        # pure number (TTL).
        hostname = None
        for token in reversed(tokens[1:]):
            if normalize_mac(token) or token.isdigit() or len(token) <= 2:
                continue
            if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                continue
            hostname = token
            break

        if not hostname:
            hostname = chassis
        if not hostname:
            continue

        remote_port = None
        for token in tokens[1:]:
            if token == hostname or normalize_mac(token):
                continue
            if _looks_like_interface(token):
                remote_port = token
                break

        result.neighbors.append(
            Neighbor(
                local_interface=local or "",
                remote_hostname=_shorten_hostname(hostname) or hostname,
                protocol="lldp",
                remote_interface=remote_port,
                remote_chassis_id=chassis,
            )
        )

    return result


def parse_procurve_lldp_remote(output: str) -> ParseResult:
    """Parse 'show lldp info remote-device' (HPE ProCurve)"""
    return _parse_tabular_lldp(output)


def parse_aruba_lldp_neighbor_info(output: str) -> ParseResult:
    """Parse 'show lldp neighbor-info' (ArubaOS / AOS-CX)"""
    return _parse_tabular_lldp(output)


def parse_fortinet_lldp_summary(output: str) -> ParseResult:
    """Parse 'get system lldp neighbors-summary' (FortiOS)"""
    result = ParseResult()

    current_port: Optional[str] = None
    block: List[str] = []
    blocks: List[tuple] = []

    for line in output.splitlines():
        match = re.match(r"^\s*Interface\s*:\s*(\S+)", line, re.IGNORECASE)
        if match:
            if current_port and block:
                blocks.append((current_port, block))
            current_port = match.group(1).strip()
            block = []
            continue
        if line.strip():
            block.append(line)

    if current_port and block:
        blocks.append((current_port, block))

    for local, lines in blocks:
        chassis = _field(lines, "Chassis ID")
        system_name = _field(lines, "System Name")
        hostname = _shorten_hostname(system_name) or _shorten_hostname(chassis)
        if not hostname:
            continue

        result.neighbors.append(
            Neighbor(
                local_interface=local,
                remote_hostname=hostname,
                protocol="lldp",
                remote_interface=_field(lines, "Port ID"),
                remote_chassis_id=normalize_mac(chassis) or chassis,
            )
        )

    return result


# --------------------------------------------------------------------------
# MAC address table parsers
# --------------------------------------------------------------------------

_MAC_TABLE_SKIP = re.compile(
    r"(mac address table|address table|total|entries|aging|^\s*[-=+]+\s*$|"
    r"vlan\s+mac|mac\s+address\s+port|legend|multicast|^\s*\*?\s*$)",
    re.IGNORECASE,
)

# Ports that are the switch itself, not a real edge port.
_NON_PORT_TOKENS = {"cpu", "self", "router", "drop", "-", "n/a", "switch"}


def _parse_generic_mac_table(output: str) -> ParseResult:
    """
    Parse any vendor's MAC address table

    Anchors on the MAC in each row rather than on column positions, then takes
    the VLAN from the numeric field before it and the port from the last
    interface-shaped token on the row. This handles the IOS, NX-OS, Comware,
    ProCurve and AOS-CX layouts with one implementation.
    """
    result = ParseResult()
    seen = set()

    for line in _clean_lines(output):
        mac = find_mac(line)
        if not mac:
            continue
        if _MAC_TABLE_SKIP.match(line.strip()):
            continue

        tokens = line.replace("|", " ").split()

        mac_index = None
        for index, token in enumerate(tokens):
            if normalize_mac(token) == mac:
                mac_index = index
                break

        # ProCurve prints "00 11 22 33 44 55"; the MAC spans six tokens.
        if mac_index is None:
            for index in range(len(tokens) - 5):
                joined = "".join(tokens[index : index + 6])
                if normalize_mac(joined) == mac:
                    mac_index = index
                    tokens = tokens[:index] + [mac] + tokens[index + 6 :]
                    break

        if mac_index is None:
            continue

        vlan = None
        for token in tokens[:mac_index]:
            candidate = token.lstrip("*").lstrip("v").rstrip(",")
            if candidate.isdigit() and 1 <= int(candidate) <= 4094:
                vlan = int(candidate)
        # Comware and AOS-CX put the VLAN after the MAC.
        if vlan is None:
            for token in tokens[mac_index + 1 :]:
                candidate = token.lstrip("*").lstrip("v")
                if candidate.isdigit() and 1 <= int(candidate) <= 4094:
                    vlan = int(candidate)
                    break

        interface = None
        for token in reversed(tokens[mac_index + 1 :]):
            if token.lower() in _NON_PORT_TOKENS:
                continue
            if normalize_mac(token):
                continue
            if _looks_like_interface(token):
                interface = token
                break

        if not interface:
            continue
        if interface.lower() in _NON_PORT_TOKENS:
            continue

        entry_type = None
        for token in tokens:
            lowered = token.lower().strip(",")
            if lowered in ("dynamic", "static", "learned", "learn", "d", "s", "self"):
                entry_type = {"d": "dynamic", "s": "static", "learn": "dynamic",
                              "learned": "dynamic"}.get(lowered, lowered)
                break

        key = (mac, interface, vlan)
        if key in seen:
            continue
        seen.add(key)

        result.mac_entries.append(
            MacEntry(mac=mac, interface=interface, vlan=vlan, entry_type=entry_type)
        )

    return result


def parse_cisco_mac_table(output: str) -> ParseResult:
    """Parse 'show mac address-table' (Cisco IOS/NX-OS, Arista)"""
    return _parse_generic_mac_table(output)


def parse_comware_mac_table(output: str) -> ParseResult:
    """Parse 'display mac-address' (HPE Comware)"""
    return _parse_generic_mac_table(output)


def parse_procurve_mac_table(output: str) -> ParseResult:
    """Parse 'show mac-address' (HPE ProCurve)"""
    return _parse_generic_mac_table(output)


def parse_aruba_mac_table(output: str) -> ParseResult:
    """Parse 'show mac-address' (ArubaOS / AOS-CX)"""
    return _parse_generic_mac_table(output)


def parse_juniper_ethernet_switching(output: str) -> ParseResult:
    """Parse 'show ethernet-switching table' (Junos)"""
    return _parse_generic_mac_table(output)


# --------------------------------------------------------------------------
# ARP parsers
# --------------------------------------------------------------------------


def _parse_generic_arp(output: str) -> ParseResult:
    """
    Parse any vendor's ARP table

    Anchors on rows that contain both an IPv4 address and a MAC.
    """
    result = ParseResult()
    seen = set()

    for line in _clean_lines(output):
        mac = find_mac(line)
        ip_address = find_ipv4(line)
        if not mac or not ip_address:
            continue

        tokens = line.replace("|", " ").split()

        interface = None
        for token in reversed(tokens):
            if token == ip_address or normalize_mac(token):
                continue
            if token.isdigit():
                continue
            if _looks_like_interface(token):
                interface = token
                break

        if (ip_address, mac) in seen:
            continue
        seen.add((ip_address, mac))

        result.arp_entries.append(
            ArpEntry(ip_address=ip_address, mac=mac, interface=interface)
        )

    return result


def parse_cisco_arp(output: str) -> ParseResult:
    """Parse 'show ip arp' (Cisco, Arista)"""
    return _parse_generic_arp(output)


def parse_juniper_arp(output: str) -> ParseResult:
    """Parse 'show arp no-resolve' (Junos)"""
    return _parse_generic_arp(output)


def parse_fortinet_arp(output: str) -> ParseResult:
    """Parse 'get system arp' (FortiOS)"""
    return _parse_generic_arp(output)


def parse_aruba_arp(output: str) -> ParseResult:
    """Parse 'show arp' (ArubaOS)"""
    return _parse_generic_arp(output)


def parse_comware_arp(output: str) -> ParseResult:
    """Parse 'display arp' (HPE Comware)"""
    return _parse_generic_arp(output)


def parse_procurve_arp(output: str) -> ParseResult:
    """Parse 'show arp' (HPE ProCurve)"""
    return _parse_generic_arp(output)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

PARSERS: Dict[str, Callable[[str], ParseResult]] = {
    "cisco_lldp_detail": parse_cisco_lldp_detail,
    "cisco_cdp_detail": parse_cisco_cdp_detail,
    "juniper_lldp": parse_juniper_lldp,
    "comware_lldp_verbose": parse_comware_lldp_verbose,
    "procurve_lldp_remote": parse_procurve_lldp_remote,
    "aruba_lldp_neighbor_info": parse_aruba_lldp_neighbor_info,
    "fortinet_lldp_summary": parse_fortinet_lldp_summary,
    "cisco_mac_table": parse_cisco_mac_table,
    "comware_mac_table": parse_comware_mac_table,
    "procurve_mac_table": parse_procurve_mac_table,
    "aruba_mac_table": parse_aruba_mac_table,
    "juniper_ethernet_switching": parse_juniper_ethernet_switching,
    "cisco_arp": parse_cisco_arp,
    "juniper_arp": parse_juniper_arp,
    "fortinet_arp": parse_fortinet_arp,
    "aruba_arp": parse_aruba_arp,
    "comware_arp": parse_comware_arp,
    "procurve_arp": parse_procurve_arp,
}


def parse(output_format: str, output: str) -> ParseResult:
    """
    Parse device output using the named format

    Never raises on bad input: an unknown format or unparseable text yields an
    empty result, so one odd device cannot fail a discovery run.

    Args:
        output_format: Format name from DISCOVERY_COMMANDS
        output: Raw device output

    Returns:
        ParseResult
    """
    parser = PARSERS.get(output_format)
    if parser is None or not output:
        return ParseResult()

    try:
        return parser(output)
    except Exception:  # noqa: BLE001 - a parser must never break a run
        import logging

        logging.getLogger(__name__).exception(
            "Parser %s failed; returning no rows", output_format
        )
        return ParseResult()
