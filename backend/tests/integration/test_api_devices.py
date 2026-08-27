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
from app.services.credentials import resolve_for_device
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

    snapshot = DeviceSnapshot.from_device(device, resolve_for_device(db, device))
    assert snapshot.transport == "telnet"
    # No vault credential, so the device's own login is what is used.
    assert snapshot.credential_source == "device"

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


@pytest.fixture
def uniform_fleet(db, org):
    """
    A fleet with nothing to tell its devices apart

    This is what a discovery crawl actually produces before anything has been
    logged into: one device type, one transport, never authenticated, never
    backed up. Sorting on any of those columns ties on every row.
    """
    for hostname in ("sw-a", "sw-b", "sw-c", "sw-d"):
        db.add(
            Device(
                organization_id=org.id,
                hostname=hostname,
                ip_address=f"10.9.0.{ord(hostname[-1])}",
                device_type="cisco_ios",
                username="admin",
                encrypted_password=encryption_service.encrypt("secret"),
                transport="ssh",
                last_auth_status="never",
                is_active=True,
            )
        )
    db.commit()


@pytest.mark.parametrize(
    "column",
    ["device_type", "transport", "last_auth_status", "is_active", "last_backup_at"],
)
def test_reversing_a_tied_column_still_reverses_the_page(
    client, admin, uniform_fleet, column
):
    """
    Flipping the direction has to change something the user can see

    With a fixed ascending tiebreak, a column where every row holds the same
    value came back in an identical order both ways - so clicking the header
    did nothing visible, which is indistinguishable from sorting being broken.
    Reported from a real install, where every discovered device was cisco_ios
    over ssh and none had been authenticated.
    """
    ascending = _hostnames(client, admin, f"&sort_by={column}&sort_dir=asc")
    descending = _hostnames(client, admin, f"&sort_by={column}&sort_dir=desc")

    assert ascending == ["sw-a", "sw-b", "sw-c", "sw-d"]
    assert descending == ascending[::-1]


def test_a_tied_column_still_pages_without_repeating(client, admin, uniform_fleet):
    """
    The tiebreak's original job, which the reversal must not break

    Every row ties on device_type, so without a tiebreak at all the database
    is free to return them in any order and paging would repeat and skip rows.
    """
    for direction in ("asc", "desc"):
        seen = []
        for skip in (0, 2):
            body = (
                as_user(client, admin)
                .get(
                    f"/api/v1/devices?limit=2&skip={skip}"
                    f"&sort_by=device_type&sort_dir={direction}"
                )
                .json()
            )
            seen.extend(item["hostname"] for item in body["items"])

        assert len(set(seen)) == 4, f"{direction}: paging repeated a row: {seen}"


# --------------------------------------------------------------------------
# Vault credentials on a device
#
# A device either holds its own login or points at a vault entry. The point of
# these is that choosing a vault entry changes how the device is actually
# reached - a choice that only showed up in the form would be worse than no
# choice at all.
# --------------------------------------------------------------------------


@pytest.fixture
def cli_credential(db, org):
    from app.models.credential import Credential

    credential = Credential(
        organization_id=org.id,
        name="Core switches",
        kind="cli",
        priority=10,
        is_enabled=True,
        username="netops",
        encrypted_password=encryption_service.encrypt("vault-secret"),
        encrypted_enable_secret=encryption_service.encrypt("vault-enable"),
    )
    db.add(credential)
    db.commit()
    return credential


@pytest.fixture
def snmp_credential(db, org):
    from app.models.credential import Credential

    credential = Credential(
        organization_id=org.id,
        name="Read-only community",
        kind="snmp",
        priority=10,
        is_enabled=True,
        snmp_version="2c",
        encrypted_community=encryption_service.encrypt("s3cret-community"),
    )
    db.add(credential)
    db.commit()
    return credential


def test_a_device_can_be_created_with_a_vault_credential(
    client, admin, org, cli_credential
):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-vault",
            "ip_address": "10.7.0.1",
            "device_type": "cisco_ios",
            "credential_id": cli_credential.id,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["credential_id"] == cli_credential.id
    assert body["credential_name"] == "Core switches"
    # No login of its own: the vault entry is the single place to rotate it.
    assert body["username"] is None


def test_a_device_still_needs_credentials_from_somewhere(client, admin):
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-nothing",
            "ip_address": "10.7.0.2",
            "device_type": "cisco_ios",
        },
    )

    assert response.status_code == 422
    assert "vault credential" in response.text


def test_a_vault_credential_of_the_wrong_kind_is_refused(
    client, admin, snmp_credential
):
    """An SNMP community is not a login, and trying it as one looks like a typo"""
    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-wrong-kind",
            "ip_address": "10.7.0.3",
            "device_type": "cisco_ios",
            "credential_id": snmp_credential.id,
        },
    )

    assert response.status_code == 400
    assert "snmp credential" in response.json()["detail"]


def test_another_organizations_credential_is_refused(client, admin, db):
    from app.models.credential import Credential

    other_org = Organization(name="Somebody else")
    db.add(other_org)
    db.commit()
    theirs = Credential(
        organization_id=other_org.id,
        name="Not yours",
        kind="cli",
        priority=10,
        username="root",
        encrypted_password=encryption_service.encrypt("x"),
    )
    db.add(theirs)
    db.commit()

    response = as_user(client, admin).post(
        "/api/v1/devices",
        json={
            "hostname": "sw-cross-tenant",
            "ip_address": "10.7.0.4",
            "device_type": "cisco_ios",
            "credential_id": theirs.id,
        },
    )

    assert response.status_code == 400
    assert "No vault credential" in response.json()["detail"]


def test_switching_a_device_to_the_vault_drops_its_own_login(
    client, admin, db, org, cli_credential
):
    """
    Otherwise a stale username and password sit behind the vault reference,
    and the device keeps working after the vault entry is deleted using
    credentials nobody remembers setting.
    """
    device = Device(
        organization_id=org.id,
        hostname="sw-switching",
        ip_address="10.7.0.5",
        device_type="cisco_ios",
        username="local-admin",
        encrypted_password=encryption_service.encrypt("local-secret"),
    )
    db.add(device)
    db.commit()

    response = as_user(client, admin).put(
        f"/api/v1/devices/{device.id}",
        json={"credential_id": cli_credential.id},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.get(Device, device.id)
    assert stored.credential_id == cli_credential.id
    assert stored.username is None
    assert stored.encrypted_password is None


def test_typing_a_password_takes_a_device_back_off_the_vault(
    client, admin, db, org, cli_credential
):
    device = Device(
        organization_id=org.id,
        hostname="sw-back-to-manual",
        ip_address="10.7.0.6",
        device_type="cisco_ios",
        credential_id=cli_credential.id,
    )
    db.add(device)
    db.commit()

    response = as_user(client, admin).put(
        f"/api/v1/devices/{device.id}",
        json={"username": "local-admin", "password": "typed-by-hand"},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.get(Device, device.id)
    assert stored.credential_id is None
    assert stored.username == "local-admin"
    assert encryption_service.decrypt(stored.encrypted_password) == "typed-by-hand"


def test_a_backup_logs_in_with_the_vault_credential(db, org, cli_credential):
    """
    The whole point: the resolved login is what reaches the device

    A device that names a vault credential holds no username or password of
    its own, so if resolution were skipped the connector would be handed
    nothing and the backup would fail with an authentication error.
    """
    device = Device(
        organization_id=org.id,
        hostname="sw-resolved",
        ip_address="10.7.0.7",
        device_type="cisco_ios",
        credential_id=cli_credential.id,
    )
    db.add(device)
    db.commit()

    snapshot = DeviceSnapshot.from_device(device, resolve_for_device(db, device))

    assert snapshot.username == "netops"
    assert encryption_service.decrypt(snapshot.encrypted_password) == "vault-secret"
    assert encryption_service.decrypt(snapshot.enable_secret) == "vault-enable"
    assert snapshot.credential_source == "Core switches"


def test_an_snmp_credential_supplies_the_community(db, org, snmp_credential):
    device = Device(
        organization_id=org.id,
        hostname="sw-snmp-vault",
        ip_address="10.7.0.8",
        device_type="cisco_ios",
        transport="snmp",
        snmp_port=1161,
        snmp_credential_id=snmp_credential.id,
    )
    db.add(device)
    db.commit()

    login = resolve_for_device(db, device)

    assert login.snmp_source == "Read-only community"
    assert login.snmp["version"] == "2c"
    # Still encrypted, the same as the device's own columns, so the connector
    # needs no telling where it came from.
    assert encryption_service.decrypt(login.snmp["community"]) == "s3cret-community"
    # The port is the device's: which community to use and which port answers
    # are separate facts.
    assert login.snmp["port"] == 1161


def test_resolution_ignores_another_organizations_credential(db, org):
    """
    Tenant scoping at the point of use, not just at the point of entry

    The API refuses to store a cross-tenant reference, so this is the second
    line: were one to exist - a restored backup, a hand-edited row - it must
    not be used to log into anything.
    """
    from app.models.credential import Credential

    other_org = Organization(name="Somebody else entirely")
    db.add(other_org)
    db.commit()

    theirs = Credential(
        organization_id=other_org.id,
        name="Their credential",
        kind="cli",
        priority=10,
        username="their-admin",
        encrypted_password=encryption_service.encrypt("their-secret"),
    )
    db.add(theirs)
    db.commit()

    device = Device(
        organization_id=org.id,
        hostname="sw-cross-ref",
        ip_address="10.7.0.9",
        device_type="cisco_ios",
        username="fallback",
        encrypted_password=encryption_service.encrypt("fallback-secret"),
        credential_id=theirs.id,
    )
    db.add(device)
    db.commit()

    login = resolve_for_device(db, device)

    assert login.username == "fallback"
    assert login.cli_source == "device"


def test_a_credential_of_the_wrong_kind_is_ignored_at_resolution(db, org):
    """An SNMP community used as a login would look like a wrong password"""
    from app.models.credential import Credential

    community = Credential(
        organization_id=org.id,
        name="Community, not a login",
        kind="snmp",
        priority=10,
        snmp_version="2c",
        encrypted_community=encryption_service.encrypt("public"),
    )
    db.add(community)
    db.commit()

    device = Device(
        organization_id=org.id,
        hostname="sw-wrong-kind-stored",
        ip_address="10.7.0.11",
        device_type="cisco_ios",
        username="fallback",
        encrypted_password=encryption_service.encrypt("fallback-secret"),
        credential_id=community.id,
    )
    db.add(device)
    db.commit()

    login = resolve_for_device(db, device)

    assert login.username == "fallback"
    assert login.cli_source == "device"


def test_deleting_a_credential_a_device_uses_is_refused(
    client, admin, db, org, cli_credential
):
    device = Device(
        organization_id=org.id,
        hostname="sw-depends",
        ip_address="10.7.0.10",
        device_type="cisco_ios",
        credential_id=cli_credential.id,
    )
    db.add(device)
    db.commit()

    session = as_user(client, admin)

    refused = session.delete(f"/api/v1/credentials/{cli_credential.id}")
    assert refused.status_code == 409
    assert "sw-depends" in refused.json()["detail"]

    forced = session.delete(f"/api/v1/credentials/{cli_credential.id}?force=true")
    assert forced.status_code in (200, 204), forced.text


# --------------------------------------------------------------------------
# Rediscovery endpoints
# --------------------------------------------------------------------------


def test_rediscovering_one_device_returns_what_changed(client, admin, db, org):
    """
    Inline, because the caller wants the answer and not a task id

    One device is one round of credentials; a whole estate goes through the
    bulk endpoint, which queues.
    """
    device = Device(
        organization_id=org.id,
        hostname="sw-reprobe",
        ip_address="10.8.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        is_active=False,
        last_auth_status="auth_failed",
    )
    db.add(device)
    db.commit()

    from app.services import discovery_probe as probe

    result = probe.DeviceAssessment(
        probes=[
            probe.ProbeOutcome(
                transport="telnet",
                result=probe.SUCCESS,
                credential_name="Legacy login",
                attempts=2,
                message="Logged in over telnet",
            )
        ],
        device_type="hp_procurve",
        transport="telnet",
        facts={"identified_by": "collection", "model": "J9773A"},
        backup_eligible=True,
        auth_status="success",
    )

    with mock.patch.object(probe, "assess", return_value=result):
        response = as_user(client, admin).post(
            f"/api/v1/devices/{device.id}/rediscover"
        )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["authenticated"] is True
    assert body["transport"] == "telnet"
    assert body["device_type"] == "hp_procurve"
    assert body["identified_by"] == "collection"
    assert body["changes"]["is_active"] == {"from": False, "to": True}
    # The message says how the platform was worked out, since that is the part
    # an operator would otherwise have to guess at.
    assert "trying each vendor's command" in body["message"]


def test_rediscovering_a_missing_device_is_a_404(client, admin):
    response = as_user(client, admin).post("/api/v1/devices/999999/rediscover")

    assert response.status_code == 404


def test_bulk_rediscovery_is_queued(client, admin, db, org):
    device = Device(
        organization_id=org.id,
        hostname="sw-bulk-reprobe",
        ip_address="10.8.0.2",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(device)
    db.commit()

    with mock.patch(
        "app.tasks.discovery.rediscover_devices_task.delay"
    ) as delay:
        delay.return_value = mock.Mock(id="task-redisc-1")

        response = as_user(client, admin).post(
            "/api/v1/devices/rediscover",
            json={"device_ids": [device.id]},
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] is True
    assert body["task_id"] == "task-redisc-1"
    assert body["devices"] == 1

    assert delay.call_args.kwargs["device_ids"] == [device.id]
    assert delay.call_args.kwargs["include_inactive"] is True


def test_bulk_rediscovery_refuses_a_device_from_another_organization(
    client, admin, db
):
    """Tenant scoping, checked before anything is queued"""
    other = Organization(name="Not mine at all")
    db.add(other)
    db.commit()
    theirs = Device(
        organization_id=other.id,
        hostname="their-switch",
        ip_address="10.99.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(theirs)
    db.commit()

    response = as_user(client, admin).post(
        "/api/v1/devices/rediscover", json={"device_ids": [theirs.id]}
    )

    assert response.status_code == 404
    assert str(theirs.id) in response.json()["detail"]


def test_rediscovery_can_run_inline_for_a_whole_selection(client, admin, db, org):
    """run_async=false is for a caller that wants the results in the response"""
    device = Device(
        organization_id=org.id,
        hostname="sw-inline",
        ip_address="10.8.0.3",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(device)
    db.commit()

    from app.services import discovery_probe as probe

    result = probe.DeviceAssessment(
        probes=[],
        backup_eligible=False,
        auth_status="unreachable",
        auth_error="Nothing answered on SSH, telnet or SNMP",
    )

    with mock.patch.object(probe, "assess", return_value=result):
        response = as_user(client, admin).post(
            "/api/v1/devices/rediscover",
            json={"device_ids": [device.id], "run_async": False},
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] is False
    assert body["probed"] == 1
    assert body["authenticated"] == 0
    assert body["devices"][0]["message"] == "Nothing answered on SSH, telnet or SNMP"
