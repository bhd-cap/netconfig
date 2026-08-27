"""
API-level tests for the device transport and SNMP fields.

The connector already has its own transport tests; these cover the seam the
UI actually uses - that a device can be created and edited as telnet or SNMP,
that the secrets are encrypted and never returned, and that the backup and
connectivity paths use the transport the device is configured for.
"""
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Device, Organization
from app.models.administration import Role
from app.services import user_admin
from app.services.config_retriever import ConfigurationRetriever, DeviceSnapshot
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
            "app_settings, backup_targets, topology_diagrams RESTART IDENTITY CASCADE"
        )
    )
    db.commit()

    organization = Organization(name="ApiDeviceTest", is_active=True)
    db.add(organization)
    db.commit()
    user_admin.seed_system_roles(db, organization.id)
    return organization


@pytest.fixture
def admin(db, org):
    role = db.execute(
        select(Role).where(
            Role.organization_id == org.id, Role.name == "Administrator"
        )
    ).scalar_one()

    return user_admin.create_user(
        db, org.id, "devadmin", "devadmin@example.com",
        password="devpass1234", role_id=role.id,
    )["user"]


@pytest.fixture
def client(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def as_user(client, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return client


def test_a_device_defaults_to_ssh(client, admin):
    body = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-default",
            "ip_address": "10.1.0.1",
            "device_type": "cisco_ios",
            "username": "admin",
            "password": "secret",
        },
    ).json()

    assert body["transport"] == "ssh"
    assert body["port"] == 22
    assert body["snmp_port"] == 161
    assert body["snmp_version"] is None


def test_a_telnet_device_can_be_created(client, db, admin):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-telnet",
            "ip_address": "10.1.0.2",
            "device_type": "cisco_ios",
            "username": "admin",
            "password": "secret",
            "transport": "telnet",
            "port": 23,
        },
    )

    assert response.status_code == 201
    assert response.json()["transport"] == "telnet"
    assert response.json()["port"] == 23

    stored = db.execute(
        select(Device).where(Device.hostname == "sw-telnet")
    ).scalar_one()
    assert stored.transport == "telnet"


def test_snmp_secrets_are_encrypted_and_never_returned(client, db, admin):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-snmp",
            "ip_address": "10.1.0.3",
            "device_type": "cisco_ios",
            "username": "admin",
            "password": "secret",
            "transport": "snmp",
            "snmp_version": "2c",
            "snmp_community": "public-but-secret",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["transport"] == "snmp"
    assert body["snmp_version"] == "2c"
    assert "snmp_community" not in body

    stored = db.execute(select(Device).where(Device.hostname == "sw-snmp")).scalar_one()
    assert stored.snmp_community != "public-but-secret"
    assert encryption_service.decrypt(stored.snmp_community) == "public-but-secret"


def test_snmp_v3_credentials_round_trip(client, db, admin):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-v3",
            "ip_address": "10.1.0.4",
            "device_type": "arista_eos",
            "username": "admin",
            "password": "secret",
            "transport": "snmp",
            "snmp_version": "3",
            "snmp_v3_user": "monitor",
            "snmp_v3_auth_key": "authkey123",
            "snmp_v3_priv_key": "privkey123",
            "snmp_v3_auth_protocol": "SHA",
            "snmp_v3_priv_protocol": "AES",
        },
    )

    assert response.status_code == 201
    assert response.json()["snmp_v3_user"] == "monitor"
    assert response.json()["snmp_v3_auth_protocol"] == "SHA"
    assert "snmp_v3_auth_key" not in response.json()

    stored = db.execute(select(Device).where(Device.hostname == "sw-v3")).scalar_one()
    assert encryption_service.decrypt(stored.snmp_v3_auth_key) == "authkey123"
    assert encryption_service.decrypt(stored.snmp_v3_priv_key) == "privkey123"


def test_an_unknown_transport_is_rejected(client, admin):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-bad",
            "ip_address": "10.1.0.5",
            "device_type": "cisco_ios",
            "username": "admin",
            "password": "secret",
            "transport": "carrier-pigeon",
        },
    )

    assert response.status_code == 422


def test_updating_transport_keeps_the_snmp_secret(client, db, admin):
    session = as_user(client, admin)

    device_id = session.post(
        "/api/v1/devices",
        json={
            "hostname": "sw-keep",
            "ip_address": "10.1.0.6",
            "device_type": "cisco_ios",
            "username": "admin",
            "password": "secret",
            "transport": "snmp",
            "snmp_version": "2c",
            "snmp_community": "original-community",
        },
    ).json()["id"]

    updated = session.put(
        f"/api/v1/devices/{device_id}", json={"transport": "ssh", "port": 22}
    )

    assert updated.status_code == 200
    assert updated.json()["transport"] == "ssh"

    stored = db.execute(select(Device).where(Device.id == device_id)).scalar_one()
    assert encryption_service.decrypt(stored.snmp_community) == "original-community"

    # And supplying a new one replaces it.
    session.put(
        f"/api/v1/devices/{device_id}", json={"snmp_community": "rotated"}
    )
    db.refresh(stored)
    assert encryption_service.decrypt(stored.snmp_community) == "rotated"


def test_backing_up_an_snmp_device_says_why_it_cannot(db, org):
    """
    SNMP has no OID that returns a running configuration

    Failing with that sentence beats a confusing SSH authentication error on
    a device that has no SSH credentials at all.
    """
    device = Device(
        organization_id=org.id,
        hostname="snmp-only",
        ip_address="10.1.0.7",
        device_type="cisco_ios",
        username="",
        encrypted_password="",
        transport="snmp",
        snmp_version="2c",
        snmp_community=encryption_service.encrypt("public"),
    )
    db.add(device)
    db.commit()

    result = ConfigurationRetriever(db).backup_device(device.id)

    assert result["success"] is False
    assert "SNMP" in result["message"]
    assert "cannot retrieve a configuration" in result["message"]


def test_a_backup_uses_the_configured_transport(db, org):
    device = Device(
        organization_id=org.id,
        hostname="telnet-sw",
        ip_address="10.1.0.8",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        port=23,
        transport="telnet",
    )
    db.add(device)
    db.commit()

    snapshot = DeviceSnapshot.from_device(device)
    assert snapshot.transport == "telnet"

    with mock.patch(
        "app.services.config_retriever.DeviceConnector"
    ) as connector_class:
        instance = connector_class.return_value
        instance.__enter__ = mock.Mock(return_value=instance)
        instance.__exit__ = mock.Mock(return_value=False)
        instance.get_running_config.return_value = "hostname telnet-sw\n"

        ConfigurationRetriever._retrieve_config(snapshot)

    assert connector_class.call_args.kwargs["transport"] == "telnet"
    assert connector_class.call_args.kwargs["port"] == 23


def test_connectivity_test_uses_the_configured_transport(client, db, org, admin):
    device = Device(
        organization_id=org.id,
        hostname="snmp-test",
        ip_address="10.1.0.9",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        transport="snmp",
        snmp_version="2c",
        snmp_community=encryption_service.encrypt("public"),
    )
    db.add(device)
    db.commit()

    with mock.patch("app.api.v1.devices.DeviceConnector") as connector_class:
        connector_class.return_value.test_connection.return_value = {
            "success": True,
            "message": "ok",
            "response_time": 0.1,
            "device_info": {},
        }

        response = as_user(client, admin).post(f"/api/v1/devices/{device.id}/test")

    assert response.status_code == 200
    kwargs = connector_class.call_args.kwargs
    assert kwargs["transport"] == "snmp"
    assert kwargs["snmp"]["version"] == "2c"
    # The secret is handed over still encrypted; the connector decrypts it.
    assert kwargs["snmp"]["community"] != "public"


# --------------------------------------------------------------------------
# Sorting, bulk edit, bulk delete and the detail view
# --------------------------------------------------------------------------


@pytest.fixture
def fleet(db, org):
    """Three devices with values worth sorting on"""
    rows = [
        ("alpha", "10.2.0.30", "cisco_ios", "ssh", "NYC", "success"),
        ("bravo", "10.2.0.10", "arista_eos", "telnet", "LON", "auth_failed"),
        ("charlie", "10.2.0.20", "hp_procurve", "snmp", None, "never"),
    ]

    made = []
    for hostname, ip, device_type, transport, location, auth in rows:
        device = Device(
            organization_id=org.id,
            hostname=hostname,
            ip_address=ip,
            device_type=device_type,
            username="admin",
            encrypted_password=encryption_service.encrypt("secret"),
            transport=transport,
            location=location,
            last_auth_status=auth,
            is_active=True,
        )
        db.add(device)
        made.append(device)

    db.commit()
    return made


def _hostnames(client, user, query=""):
    body = as_user(client, user).get(f"/api/v1/devices?limit=50{query}").json()
    return [item["hostname"] for item in body["items"]]


def test_devices_sort_on_a_catalogued_column(client, admin, fleet):
    assert _hostnames(client, admin, "&sort_by=ip_address&sort_dir=asc") == [
        "bravo",
        "charlie",
        "alpha",
    ]
    assert _hostnames(client, admin, "&sort_by=hostname&sort_dir=desc") == [
        "charlie",
        "bravo",
        "alpha",
    ]


def test_sorting_puts_nulls_last_either_way(client, admin, fleet):
    """charlie has no location, so it is noise at either end"""
    for direction in ("asc", "desc"):
        assert (
            _hostnames(client, admin, f"&sort_by=location&sort_dir={direction}")[-1]
            == "charlie"
        )


def test_an_unsortable_column_is_refused(client, admin, fleet):
    """A column name from a query string must never reach the SQL"""
    response = as_user(client, admin).get(
        "/api/v1/devices?sort_by=encrypted_password"
    )

    assert response.status_code == 400
    assert "encrypted_password" in response.json()["detail"]


def test_sortable_columns_are_listed(client, admin):
    columns = as_user(client, admin).get("/api/v1/devices/sortable").json()["columns"]

    assert "hostname" in columns
    assert "last_auth_status" in columns
    assert "encrypted_password" not in columns


def test_bulk_update_writes_only_the_fields_supplied(client, db, admin, fleet):
    response = as_user(client, admin).patch(
        "/api/v1/devices/bulk",
        json={
            "device_ids": [fleet[0].id, fleet[1].id],
            "is_active": False,
            "location": "Lab rack 4",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 2
    assert body["fields"] == ["is_active", "location"]

    db.expire_all()
    assert fleet[0].is_active is False
    assert fleet[0].location == "Lab rack 4"
    # Untouched fields keep their values, and the third device is untouched.
    assert fleet[0].device_type == "cisco_ios"
    assert fleet[2].is_active is True
    assert fleet[2].location is None


def test_bulk_update_needs_something_to_change(client, admin, fleet):
    response = as_user(client, admin).patch(
        "/api/v1/devices/bulk", json={"device_ids": [fleet[0].id]}
    )

    assert response.status_code == 400


def test_bulk_update_ignores_devices_from_another_organization(client, db, admin, fleet):
    other = Organization(name="Somewhere else", is_active=True)
    db.add(other)
    db.commit()

    foreign = Device(
        organization_id=other.id,
        hostname="not-ours",
        ip_address="10.3.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        is_active=True,
    )
    db.add(foreign)
    db.commit()

    body = as_user(client, admin).patch(
        "/api/v1/devices/bulk",
        json={"device_ids": [fleet[0].id, foreign.id], "is_active": False},
    ).json()

    assert body["updated"] == 1
    assert body["not_found"] == [foreign.id]

    db.expire_all()
    assert foreign.is_active is True


def test_deleting_devices_keeps_the_inventory_and_adjacencies(
    client, db, admin, fleet
):
    """
    What was plugged into a port is history, not a property of the switch

    Deleting the switch is not a statement about the laptop that was on port
    12 last week, so the row survives with its device_id nulled and the
    hostname it was seen on intact.
    """
    from app.models.network import HostInventory, Neighbor

    switch = fleet[0]

    db.add(
        HostInventory(
            organization_id=switch.organization_id,
            device_id=switch.id,
            device_hostname=switch.hostname,
            interface="12",
            mac_address="00:1b:2c:00:00:01",
            vlan=10,
        )
    )
    db.add(
        Neighbor(
            organization_id=switch.organization_id,
            device_id=switch.id,
            device_hostname=switch.hostname,
            local_interface="Gi1/0/1",
            remote_hostname="dist-01",
            remote_interface="Et49",
            protocol="lldp",
        )
    )
    db.commit()

    response = as_user(client, admin).post(
        "/api/v1/devices/bulk-delete", json={"device_ids": [switch.id]}
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 1
    assert db.get(Device, switch.id) is None

    host = db.execute(select(HostInventory)).scalars().one()
    assert host.device_id is None, "the row must survive the switch"
    assert host.device_hostname == "alpha"
    assert host.interface == "12"

    adjacency = db.execute(select(Neighbor)).scalars().one()
    assert adjacency.device_id is None
    assert adjacency.device_hostname == "alpha"


def test_deleting_one_device_keeps_its_inventory_too(client, db, admin, fleet):
    """The single delete goes through the same foreign key"""
    from app.models.network import HostInventory

    switch = fleet[1]
    db.add(
        HostInventory(
            organization_id=switch.organization_id,
            device_id=switch.id,
            device_hostname=switch.hostname,
            interface="3",
            mac_address="00:1b:2c:00:00:02",
            vlan=1,
        )
    )
    db.commit()

    assert (
        as_user(client, admin).delete(f"/api/v1/devices/{switch.id}").status_code
        == 200
    )

    host = db.execute(select(HostInventory)).scalars().one()
    assert host.device_id is None
    assert host.device_hostname == "bravo"


def test_device_detail_reports_every_transport_tried(client, db, admin, fleet):
    from app.models.credential import Credential, DeviceProbe

    device = fleet[2]
    device.last_auth_status = "auth_failed"
    device.auth_error = "ssh: authentication failed; telnet: nothing listening"
    device.model = "J9773A"
    device.snmp_sysname = "charlie"

    community = Credential(
        organization_id=device.organization_id,
        name="Read-only community",
        kind="snmp",
        priority=10,
        snmp_version="2c",
        encrypted_community=encryption_service.encrypt("public"),
    )
    db.add(community)
    db.commit()

    db.add_all(
        [
            DeviceProbe(
                organization_id=device.organization_id,
                device_id=device.id,
                transport="ssh",
                result="auth_failed",
                attempts=2,
                message="Authentication failed for both CLI credentials",
                duration=4210,
            ),
            DeviceProbe(
                organization_id=device.organization_id,
                device_id=device.id,
                transport="snmp",
                result="success",
                credential_id=community.id,
                credential_name=community.name,
                attempts=1,
                message="sysDescr read",
                duration=140,
            ),
        ]
    )
    db.commit()

    body = as_user(client, admin).get(f"/api/v1/devices/{device.id}/detail").json()

    assert body["device"]["hostname"] == "charlie"
    assert body["authentication"]["status"] == "auth_failed"
    assert body["authentication"]["backup_eligible"] is False
    assert body["facts"]["model"] == "J9773A"
    assert body["facts"]["snmp_sysname"] == "charlie"

    by_transport = {row["transport"]: row for row in body["probes"]}
    assert by_transport["ssh"]["attempts"] == 2
    assert by_transport["snmp"]["credential_name"] == "Read-only community"
    assert by_transport["snmp"]["duration_ms"] == 140

    # No secret leaks through the detail view.
    assert "public" not in json.dumps(body)


def test_device_detail_is_scoped_to_the_organization(client, db, admin, fleet):
    other = Organization(name="Elsewhere", is_active=True)
    db.add(other)
    db.commit()

    foreign = Device(
        organization_id=other.id,
        hostname="theirs",
        ip_address="10.4.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(foreign)
    db.commit()

    assert (
        as_user(client, admin).get(f"/api/v1/devices/{foreign.id}/detail").status_code
        == 404
    )
