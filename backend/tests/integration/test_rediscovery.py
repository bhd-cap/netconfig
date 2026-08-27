"""
Re-probing devices that are already in the inventory.

What discovery decided when a device was first found goes stale: credentials
get rotated, SSH gets enabled on a switch that only spoke SNMP, a box is
swapped for a different model behind the same address. These cover what a
re-probe is allowed to change, and - just as important - what it must leave
alone.

The probe itself is mocked at the assess() boundary. Its own behaviour has unit
tests against captured device output; what needs testing here is what the
service writes.
"""
from datetime import datetime, timezone
from unittest import mock

import pytest
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models.credential import Credential, DeviceProbe
from app.models.device import Device
from app.models.network import HostInventory
from app.models.organization import Organization
from app.services import discovery_probe as probe
from app.services.rediscovery import RediscoveryService
from app.utils.encryption import encryption_service


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def org(db):
    db.execute(
        text(
            "TRUNCATE organizations, users, devices, configurations, backup_jobs, "
            "audit_logs, neighbors, host_inventory, discovery_runs, roles, "
            "app_settings, backup_targets, topology_diagrams, credentials, "
            "device_probes, oui_vendors RESTART IDENTITY CASCADE"
        )
    )
    db.commit()

    organization = Organization(name="RediscoveryTest", is_active=True)
    db.add(organization)
    db.commit()
    return organization


def make_device(db, org, **kwargs):
    device = Device(
        organization_id=org.id,
        hostname=kwargs.pop("hostname", "sw-01"),
        ip_address=kwargs.pop("ip_address", "10.30.0.1"),
        device_type=kwargs.pop("device_type", "cisco_ios"),
        username=kwargs.pop("username", "admin"),
        encrypted_password=encryption_service.encrypt(kwargs.pop("password", "secret")),
        transport=kwargs.pop("transport", "ssh"),
        **kwargs,
    )
    db.add(device)
    db.commit()
    return device


def assessment(
    *,
    transport="ssh",
    device_type=None,
    credential_id=None,
    credential_name=None,
    facts=None,
    eligible=True,
    auth_status="success",
    auth_error=None,
    snmp_ok=False,
):
    """One assess() result, built the way probe.assess would return it"""
    probes = []

    if snmp_ok:
        probes.append(
            probe.ProbeOutcome(
                transport="snmp",
                result=probe.SUCCESS,
                credential_name="community",
                attempts=1,
                message="SNMP answered",
            )
        )

    if transport:
        probes.append(
            probe.ProbeOutcome(
                transport=transport,
                result=probe.SUCCESS if auth_status == "success" else probe.AUTH_FAILED,
                credential_id=credential_id,
                credential_name=credential_name,
                attempts=1,
                message=f"{transport} outcome",
            )
        )

    return probe.DeviceAssessment(
        probes=probes,
        device_type=device_type,
        transport=transport,
        credential_id=credential_id,
        facts=facts or {},
        backup_eligible=eligible,
        auth_status=auth_status,
        auth_error=auth_error,
    )


def rediscover(db, org, device_ids=None, result=None, **kwargs):
    """Run a pass with assess() returning `result` for every device"""
    with mock.patch.object(probe, "assess", return_value=result) as assess:
        summary = RediscoveryService(db).rediscover(
            organization_id=org.id, device_ids=device_ids, **kwargs
        )
    return summary, assess


# --------------------------------------------------------------------------
# What it changes
# --------------------------------------------------------------------------


def test_a_working_login_puts_a_device_on_the_backup_schedule(db, org):
    """
    The case that matters most

    Every device a crawl found was registered inactive when nothing could log
    into it. Once the vault gains the credential that works, a re-probe is
    what turns it into a device that gets backed up.
    """
    credential = Credential(
        organization_id=org.id,
        name="Core switches",
        kind="cli",
        priority=10,
        is_enabled=True,
        username="netops",
        encrypted_password=encryption_service.encrypt("works-now"),
    )
    db.add(credential)
    db.commit()

    device = make_device(
        db, org, is_active=False, last_auth_status="auth_failed",
        auth_error="4 credentials tried, none accepted",
    )

    summary, _ = rediscover(
        db, org, [device.id],
        result=assessment(
            credential_id=credential.id, credential_name=credential.name
        ),
    )

    db.refresh(device)
    assert device.is_active is True
    assert device.last_auth_status == "success"
    assert device.auth_error is None
    # Recorded so the next run tries the one that worked first.
    assert device.credential_id == credential.id
    # And the vault's own success count moves, which is what makes its
    # ordering worth trusting.
    db.refresh(credential)
    assert credential.success_count == 1
    assert summary.authenticated == 1
    assert summary.changed == 1
    assert summary.devices[0]["changes"]["is_active"] == {"from": False, "to": True}


def test_a_device_that_stops_authenticating_comes_off_the_schedule(db, org):
    """A rotated password must not leave a device failing silently every night"""
    device = make_device(db, org, is_active=True, last_auth_status="success")

    rediscover(
        db, org, [device.id],
        result=assessment(
            auth_status="auth_failed",
            eligible=False,
            auth_error="2 credentials tried, none accepted",
        ),
    )

    db.refresh(device)
    assert device.is_active is False
    assert device.last_auth_status == "auth_failed"
    assert "none accepted" in device.auth_error


def test_the_platform_is_corrected(db, org):
    """
    The other half of the original defect

    Everything a crawl found was registered as cisco_ios, which is the wrong
    Netmiko driver and the wrong configuration command for most of an estate.
    """
    device = make_device(db, org, device_type="cisco_ios")

    summary, _ = rediscover(
        db, org, [device.id],
        result=assessment(
            device_type="arista_eos",
            facts={"identified_by": "version", "model": "DCS-7050SX-64"},
        ),
    )

    db.refresh(device)
    assert device.device_type == "arista_eos"
    assert device.model == "DCS-7050SX-64"
    assert summary.devices[0]["changes"]["device_type"] == {
        "from": "cisco_ios",
        "to": "arista_eos",
    }
    assert summary.devices[0]["identified_by"] == "version"


def test_a_device_that_only_answers_snmp_is_moved_to_snmp(db, org):
    """
    Rather than left pointed at a port that refused

    An SNMP device is inventoried and crawled and never backed up, which is a
    truthful state; a device marked ssh that has no ssh is not.
    """
    device = make_device(db, org, transport="ssh")

    rediscover(
        db, org, [device.id],
        result=assessment(
            transport=None,
            snmp_ok=True,
            eligible=False,
            auth_status="auth_failed",
            auth_error="SSH refused, telnet timed out",
        ),
    )

    db.refresh(device)
    assert device.transport == "snmp"
    assert device.is_active is False


def test_telnet_is_recorded_when_ssh_did_not_answer(db, org):
    device = make_device(db, org, transport="ssh")

    rediscover(
        db, org, [device.id],
        result=assessment(transport="telnet", credential_name="Legacy login"),
    )

    db.refresh(device)
    assert device.transport == "telnet"
    assert device.last_auth_status == "success"


def test_the_facts_a_device_reports_are_stored(db, org):
    device = make_device(db, org)

    rediscover(
        db, org, [device.id],
        result=assessment(
            facts={
                "model": "C9300-48P",
                "serial_number": "FOC1234X0YZ",
                "os_version": "17.06.04",
                "sysname": "access-01.example.net",
                "sysdescr": "Cisco IOS Software, C9300 Software",
                "uptime_seconds": 981234,
                "collection_probes": ["show running-config: 40213 bytes"],
            }
        ),
    )

    db.refresh(device)
    assert device.model == "C9300-48P"
    assert device.serial_number == "FOC1234X0YZ"
    assert device.os_version == "17.06.04"
    assert device.snmp_sysname == "access-01.example.net"
    assert device.snmp_uptime_seconds == 981234
    # Anything without a column of its own is kept rather than dropped.
    assert device.discovered_facts["collection_probes"]


def test_each_transport_gets_one_probe_row(db, org):
    """
    Upserted, not appended

    "SSH refused, telnet timed out, 4 credentials tried" is what the detail
    view needs; an ever-growing log is not.
    """
    device = make_device(db, org)

    for _ in range(3):
        rediscover(
            db, org, [device.id],
            result=assessment(snmp_ok=True, credential_name="Core switches"),
        )

    rows = list(
        db.scalars(select(DeviceProbe).where(DeviceProbe.device_id == device.id))
    )
    assert {row.transport for row in rows} == {"snmp", "ssh"}
    assert len(rows) == 2


# --------------------------------------------------------------------------
# What it must not change
# --------------------------------------------------------------------------


def test_what_a_person_entered_is_left_alone(db, org):
    """A rediscovery that renamed devices or moved them would be unusable"""
    device = make_device(
        db, org,
        hostname="core-01",
        location="NYC-DC1 rack 4",
        description="Primary core switch",
        tags={"role": "core", "owner": "netops"},
    )

    rediscover(
        db, org, [device.id],
        result=assessment(
            device_type="arista_eos",
            facts={"sysname": "something-else-entirely"},
        ),
    )

    db.refresh(device)
    assert device.hostname == "core-01"
    assert device.location == "NYC-DC1 rack 4"
    assert device.description == "Primary core switch"
    assert device.tags == {"role": "core", "owner": "netops"}


def test_an_unchanged_device_reports_no_changes(db, org):
    """So a pass over a stable estate is quiet rather than noise"""
    device = make_device(
        db, org, device_type="cisco_ios", transport="ssh", is_active=True,
        last_auth_status="success",
    )

    summary, _ = rediscover(
        db, org, [device.id],
        result=assessment(device_type="cisco_ios", transport="ssh"),
    )

    db.refresh(device)
    assert summary.changed == 0
    assert summary.devices[0]["changes"] == {}


def test_one_device_blowing_up_does_not_abandon_the_rest(db, org):
    """A pass over an estate must survive a single bad device"""
    first = make_device(db, org, hostname="sw-a", ip_address="10.30.0.1")
    second = make_device(db, org, hostname="sw-b", ip_address="10.30.0.2")

    def explode_on_first(host, **kwargs):
        if host == "10.30.0.1":
            raise RuntimeError("paramiko blew up")
        return assessment(credential_name="Core switches")

    with mock.patch.object(probe, "assess", side_effect=explode_on_first):
        summary = RediscoveryService(db).rediscover(organization_id=org.id)

    assert summary.probed == 2
    assert summary.failed == 1
    assert summary.authenticated == 1

    db.refresh(second)
    assert second.last_auth_status == "success"

    failed = next(d for d in summary.devices if d["hostname"] == "sw-a")
    assert "paramiko blew up" in failed["error"]


# --------------------------------------------------------------------------
# Which devices, and with which credentials
# --------------------------------------------------------------------------


def test_inactive_devices_are_probed_by_default(db, org):
    """They are the whole point: a device nothing could log into last time"""
    make_device(db, org, hostname="sw-off", ip_address="10.30.0.9", is_active=False)

    summary, _ = rediscover(db, org, result=assessment())

    assert summary.probed == 1


def test_inactive_devices_can_be_skipped(db, org):
    make_device(db, org, hostname="sw-off", ip_address="10.30.0.9", is_active=False)
    make_device(db, org, hostname="sw-on", ip_address="10.30.0.10", is_active=True)

    summary, _ = rediscover(db, org, result=assessment(), include_inactive=False)

    assert summary.probed == 1
    assert summary.devices[0]["hostname"] == "sw-on"


def test_the_vault_is_tried_before_the_devices_own_credentials(db, org):
    """
    Order matters: a rotated vault entry must win over a stale stored one

    Otherwise a device keeps authenticating with the old password until it
    stops working, and the vault is not the single place to change it.
    """
    db.add(
        Credential(
            organization_id=org.id,
            name="Rotated vault entry",
            kind="cli",
            priority=10,
            is_enabled=True,
            username="netops",
            encrypted_password=encryption_service.encrypt("new-password"),
        )
    )
    db.commit()

    device = make_device(db, org, username="old-admin", password="old-password")

    _, assess = rediscover(db, org, [device.id], result=assessment())

    attempts = assess.call_args.kwargs["cli_attempts"]
    names = [attempt.name for attempt in attempts]

    assert names[0] == "Rotated vault entry"
    assert names[-1] == f"stored on {device.hostname}"


def test_a_vault_only_device_offers_no_stored_credential(db, org):
    """It holds none: that is what makes the vault the one place to rotate it"""
    device = make_device(db, org)
    device.username = None
    device.encrypted_password = None
    db.commit()

    _, assess = rediscover(db, org, [device.id], result=assessment())

    assert assess.call_args.kwargs["cli_attempts"] == []


def test_the_mac_vendor_is_offered_as_a_platform_hint(db, org):
    """
    A device cabled to another switch shows up in that switch's MAC table

    The OUI gives a vendor name, and a vendor name is what identify_platform
    already reads. Weak on its own - an HP badge could be ProCurve or Comware -
    so it orders the collection probes rather than deciding anything.
    """
    switch = make_device(db, org, hostname="sw-a", ip_address="10.30.0.1")
    target = make_device(db, org, hostname="sw-b", ip_address="10.30.0.2")

    db.add(
        HostInventory(
            organization_id=org.id,
            device_id=switch.id,
            interface="Gi1/0/24",
            mac_address="00:1c:58:aa:bb:cc",
            ip_address="10.30.0.2",
            vendor="Arista Networks",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()

    _, assess = rediscover(db, org, [target.id], result=assessment())

    assert assess.call_args.kwargs["platform_hint"] == "arista_eos"


def test_no_inventory_entry_means_no_hint_rather_than_a_guess(db, org):
    device = make_device(db, org)

    _, assess = rediscover(db, org, [device.id], result=assessment())

    assert assess.call_args.kwargs["platform_hint"] is None
