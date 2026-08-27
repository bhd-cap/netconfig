"""
Parser tests using realistic device output for every supported vendor.

Fixtures reproduce the layout each platform actually prints, including the
header lines, separators and quirks (CPU entries, space-separated chassis ids,
fully qualified CDP names) that the parsers have to cope with.
"""
import pytest

from app.services import parsers
from app.config.discovery_commands import DISCOVERY_COMMANDS, get_discovery_command


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0011.2233.4455", "00:11:22:33:44:55"),
        ("00:11:22:33:44:55", "00:11:22:33:44:55"),
        ("00-11-22-33-44-55", "00:11:22:33:44:55"),
        ("001122-334455", "00:11:22:33:44:55"),
        ("0011-2233-4455", "00:11:22:33:44:55"),
        ("00 11 22 33 44 55", "00:11:22:33:44:55"),
        ("001122334455", "00:11:22:33:44:55"),
        ("AABB.CCDD.EEFF", "aa:bb:cc:dd:ee:ff"),
        ("not-a-mac", None),
        ("", None),
        (None, None),
        ("0011.2233", None),
    ],
)
def test_normalize_mac(raw, expected):
    assert parsers.normalize_mac(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Gi1/0/1", "GigabitEthernet1/0/1"),
        ("GigabitEthernet1/0/1", "GigabitEthernet1/0/1"),
        ("Te1/1/1", "TenGigabitEthernet1/1/1"),
        ("Fa0/1", "FastEthernet0/1"),
        ("Eth1/1", "Ethernet1/1"),
        ("ge-0/0/1.0", "ge-0/0/1"),
        ("Po1", "Port-channel1"),
        ("1/1/1", "1/1/1"),
        ("24", "24"),
        (None, None),
    ],
)
def test_canonical_interface(raw, expected):
    assert parsers.canonical_interface(raw) == expected


def test_invalid_ipv4_octets_rejected():
    assert parsers.find_ipv4("999.1.1.1 is not valid") is None
    assert parsers.find_ipv4("addr 10.20.30.40 here") == "10.20.30.40"


# --------------------------------------------------------------------------
# LLDP / CDP
# --------------------------------------------------------------------------

CISCO_LLDP = """
------------------------------------------------
Local Intf: Gi1/0/1
Chassis id: 0011.2233.4455
Port id: Gi1/0/24
Port Description: uplink to access
System Name: switch-core-01

System Description:
Cisco IOS Software, C3850 Software

Time remaining: 97 seconds
System Capabilities: B,R
Enabled Capabilities: B
Management Addresses:
    IP: 10.10.0.1
Auto Negotiation - supported, enabled

------------------------------------------------
Local Intf: Gi1/0/2
Chassis id: aabb.ccdd.eeff
Port id: GigabitEthernet0/1
System Name: router-edge-01.corp.example.com
System Description:
Cisco IOS XE Software

Time remaining: 105 seconds
Enabled Capabilities: R
Management Addresses:
    IP: 10.10.0.254

Total entries displayed: 2
"""

CISCO_CDP = """
-------------------------
Device ID: switch-core-01.corp.example.com
Entry address(es):
  IP address: 10.10.0.1
Platform: cisco WS-C3850-48P,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/24
Holdtime : 143 sec

Version :
Cisco IOS Software

-------------------------
Device ID: AP-lobby(FCW2140L0AB)
Entry address(es):
  IP address: 10.20.0.55
Platform: cisco AIR-AP2802I-B-K9,  Capabilities: Trans-Bridge
Interface: GigabitEthernet1/0/9,  Port ID (outgoing port): GigabitEthernet0
Holdtime : 167 sec
"""

JUNIPER_LLDP = """
LLDP Neighbor Information:
Local Information:
Index   1 Time   0:15:22

Local Interface    Parent Interface    Chassis Id          Port info          System Name
ge-0/0/1           -                   00:11:22:33:44:55   Gi1/0/24           switch-core-01
ge-0/0/2           ae0                 aa:bb:cc:dd:ee:ff   ge-0/0/5           switch-dist-02
xe-0/1/0           -                   00:1a:2b:3c:4d:5e   Ethernet1/1        spine-01
"""

COMWARE_LLDP = """
LLDP neighbor-information of port 1[GigabitEthernet1/0/1]:
  LLDP agent nearest-bridge:
    LLDP neighbor index : 1
    Update time         : 0 days,0 hours,3 minutes,12 seconds
    Chassis type        : MAC address
    Chassis ID          : 0011-2233-4455
    Port ID type        : Interface name
    Port ID             : GigabitEthernet1/0/24
    Port description    : uplink
    System name         : switch-core-01
    System description  : HPE Comware Platform Software
    System capabilities supported : Bridge,Router
    System capabilities enabled   : Bridge
    Management address  : 10.10.0.1

LLDP neighbor-information of port 2[GigabitEthernet1/0/2]:
  LLDP agent nearest-bridge:
    Chassis ID          : aabb-ccdd-eeff
    Port ID             : 1/1/3
    System name         : aruba-access-03
    Management address  : 10.10.0.9
"""

PROCURVE_LLDP = """
 LLDP Remote Devices Information

  LocalPort | ChassisId                 PortId PortDescr SysName
  --------- + ------------------------- ------ --------- --------------------
  1         | 00 11 22 33 44 55         24     uplink    switch-core-01
  2         | aa bb cc dd ee ff         5      trunk     switch-dist-02
"""

ARUBA_LLDP = """
LLDP Neighbor Information
=========================

Total Neighbor Entries : 2

LOCAL-PORT  CHASSIS-ID         PORT-ID   TTL   SYS-NAME
--------------------------------------------------------------
1/1/1       00:11:22:33:44:55  1/1/24    120   switch-core-01
1/1/2       aa:bb:cc:dd:ee:ff  Gi1/0/8   120   switch-access-07
"""

FORTINET_LLDP = """
Interface: port1
  Neighbor 1:
    Chassis ID: 00:11:22:33:44:55
    Port ID: GigabitEthernet1/0/24
    System Name: switch-core-01
    TTL: 120

Interface: port2
  Neighbor 1:
    Chassis ID: aa:bb:cc:dd:ee:ff
    Port ID: 1/1/5
    System Name: switch-dmz-01
"""


def test_cisco_lldp_detail():
    result = parsers.parse("cisco_lldp_detail", CISCO_LLDP)
    assert len(result.neighbors) == 2

    first = result.neighbors[0]
    assert first.local_interface == "Gi1/0/1"
    assert first.remote_hostname == "switch-core-01"
    assert first.remote_interface == "Gi1/0/24"
    assert first.remote_mgmt_ip == "10.10.0.1"
    assert first.remote_chassis_id == "00:11:22:33:44:55"
    assert first.protocol == "lldp"

    # A fully qualified system name is reduced to the hostname, so the same
    # device does not appear twice on a diagram.
    assert result.neighbors[1].remote_hostname == "router-edge-01"
    assert result.neighbors[1].remote_mgmt_ip == "10.10.0.254"


def test_cisco_cdp_detail():
    result = parsers.parse("cisco_cdp_detail", CISCO_CDP)
    assert len(result.neighbors) == 2

    first = result.neighbors[0]
    assert first.local_interface == "GigabitEthernet1/0/1"
    assert first.remote_hostname == "switch-core-01"
    assert first.remote_interface == "GigabitEthernet1/0/24"
    assert first.remote_mgmt_ip == "10.10.0.1"
    assert first.remote_platform == "cisco WS-C3850-48P"
    assert first.capabilities == "Switch IGMP"
    assert first.protocol == "cdp"

    # CDP appends the serial in parentheses; it is not part of the hostname.
    assert result.neighbors[1].remote_hostname == "AP-lobby"


def test_juniper_lldp():
    result = parsers.parse("juniper_lldp", JUNIPER_LLDP)
    assert len(result.neighbors) == 3

    assert result.neighbors[0].local_interface == "ge-0/0/1"
    assert result.neighbors[0].remote_hostname == "switch-core-01"
    assert result.neighbors[0].remote_interface == "Gi1/0/24"
    assert result.neighbors[0].remote_chassis_id == "00:11:22:33:44:55"

    # The parent-interface column (ae0) must not be mistaken for the chassis.
    assert result.neighbors[1].remote_hostname == "switch-dist-02"
    assert result.neighbors[1].remote_chassis_id == "aa:bb:cc:dd:ee:ff"


def test_comware_lldp_verbose():
    result = parsers.parse("comware_lldp_verbose", COMWARE_LLDP)
    assert len(result.neighbors) == 2

    first = result.neighbors[0]
    assert first.local_interface == "GigabitEthernet1/0/1"
    assert first.remote_hostname == "switch-core-01"
    assert first.remote_interface == "GigabitEthernet1/0/24"
    assert first.remote_mgmt_ip == "10.10.0.1"
    assert first.remote_chassis_id == "00:11:22:33:44:55"

    assert result.neighbors[1].remote_hostname == "aruba-access-03"


def test_procurve_lldp_remote():
    result = parsers.parse("procurve_lldp_remote", PROCURVE_LLDP)
    assert len(result.neighbors) == 2

    first = result.neighbors[0]
    assert first.local_interface == "1"
    assert first.remote_hostname == "switch-core-01"
    # ProCurve prints the chassis id as space separated octets.
    assert first.remote_chassis_id == "00:11:22:33:44:55"


def test_aruba_lldp_neighbor_info():
    result = parsers.parse("aruba_lldp_neighbor_info", ARUBA_LLDP)
    assert len(result.neighbors) == 2

    first = result.neighbors[0]
    assert first.local_interface == "1/1/1"
    assert first.remote_hostname == "switch-core-01"
    assert first.remote_chassis_id == "00:11:22:33:44:55"


def test_fortinet_lldp_summary():
    result = parsers.parse("fortinet_lldp_summary", FORTINET_LLDP)
    assert len(result.neighbors) == 2
    assert result.neighbors[0].local_interface == "port1"
    assert result.neighbors[0].remote_hostname == "switch-core-01"
    assert result.neighbors[1].remote_hostname == "switch-dmz-01"


# --------------------------------------------------------------------------
# MAC address tables
# --------------------------------------------------------------------------

CISCO_MAC = """
          Mac Address Table
-------------------------------------------

Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
 All    0100.0ccc.cccc    STATIC      CPU
 All    0180.c200.0000    STATIC      CPU
  10    0011.2233.4455    DYNAMIC     Gi1/0/1
  10    aabb.ccdd.eeff    DYNAMIC     Gi1/0/2
  20    0022.3344.5566    DYNAMIC     Gi1/0/3
  20    0033.4455.6677    STATIC      Gi1/0/4
Total Mac Addresses for this criterion: 6
"""

NXOS_MAC = """
Legend:
        * - primary entry, G - Gateway MAC, (R) - Routed MAC, O - Overlay MAC
   VLAN     MAC Address      Type      age     Secure NTFY Ports
---------+-----------------+--------+---------+------+----+------------------
* 10       0011.2233.4455   dynamic  0         F      F    Eth1/1
* 20       aabb.ccdd.eeff   dynamic  0         F      F    Eth1/2
G  -       0022.3344.5566   static   -         F      F    sup-eth1(R)
"""

COMWARE_MAC = """
MAC Address      VLAN ID    State            Port/Nickname            Aging
0011-2233-4455   10         Learned          GE1/0/1                  Y
aabb-ccdd-eeff   20         Learned          GE1/0/2                  Y
0022-3344-5566   1          Config static    GE1/0/3                  N
"""

PROCURVE_MAC = """
 Status and Counters - Port Address Table

  MAC Address       Port                       Type
  ----------------- -------------------------- ---------
  001122-334455     1                          Dynamic
  aabbcc-ddeeff     2                          Dynamic
  002233-445566     Trk1                       Dynamic
"""

ARUBA_MAC = """
MAC age-time            : 300 seconds
Number of MAC addresses : 3

MAC Address          VLAN     Type                      Port
--------------------------------------------------------------
00:11:22:33:44:55    10       dynamic                   1/1/1
aa:bb:cc:dd:ee:ff    10       dynamic                   1/1/2
00:22:33:44:55:66    20       static                    1/1/3
"""

JUNIPER_MAC = """
Ethernet-switching table: 3 entries, 2 learned
  VLAN              MAC address       Type         Age Interfaces
  v10               00:11:22:33:44:55 Learn          0 ge-0/0/1.0
  v10               aa:bb:cc:dd:ee:ff Learn          0 ge-0/0/2.0
  v20               00:22:33:44:55:66 Static         - ge-0/0/3.0
"""


def test_cisco_mac_table():
    result = parsers.parse("cisco_mac_table", CISCO_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    # CPU entries are the switch itself, not hosts on a port.
    assert "01:00:0c:cc:cc:cc" not in macs
    assert len(result.mac_entries) == 4

    assert macs["00:11:22:33:44:55"].interface == "Gi1/0/1"
    assert macs["00:11:22:33:44:55"].vlan == 10
    assert macs["00:11:22:33:44:55"].entry_type == "dynamic"
    assert macs["00:33:44:55:66:77"].entry_type == "static"


def test_nxos_mac_table():
    result = parsers.parse("cisco_mac_table", NXOS_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    assert macs["00:11:22:33:44:55"].interface == "Eth1/1"
    assert macs["00:11:22:33:44:55"].vlan == 10
    assert macs["aa:bb:cc:dd:ee:ff"].vlan == 20
    # The sup-eth1 routed entry has no usable VLAN and is the switch itself.
    assert "00:22:33:44:55:66" not in macs or macs["00:22:33:44:55:66"].vlan is None


def test_comware_mac_table():
    result = parsers.parse("comware_mac_table", COMWARE_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    assert len(result.mac_entries) == 3
    assert macs["00:11:22:33:44:55"].interface == "GE1/0/1"
    assert macs["00:11:22:33:44:55"].vlan == 10
    assert macs["aa:bb:cc:dd:ee:ff"].vlan == 20


def test_procurve_mac_table():
    result = parsers.parse("procurve_mac_table", PROCURVE_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    assert len(result.mac_entries) == 3
    assert macs["00:11:22:33:44:55"].interface == "1"
    assert macs["00:22:33:44:55:66"].interface == "Trk1"


def test_aruba_mac_table():
    result = parsers.parse("aruba_mac_table", ARUBA_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    assert len(result.mac_entries) == 3
    assert macs["00:11:22:33:44:55"].interface == "1/1/1"
    assert macs["00:11:22:33:44:55"].vlan == 10
    assert macs["00:22:33:44:55:66"].entry_type == "static"


def test_juniper_mac_table():
    result = parsers.parse("juniper_ethernet_switching", JUNIPER_MAC)
    macs = {entry.mac: entry for entry in result.mac_entries}

    assert len(result.mac_entries) == 3
    assert macs["00:11:22:33:44:55"].interface == "ge-0/0/1.0"
    assert macs["00:11:22:33:44:55"].vlan == 10


# --------------------------------------------------------------------------
# ARP
# --------------------------------------------------------------------------

CISCO_ARP = """
Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  10.10.0.1               -   0011.2233.4455  ARPA   Vlan10
Internet  10.10.0.50              5   aabb.ccdd.eeff  ARPA   Vlan10
Internet  10.20.0.7             120   0022.3344.5566  ARPA   Vlan20
"""

JUNIPER_ARP = """
MAC Address       Address         Name                      Interface    Flags
00:11:22:33:44:55 10.10.0.1       10.10.0.1                 ge-0/0/1.0   none
aa:bb:cc:dd:ee:ff 10.10.0.50      10.10.0.50                ge-0/0/2.0   none
Total entries: 2
"""

FORTINET_ARP = """
Address           Age(min)   Hardware Addr      Interface
10.10.0.1         0          00:11:22:33:44:55  port1
10.10.0.50        3          aa:bb:cc:dd:ee:ff  port2
"""


def test_cisco_arp():
    result = parsers.parse("cisco_arp", CISCO_ARP)
    assert len(result.arp_entries) == 3

    by_ip = {entry.ip_address: entry for entry in result.arp_entries}
    assert by_ip["10.10.0.1"].mac == "00:11:22:33:44:55"
    assert by_ip["10.10.0.1"].interface == "Vlan10"
    assert by_ip["10.20.0.7"].mac == "00:22:33:44:55:66"


def test_juniper_arp():
    result = parsers.parse("juniper_arp", JUNIPER_ARP)
    assert len(result.arp_entries) == 2
    by_ip = {entry.ip_address: entry for entry in result.arp_entries}
    assert by_ip["10.10.0.1"].mac == "00:11:22:33:44:55"
    assert by_ip["10.10.0.1"].interface == "ge-0/0/1.0"


def test_fortinet_arp():
    result = parsers.parse("fortinet_arp", FORTINET_ARP)
    assert len(result.arp_entries) == 2
    by_ip = {entry.ip_address: entry for entry in result.arp_entries}
    assert by_ip["10.10.0.1"].interface == "port1"


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------


@pytest.mark.parametrize("output_format", sorted(parsers.PARSERS))
def test_parsers_tolerate_junk(output_format):
    """No parser may raise, whatever the device printed."""
    for junk in (
        "",
        "\n\n\n",
        "% Invalid input detected at '^' marker.",
        "Command authorization failed",
        "\x1b[2J\x1b[H garbage \x00\xff",
        "-" * 400,
        "a\n" * 500,
    ):
        result = parsers.parse(output_format, junk)
        assert isinstance(result, parsers.ParseResult)
        assert result.neighbors == []
        assert result.mac_entries == []
        assert result.arp_entries == []


def test_unknown_format_returns_empty():
    result = parsers.parse("no_such_format", CISCO_MAC)
    assert result.mac_entries == []


def test_error_output_does_not_produce_neighbors():
    """An authorisation failure must not be read as a device called 'failed'."""
    result = parsers.parse("cisco_cdp_detail", "% Authorization failed for command")
    assert result.neighbors == []


def test_every_declared_format_has_a_parser():
    """Every format named in the command matrix must be implemented."""
    for device_type, capabilities in DISCOVERY_COMMANDS.items():
        for capability, spec in capabilities.items():
            assert spec["format"] in parsers.PARSERS, (
                f"{device_type}.{capability} declares format "
                f"{spec['format']!r} with no parser"
            )
            assert spec["command"], f"{device_type}.{capability} has no command"


def test_all_six_vendors_can_discover():
    """Each vendor the product claims to support must have LLDP or CDP."""
    for device_type in (
        "cisco_ios",
        "cisco_ios_xe",
        "cisco_nxos",
        "arista_eos",
        "fortinet",
        "juniper_junos",
        "aruba_os",
        "hp_comware",
        "hp_procurve",
    ):
        assert get_discovery_command(device_type, "lldp") is not None, device_type
