"""
API-level tests for the device transport and SNMP fields.

The connector already has its own transport tests; these cover the seam the
UI actually uses - that a device can be created and edited as telnet or SNMP,
that the secrets are encrypted and never returned, and that the backup and
connectivity paths use the transport the device is configured for.
"""
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
