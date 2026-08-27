"""
Per-vendor commands for neighbour discovery and host inventory

Kept separate from device_types.py so the backup path stays untouched: a device
type that has no entry here simply cannot be discovered, it still backs up.

Each entry maps a capability to the command that produces it and the parser
format used to read the output. Formats are defined in
app.services.parsers - several vendors share one format.
"""
from typing import Dict, Any, List, Optional

# Capabilities:
#   lldp  - LLDP neighbours (link layer adjacency, all vendors)
#   cdp   - CDP neighbours (Cisco proprietary, also received by Arista)
#   mac   - MAC address table (which MAC is on which port)
#   arp   - ARP table (MAC to IP, so inventory can show addresses)
DISCOVERY_COMMANDS: Dict[str, Dict[str, Any]] = {
    "cisco_ios": {
        "lldp": {"command": "show lldp neighbors detail", "format": "cisco_lldp_detail"},
        "cdp": {"command": "show cdp neighbors detail", "format": "cisco_cdp_detail"},
        "mac": {"command": "show mac address-table", "format": "cisco_mac_table"},
        "arp": {"command": "show ip arp", "format": "cisco_arp"},
    },
    "cisco_ios_xe": {
        "lldp": {"command": "show lldp neighbors detail", "format": "cisco_lldp_detail"},
        "cdp": {"command": "show cdp neighbors detail", "format": "cisco_cdp_detail"},
        "mac": {"command": "show mac address-table", "format": "cisco_mac_table"},
        "arp": {"command": "show ip arp", "format": "cisco_arp"},
    },
    "cisco_nxos": {
        "lldp": {"command": "show lldp neighbors detail", "format": "cisco_lldp_detail"},
        "cdp": {"command": "show cdp neighbors detail", "format": "cisco_cdp_detail"},
        "mac": {"command": "show mac address-table", "format": "cisco_mac_table"},
        "arp": {"command": "show ip arp", "format": "cisco_arp"},
    },
    "arista_eos": {
        "lldp": {"command": "show lldp neighbors detail", "format": "cisco_lldp_detail"},
        # Arista receives CDP but does not originate it; the output format
        # follows Cisco's.
        "cdp": {"command": "show cdp neighbors detail", "format": "cisco_cdp_detail"},
        "mac": {"command": "show mac address-table", "format": "cisco_mac_table"},
        "arp": {"command": "show ip arp", "format": "cisco_arp"},
    },
    "fortinet": {
        # FortiOS exposes LLDP only as a summary table, and only on models
        # with a switch fabric. Firewalls have no MAC address table in the
        # switching sense, so inventory comes from ARP.
        "lldp": {
            "command": "get system lldp neighbors-summary",
            "format": "fortinet_lldp_summary",
        },
        "arp": {"command": "get system arp", "format": "fortinet_arp"},
    },
    "juniper_junos": {
        "lldp": {"command": "show lldp neighbors", "format": "juniper_lldp"},
        "mac": {
            "command": "show ethernet-switching table",
            "format": "juniper_ethernet_switching",
        },
        "arp": {"command": "show arp no-resolve", "format": "juniper_arp"},
    },
    "aruba_os": {
        "lldp": {
            "command": "show lldp neighbor-info",
            "format": "aruba_lldp_neighbor_info",
        },
        "mac": {"command": "show mac-address", "format": "aruba_mac_table"},
        "arp": {"command": "show arp", "format": "aruba_arp"},
    },
    "hp_comware": {
        "lldp": {
            "command": "display lldp neighbor-information verbose",
            "format": "comware_lldp_verbose",
        },
        "mac": {"command": "display mac-address", "format": "comware_mac_table"},
        "arp": {"command": "display arp", "format": "comware_arp"},
    },
    "hp_procurve": {
        "lldp": {
            "command": "show lldp info remote-device",
            "format": "procurve_lldp_remote",
        },
        "mac": {"command": "show mac-address", "format": "procurve_mac_table"},
        "arp": {"command": "show arp", "format": "procurve_arp"},
    },
}


def get_discovery_command(device_type: str, capability: str) -> Optional[Dict[str, str]]:
    """
    Get the command and parser format for one capability on a device type

    Args:
        device_type: Device type identifier
        capability: One of 'lldp', 'cdp', 'mac', 'arp'

    Returns:
        dict with 'command' and 'format', or None when the device type does
        not support that capability
    """
    return DISCOVERY_COMMANDS.get(device_type, {}).get(capability)


def get_capabilities(device_type: str) -> List[str]:
    """
    List the discovery capabilities a device type supports

    Args:
        device_type: Device type identifier

    Returns:
        list of capability names
    """
    return sorted(DISCOVERY_COMMANDS.get(device_type, {}).keys())


def supports_discovery(device_type: str) -> bool:
    """
    Whether any neighbour discovery is possible for this device type

    Args:
        device_type: Device type identifier

    Returns:
        bool
    """
    caps = DISCOVERY_COMMANDS.get(device_type, {})
    return "lldp" in caps or "cdp" in caps


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------
# Netmiko names its telnet drivers by suffixing the SSH driver. Only the
# device types with a real telnet driver are listed; the rest fall back to
# a generic terminal driver.
TELNET_DEVICE_TYPES: Dict[str, str] = {
    "cisco_ios": "cisco_ios_telnet",
    "cisco_ios_xe": "cisco_ios_telnet",
    "cisco_nxos": "cisco_ios_telnet",
    "arista_eos": "arista_eos_telnet",
    "juniper_junos": "juniper_junos_telnet",
    "aruba_os": "aruba_os",
    "hp_comware": "hp_comware_telnet",
    "hp_procurve": "hp_procurve_telnet",
    "fortinet": "generic_telnet",
}

SUPPORTED_TRANSPORTS = ("ssh", "telnet", "snmp")


def get_telnet_device_type(device_type: str) -> str:
    """
    Get the Netmiko driver to use when connecting over telnet

    Args:
        device_type: Device type identifier

    Returns:
        str: Netmiko driver name
    """
    return TELNET_DEVICE_TYPES.get(device_type, "generic_telnet")
