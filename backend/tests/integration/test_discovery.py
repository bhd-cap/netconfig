"""
Discovery and inventory tests against a real database.

Device sessions are mocked at the connector boundary - the parsers already
have their own tests against real output - so these exercise the upsert
semantics, first/last seen behaviour, neighbour matching and the crawl.
"""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import (
    Device,
    HostInventory,
    Neighbor,
    Organization,
    OuiVendor,
    User,
    DiscoveryRun,
)
from app.services import parsers
from app.services.discovery import (
    DiscoveryService,
    ProbeResult,
    guess_device_type,
)
from app.services.config_retriever import DeviceSnapshot
from app.services.oui import clear as clear_oui, import_entries, oui_lookup
from app.utils.encryption import encryption_service


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def org(db):
    from sqlalchemy import text

    db.execute(
        text(
            "TRUNCATE organizations, users, devices, configurations, backup_jobs, "
            "audit_logs, neighbors, host_inventory, discovery_runs, roles, "
            "app_settings, backup_targets, topology_diagrams RESTART IDENTITY CASCADE"
        )
    )
    db.commit()

    organization = Organization(name="DiscoveryTest", is_active=True)
    db.add(organization)
    db.commit()
    return organization


@pytest.fixture
def devices(db, org):
    made = []
    for index, (hostname, ip) in enumerate(
        [("core-01", "10.0.0.1"), ("dist-01", "10.0.0.2"), ("access-01", "10.0.0.3")]
    ):
        device = Device(
            organization_id=org.id,
            hostname=hostname,
            ip_address=ip,
            device_type="cisco_ios",
            port=22,
            username="admin",
            encrypted_password=encryption_service.encrypt("pw"),
            is_active=True,
        )
        db.add(device)
        made.append(device)
    db.commit()
    return made


@pytest.fixture
def service(db):
    return DiscoveryService(db)


# --------------------------------------------------------------------------
# Neighbour upsert
# --------------------------------------------------------------------------


def test_neighbors_upsert_rather_than_duplicate(db, org, devices, service):
    neighbors = [
        parsers.Neighbor(
            local_interface="Gi1/0/1",
            remote_hostname="dist-01",
            protocol="lldp",
            remote_interface="Gi1/0/24",
            remote_mgmt_ip="10.0.0.2",
        )
    ]

    first_seen = datetime.now(timezone.utc) - timedelta(days=1)
    service.save_neighbors(org.id, devices[0].id, neighbors, seen_at=first_seen)
    db.commit()

    later = datetime.now(timezone.utc)
    service.save_neighbors(org.id, devices[0].id, neighbors, seen_at=later)
    db.commit()

    rows = db.execute(select(Neighbor)).scalars().all()
    assert len(rows) == 1, "the same link must not insert twice"

    row = rows[0]
    assert row.first_seen.replace(microsecond=0) == first_seen.replace(microsecond=0)
    assert row.last_seen > row.first_seen
    assert row.is_active is True


def test_neighbor_without_remote_port_still_upserts(db, org, devices, service):
    """A neighbour that reports no port must not insert on every run."""
    neighbors = [
        parsers.Neighbor(
            local_interface="Gi1/0/5", remote_hostname="ap-lobby", protocol="cdp"
        )
    ]

    for _ in range(3):
        service.save_neighbors(org.id, devices[0].id, neighbors)
        db.commit()

    rows = db.execute(select(Neighbor)).scalars().all()
    assert len(rows) == 1
    assert rows[0].remote_interface == ""


def test_duplicate_link_from_lldp_and_cdp_stored_once(db, org, devices, service):
    """A device running both protocols reports each link twice."""
    both = [
        parsers.Neighbor(
            local_interface="Gi1/0/1", remote_hostname="dist-01",
            protocol="lldp", remote_interface="Gi1/0/24",
        ),
        parsers.Neighbor(
            local_interface="Gi1/0/1", remote_hostname="dist-01",
            protocol="cdp", remote_interface="Gi1/0/24",
        ),
    ]
    written = service.save_neighbors(org.id, devices[0].id, both)
    db.commit()

    assert written == 1
    assert len(db.execute(select(Neighbor)).scalars().all()) == 1


def test_match_neighbors_to_managed_devices(db, org, devices, service):
    service.save_neighbors(
        org.id,
        devices[0].id,
        [
            parsers.Neighbor(
                local_interface="Gi1/0/1", remote_hostname="dist-01",
                protocol="lldp", remote_interface="Gi1/0/24",
            ),
            parsers.Neighbor(
                local_interface="Gi1/0/2", remote_hostname="unknown-box",
                protocol="lldp", remote_interface="eth0",
            ),
        ],
    )
    db.commit()

    matched = service.match_neighbors_to_devices(org.id)
    db.commit()

    assert matched == 1
    rows = {n.remote_hostname: n for n in db.execute(select(Neighbor)).scalars()}
    assert rows["dist-01"].remote_device_id == devices[1].id
    assert rows["unknown-box"].remote_device_id is None


def test_match_by_management_ip_when_hostname_differs(db, org, devices, service):
    """CDP often reports an FQDN or a different case than the device record."""
    service.save_neighbors(
        org.id,
        devices[0].id,
        [
            parsers.Neighbor(
                local_interface="Gi1/0/3", remote_hostname="renamed-in-dns",
                protocol="cdp", remote_interface="Gi0/1",
                remote_mgmt_ip="10.0.0.3",
            )
        ],
    )
    db.commit()
    service.match_neighbors_to_devices(org.id)
    db.commit()

    row = db.execute(select(Neighbor)).scalars().one()
    assert row.remote_device_id == devices[2].id


# --------------------------------------------------------------------------
# Inventory upsert and last-seen
# --------------------------------------------------------------------------


def test_inventory_records_first_and_last_seen(db, org, devices, service):
    entries = [
        parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/1", vlan=10,
                         entry_type="dynamic")
    ]

    first = datetime.now(timezone.utc) - timedelta(days=3)
    service.save_inventory(org.id, devices[0].id, entries, seen_at=first)
    db.commit()

    later = datetime.now(timezone.utc)
    service.save_inventory(org.id, devices[0].id, entries, seen_at=later)
    db.commit()

    row = db.execute(select(HostInventory)).scalars().one()
    assert row.first_seen.replace(microsecond=0) == first.replace(microsecond=0)
    assert row.last_seen > row.first_seen
    assert row.vlan == 10
    assert row.entry_type == "dynamic"


def test_inventory_without_vlan_upserts(db, org, devices, service):
    """A table with no VLAN column must still key correctly."""
    entries = [parsers.MacEntry(mac="00:11:22:33:44:55", interface="1")]

    for _ in range(3):
        service.save_inventory(org.id, devices[0].id, entries)
        db.commit()

    rows = db.execute(select(HostInventory)).scalars().all()
    assert len(rows) == 1
    assert rows[0].vlan == 0


def test_host_moving_port_keeps_both_rows(db, org, devices, service):
    """Moving a host must not erase where it used to be."""
    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/1", vlan=10)],
    )
    db.commit()
    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/9", vlan=10)],
    )
    db.commit()

    rows = db.execute(select(HostInventory)).scalars().all()
    assert len(rows) == 2
    assert {row.interface for row in rows} == {"Gi1/0/1", "Gi1/0/9"}


def test_arp_supplies_ip_and_is_not_lost_on_a_later_sweep(db, org, devices, service):
    mac = "00:11:22:33:44:55"
    entries = [parsers.MacEntry(mac=mac, interface="Gi1/0/1", vlan=10)]

    service.save_inventory(
        org.id, devices[0].id, entries,
        arp_entries=[parsers.ArpEntry(ip_address="10.20.0.50", mac=mac)],
    )
    db.commit()
    assert db.execute(select(HostInventory)).scalars().one().ip_address == "10.20.0.50"

    # A sweep with no ARP data must not wipe the address we already knew.
    service.save_inventory(org.id, devices[0].id, entries, arp_entries=[])
    db.commit()
    assert db.execute(select(HostInventory)).scalars().one().ip_address == "10.20.0.50"


def test_stale_rows_are_deactivated_not_deleted(db, org, devices, service):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/1", vlan=10)],
        seen_at=old,
    )
    service.save_neighbors(
        org.id, devices[0].id,
        [parsers.Neighbor(local_interface="Gi1/0/1", remote_hostname="gone",
                          protocol="lldp", remote_interface="x")],
        seen_at=old,
    )
    db.commit()

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    neighbors, hosts = service.mark_stale(org.id, cutoff)
    db.commit()

    assert (neighbors, hosts) == (1, 1)
    # The rows survive, so "last seen 30 days ago" is still answerable.
    assert db.execute(select(HostInventory)).scalars().one().is_active is False
    assert db.execute(select(Neighbor)).scalars().one().is_active is False


# --------------------------------------------------------------------------
# OUI vendor mapping
# --------------------------------------------------------------------------


def test_vendor_resolved_from_oui_table(db, org, devices, service):
    clear_oui(db)
    import_entries(db, [("001122", "Acme Networks")])

    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/1", vlan=10)],
    )
    db.commit()

    assert db.execute(select(HostInventory)).scalars().one().vendor == "Acme Networks"


def test_randomised_mac_is_labelled_not_looked_up(db, org, devices, service):
    clear_oui(db)
    import_entries(db, [("021122", "Should Not Be Used")])

    # 0x02 has the locally-administered bit set: a randomised address.
    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="02:11:22:33:44:55", interface="Gi1/0/2", vlan=10)],
    )
    db.commit()

    vendor = db.execute(select(HostInventory)).scalars().one().vendor
    assert vendor == "Locally administered (randomised)"


def test_unknown_prefix_leaves_vendor_null(db, org, devices, service):
    clear_oui(db)
    service.save_inventory(
        org.id, devices[0].id,
        [parsers.MacEntry(mac="00:aa:bb:cc:dd:ee", interface="Gi1/0/3", vlan=10)],
    )
    db.commit()
    assert db.execute(select(HostInventory)).scalars().one().vendor is None


def test_oui_import_is_idempotent(db):
    clear_oui(db)
    import_entries(db, [("001122", "First Name")])
    import_entries(db, [("001122", "Renamed Vendor")])

    rows = db.execute(select(OuiVendor)).scalars().all()
    assert len(rows) == 1
    assert rows[0].vendor_name == "Renamed Vendor"


def test_oui_import_handles_duplicates_within_one_batch(db):
    """ON CONFLICT cannot update the same row twice in one statement."""
    clear_oui(db)
    written = import_entries(db, [("001122", "A"), ("001122", "B"), ("334455", "C")])
    assert written == 2
    assert len(db.execute(select(OuiVendor)).scalars().all()) == 2


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------


def _probe_returning(mapping):
    """Build a fake probe that answers per device hostname"""

    def fake_probe(snapshot, device_type, transport="ssh", snmp=None,
                   capabilities=None, timeout=None):
        payload = mapping.get(snapshot.hostname)
        if payload is None:
            return ProbeResult(snapshot=snapshot, error="unreachable")
        return ProbeResult(
            snapshot=snapshot,
            neighbors=payload.get("neighbors", []),
            mac_entries=payload.get("macs", []),
            arp_entries=payload.get("arps", []),
        )

    return fake_probe


def test_crawl_walks_outward_from_the_seed(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/1", remote_hostname="dist-01",
                                 protocol="lldp", remote_interface="Gi1/0/24",
                                 remote_mgmt_ip="10.0.0.2")
            ],
            "macs": [parsers.MacEntry(mac="00:11:22:33:44:55", interface="Gi1/0/1",
                                      vlan=10)],
        },
        "dist-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/2", remote_hostname="access-01",
                                 protocol="lldp", remote_interface="Gi0/1",
                                 remote_mgmt_ip="10.0.0.3")
            ],
            "macs": [parsers.MacEntry(mac="aa:bb:cc:dd:ee:ff", interface="Gi1/0/2",
                                      vlan=20)],
        },
        "access-01": {
            "macs": [parsers.MacEntry(mac="00:22:33:44:55:66", interface="Gi0/5",
                                      vlan=30)],
        },
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=2, max_workers=2)

    # Seed, then dist-01, then access-01.
    assert summary.devices_probed == 3, summary.errors
    assert summary.neighbors_found == 2
    assert summary.hosts_found == 3
    assert summary.devices_failed == 0

    run = db.get(DiscoveryRun, summary.run_id)
    assert run.status == "success"
    assert run.devices_probed == 3
    assert run.finished_at is not None


def test_crawl_respects_the_hop_limit(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/1", remote_hostname="dist-01",
                                 protocol="lldp", remote_interface="Gi1/0/24",
                                 remote_mgmt_ip="10.0.0.2")
            ]
        },
        "dist-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/2", remote_hostname="access-01",
                                 protocol="lldp", remote_interface="Gi0/1",
                                 remote_mgmt_ip="10.0.0.3")
            ]
        },
        "access-01": {},
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=1, max_workers=2)

    assert summary.devices_probed == 2  # seed + one hop, not access-01


def test_crawl_records_unreachable_devices_without_failing(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/1", remote_hostname="dist-01",
                                 protocol="lldp", remote_interface="Gi1/0/24",
                                 remote_mgmt_ip="10.0.0.2")
            ]
        },
        # dist-01 absent from the mapping, so the probe reports it unreachable
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=2, max_workers=2)

    assert summary.devices_probed == 1
    assert summary.devices_failed == 1
    assert summary.errors[0]["device"] == "dist-01"
    assert db.get(DiscoveryRun, summary.run_id).status == "success"


def test_crawl_reports_unmanaged_neighbours(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/7", remote_hostname="rogue-sw",
                                 protocol="lldp", remote_interface="1",
                                 remote_mgmt_ip="10.9.9.9",
                                 remote_platform="Arista DCS-7050")
            ]
        }
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=1)

    assert summary.devices_created == 0
    assert any(entry["hostname"] == "rogue-sw" for entry in summary.unmanaged)


def test_crawl_auto_add_registers_discovered_devices(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/7", remote_hostname="new-arista",
                                 protocol="lldp", remote_interface="Et1",
                                 remote_mgmt_ip="10.9.9.9",
                                 remote_platform="Arista Networks EOS")
            ]
        },
        "new-arista": {},
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=1, auto_add=True)

    assert summary.devices_created == 1

    created = db.execute(
        select(Device).where(Device.hostname == "new-arista")
    ).scalars().one()
    assert created.ip_address == "10.9.9.9"
    # Platform string drives the type guess.
    assert created.device_type == "arista_eos"
    assert created.discovered is True
    assert created.discovery_source == "core-01"
    # Credentials are inherited from the seed.
    assert created.encrypted_password == devices[0].encrypted_password


def test_discovered_device_keeps_the_snmp_credential_that_answered(
    db, org, devices, service
):
    """
    The community that answered is what gets stored

    Otherwise a device found with the vault's second community would be polled
    with the seed's first one on every later crawl, and go quiet.
    """
    from app.models.credential import Credential
    from app.services import discovery_probe as probe

    answered = Credential(
        organization_id=org.id,
        name="site community",
        kind="snmp",
        priority=10,
        is_enabled=True,
        snmp_version="2c",
        encrypted_community=encryption_service.encrypt("site-secret"),
    )
    db.add(answered)
    db.commit()

    # SNMP answered, no CLI login did: inventory only, never backed up.
    assessment = probe.DeviceAssessment(
        device_type="cisco_ios",
        transport="snmp",
        backup_eligible=False,
        auth_status=probe.AUTH_FAILED,
        auth_error="ssh: authentication failed",
        probes=[
            probe.ProbeOutcome(
                transport="snmp",
                result=probe.SUCCESS,
                credential_id=answered.id,
                credential_name=answered.name,
                attempts=1,
            )
        ],
    )

    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/9", remote_hostname="snmp-only",
                                 protocol="lldp", remote_interface="1",
                                 remote_mgmt_ip="10.9.9.10")
            ]
        },
        "snmp-only": {},
    }

    with mock.patch.object(
        DiscoveryService, "probe", staticmethod(_probe_returning(topology))
    ), mock.patch.object(
        DiscoveryService, "_assess_candidate", return_value=assessment
    ):
        summary = service.crawl(org.id, devices[0].id, max_hops=1, auto_add=True)

    assert summary.devices_created == 1

    created = db.execute(
        select(Device).where(Device.hostname == "snmp-only")
    ).scalars().one()

    assert created.transport == "snmp"
    assert created.is_active is False, "no CLI login, so not on the backup list"
    assert created.snmp_version == "2c"
    assert encryption_service.decrypt(created.snmp_community) == "site-secret"


def test_crawl_auto_add_skips_neighbours_without_an_address(db, org, devices, service):
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/8", remote_hostname="no-ip-host",
                                 protocol="lldp", remote_interface="1")
            ]
        }
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=1, auto_add=True)

    assert summary.devices_created == 0


def test_crawl_rejects_a_seed_from_another_organization(db, org, devices, service):
    other = Organization(name="Other", is_active=True)
    db.add(other)
    db.commit()

    foreign = Device(
        organization_id=other.id, hostname="foreign", ip_address="192.168.99.1",
        device_type="cisco_ios", port=22, username="admin",
        encrypted_password=encryption_service.encrypt("pw"), is_active=True,
    )
    db.add(foreign)
    db.commit()

    with pytest.raises(ValueError, match="not found in this organization"):
        service.crawl(org.id, foreign.id, max_hops=1)


def test_crawl_does_not_revisit_devices(db, org, devices, service):
    """Two switches pointing at each other must not loop forever."""
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/1", remote_hostname="dist-01",
                                 protocol="lldp", remote_interface="Gi1/0/24",
                                 remote_mgmt_ip="10.0.0.2")
            ]
        },
        "dist-01": {
            "neighbors": [
                parsers.Neighbor(local_interface="Gi1/0/24", remote_hostname="core-01",
                                 protocol="lldp", remote_interface="Gi1/0/1",
                                 remote_mgmt_ip="10.0.0.1")
            ]
        },
    }

    with mock.patch.object(DiscoveryService, "probe", staticmethod(_probe_returning(topology))):
        summary = service.crawl(org.id, devices[0].id, max_hops=5, max_workers=2)

    assert summary.devices_probed == 2


# --------------------------------------------------------------------------
# Text a device sends that PostgreSQL cannot store
# --------------------------------------------------------------------------


def _octets(raw: bytes):
    """
    An SNMP OctetString holding exactly these bytes

    pyasn1's own type, not a stand-in: how it renders bytes as `str` and as
    `prettyPrint` is the thing under test.
    """
    from pyasn1.type.univ import OctetString

    return OctetString(raw)


def test_a_nul_from_a_device_does_not_fail_the_crawl(db, org, devices, service):
    """
    One NUL used to take down the whole run

    Telnet encodes a bare CR as CR NUL (RFC 854) and SNMP pads LLDP names to a
    fixed width, so a NUL arrives in the middle of an ordinary neighbour table.
    psycopg2 refuses the parameter rather than the character, the INSERT fails,
    and `crawl` records a failed run - which is how this reached a user as
    "A string literal cannot contain NUL (0x00) characters" on the Discovery
    page with nothing discovered.
    """
    topology = {
        "core-01": {
            "neighbors": [
                parsers.Neighbor(
                    local_interface="Gi1/0/1\r\x00",
                    remote_hostname="dist-01\x00\x00",
                    protocol="lldp",
                    remote_interface="Gi1/0/24\x00",
                    remote_mgmt_ip="10.0.0.2",
                    remote_platform="cisco WS-C3850\x00",
                )
            ],
            "macs": [
                parsers.MacEntry(
                    mac="00:11:22:33:44:55", interface="Gi1/0/1\x00", vlan=10
                )
            ],
        },
        "dist-01": {},
    }

    with mock.patch.object(
        DiscoveryService, "probe", staticmethod(_probe_returning(topology))
    ):
        summary = service.crawl(org.id, devices[0].id, max_hops=1, max_workers=2)

    run = db.get(DiscoveryRun, summary.run_id)
    assert run.status == "success", run.error_message
    assert summary.neighbors_found == 1

    stored = db.execute(select(Neighbor)).scalars().one()
    assert "\x00" not in stored.remote_hostname
    assert "\x00" not in stored.local_interface
    assert "\x00" not in stored.remote_interface

    host = db.execute(select(HostInventory)).scalars().one()
    assert "\x00" not in host.interface


def test_a_padded_snmp_name_matches_the_device_it_names(db, org, devices, service):
    """
    Cleaning at the transport, not at the database

    The value has to be right and not merely storable. A neighbour name is an
    upsert key and is matched against managed devices, so a name still carrying
    its SNMP padding would be compared as the padded string: the link would
    never resolve to dist-01, every run would report it as an unmanaged
    neighbour, and the next sweep would insert a twin rather than update the
    row. Cleaning it at the database - which is where the last-resort guard
    does it - would store "dist-01" and still have compared "dist-01\\x00\\x00".
    """
    from app.services.snmp_client import SnmpClient

    # An LLDP system name as an agent sends it, padded to a fixed width.
    padded = _octets(b"dist-01\x00\x00")

    neighbors = [
        parsers.Neighbor(
            local_interface="Gi1/0/1",
            remote_hostname=SnmpClient._text(padded),
            protocol="lldp",
            remote_interface="Gi1/0/24",
        )
    ]

    service.save_neighbors(org.id, devices[0].id, neighbors)
    db.commit()

    assert service.match_neighbors_to_devices(org.id) == 1
    db.commit()

    stored = db.execute(select(Neighbor)).scalars().one()
    assert stored.remote_hostname == "dist-01"
    assert stored.remote_device_id == devices[1].id

    service.save_neighbors(org.id, devices[0].id, neighbors)
    db.commit()
    assert len(db.execute(select(Neighbor)).scalars().all()) == 1


def test_a_binary_chassis_id_becomes_the_mac_it_is(db, org, devices, service):
    """
    An LLDP chassis ID of the MAC-address subtype is six raw bytes

    Discovery reads lldpRemChassisId precisely so a neighbour can be matched by
    MAC, and `_probe_snmp` falls back to the raw value when normalize_mac does
    not recognise one. Decoded as characters those bytes are mojibake with a
    NUL in them, which is both unstorable and useless; rendered as hex they are
    the MAC the object was asked for.
    """
    from app.services.snmp_client import SnmpClient

    chassis = SnmpClient._text(_octets(b"\x00\x1a\x2b\x3c\x4d\x5e"))
    assert parsers.normalize_mac(chassis) == "00:1a:2b:3c:4d:5e"

    neighbors = [
        parsers.Neighbor(
            local_interface="Gi1/0/1",
            remote_hostname="unmanaged-ap",
            protocol="lldp",
            remote_chassis_id=parsers.normalize_mac(chassis) or chassis,
        )
    ]

    service.save_neighbors(org.id, devices[0].id, neighbors)
    db.commit()

    assert (
        db.execute(select(Neighbor)).scalars().one().remote_chassis_id
        == "00:1a:2b:3c:4d:5e"
    )


# --------------------------------------------------------------------------
# Device type guessing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("cisco WS-C3850-48P", "cisco_ios"),
        ("Cisco Nexus9000 C9396PX", "cisco_nxos"),
        ("Cisco IOS-XE Software", "cisco_ios_xe"),
        ("Arista Networks EOS version 4.29", "arista_eos"),
        ("FortiGate-100F", "fortinet"),
        ("Juniper Networks EX4300, JUNOS 20.4", "juniper_junos"),
        ("Aruba JL258A 2930F", "aruba_os"),
        ("HP J9773A 2530-24G-PoEP ProCurve", "hp_procurve"),
        ("H3C Comware Platform Software", "hp_comware"),
        ("Some Unknown Vendor Box", "cisco_ios"),
        (None, "cisco_ios"),
    ],
)
def test_guess_device_type(platform, expected):
    assert guess_device_type(platform, "cisco_ios") == expected
