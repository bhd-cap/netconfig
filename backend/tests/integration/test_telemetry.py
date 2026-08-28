"""
Storing polled hardware and readings, and serving them back.

The SNMP walk is mocked at the snmp_inventory.poll boundary - its parsing has
unit tests against captured MIB output. What matters here is what reaches the
database and what the endpoints return.
"""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Device, Organization
from app.models.administration import Role
from app.models.telemetry import DeviceComponent, DeviceSensor, SensorReading
from app.services import snmp_inventory, user_admin
from app.services.telemetry import TelemetryService
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
            "TRUNCATE organizations, users, devices, roles, credentials, "
            "device_probes, device_components, device_sensors, sensor_readings, "
            "audit_logs RESTART IDENTITY CASCADE"
        )
    )
    db.commit()

    organization = Organization(name="TelemetryTest", is_active=True)
    db.add(organization)
    db.commit()
    return organization


@pytest.fixture
def admin(db, org):
    roles = {role.name: role for role in user_admin.seed_system_roles(db, org.id)}
    # create_user returns a wrapper with the temporary password in it; the
    # dependency override needs the User itself.
    return user_admin.create_user(
        db, org.id, "admin", "admin@example.com",
        password="devpass1234", role_id=roles["Administrator"].id,
        must_change_password=False,
    )["user"]


@pytest.fixture
def device(db, org):
    row = Device(
        organization_id=org.id,
        hostname="sw-telemetry",
        ip_address="10.40.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        snmp_version="2c",
        snmp_community=encryption_service.encrypt("public"),
    )
    db.add(row)
    db.commit()
    return row


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


def result(components=(), readings=(), sources=("entity-mib",), error=None):
    return snmp_inventory.PollResult(
        components=list(components),
        readings=list(readings),
        sources=list(sources),
        error=error,
    )


def component(index, **kwargs):
    return snmp_inventory.Component(index=index, **kwargs)


def reading(key, **kwargs):
    kwargs.setdefault("name", key)
    kwargs.setdefault("sensor_type", "temperature")
    return snmp_inventory.Reading(key=key, **kwargs)


def poll(db, org, device_ids=None, returns=None, **kwargs):
    with mock.patch.object(snmp_inventory, "poll", return_value=returns):
        return TelemetryService(db).poll(
            organization_id=org.id, device_ids=device_ids, **kwargs
        )


# --------------------------------------------------------------------------
# Storing what a poll found
# --------------------------------------------------------------------------


def test_components_and_readings_are_stored(db, org, device):
    summary = poll(
        db, org, [device.id],
        returns=result(
            components=[
                component("1", name="Switch 1", component_class="chassis",
                          model_name="C9300-48P", serial_number="FOC2137L0AB"),
                component("2000", name="PSU A", component_class="power",
                          serial_number="ART2101B3QQ", parent_index="1"),
            ],
            readings=[
                reading("entity:1", name="Inlet", value=42.0, unit="°C"),
                reading("entity:2", name="Fan 1", sensor_type="fan",
                        value=6300.0, unit="RPM"),
            ],
        ),
    )

    assert summary.answered == 1
    assert summary.components == 2
    assert summary.sensors == 2

    stored = {
        c.entity_index: c
        for c in db.scalars(
            select(DeviceComponent).where(DeviceComponent.device_id == device.id)
        )
    }
    assert stored["1"].serial_number == "FOC2137L0AB"
    assert stored["2000"].component_class == "power"

    sensors = {
        s.sensor_key: s
        for s in db.scalars(
            select(DeviceSensor).where(DeviceSensor.device_id == device.id)
        )
    }
    assert sensors["entity:1"].value == 42.0
    assert sensors["entity:2"].unit == "RPM"


def test_polling_twice_updates_rather_than_duplicates(db, org, device):
    """Upserted on (device, index) and (device, sensor key)"""
    first = result(
        components=[component("1", name="Switch 1", component_class="chassis")],
        readings=[reading("entity:1", value=40.0)],
    )
    poll(db, org, [device.id], returns=first)

    second = result(
        components=[component("1", name="Switch 1", component_class="chassis")],
        readings=[reading("entity:1", value=44.0)],
    )
    poll(db, org, [device.id], returns=second)

    components = list(
        db.scalars(select(DeviceComponent).where(DeviceComponent.device_id == device.id))
    )
    sensors = list(
        db.scalars(select(DeviceSensor).where(DeviceSensor.device_id == device.id))
    )

    assert len(components) == 1
    assert len(sensors) == 1
    assert sensors[0].value == 44.0


def test_hardware_that_disappears_is_aged_not_deleted(db, org, device):
    """
    The serial of the part that used to be in that slot is the point

    A power supply that stops being reported has been pulled, and an inventory
    that forgets it cannot answer what was replaced.
    """
    poll(
        db, org, [device.id],
        returns=result(components=[
            component("1", name="Switch 1", component_class="chassis"),
            component("2000", name="PSU A", component_class="power",
                      serial_number="ART2101B3QQ"),
        ]),
    )

    # Second poll: the power supply is gone.
    poll(
        db, org, [device.id],
        returns=result(components=[
            component("1", name="Switch 1", component_class="chassis"),
        ]),
    )

    stored = {
        c.entity_index: c
        for c in db.scalars(
            select(DeviceComponent).where(DeviceComponent.device_id == device.id)
        )
    }

    assert stored["1"].is_active is True
    assert stored["2000"].is_active is False
    assert stored["2000"].serial_number == "ART2101B3QQ"


def test_history_is_written_for_numeric_readings(db, org, device):
    poll(
        db, org, [device.id],
        returns=result(readings=[
            reading("entity:1", value=40.0),
            reading("entity:2", value=41.0),
        ]),
    )
    poll(
        db, org, [device.id],
        returns=result(readings=[
            reading("entity:1", value=42.0),
            reading("entity:2", value=43.0),
        ]),
    )

    readings = list(db.scalars(select(SensorReading)))

    assert len(readings) == 4
    assert sorted(r.value for r in readings) == [40.0, 41.0, 42.0, 43.0]


def test_a_reading_with_no_value_leaves_the_chart_alone(db, org, device):
    """
    A power supply reporting "failed" and no number is a row in the sensor
    table and nothing in the history: a chart of nulls is noise, and its state
    is already recorded.
    """
    poll(
        db, org, [device.id],
        returns=result(readings=[
            reading("envmon:psu:1", name="PSU 1", sensor_type="power",
                    value=None, status="failed"),
        ]),
    )

    sensors = list(db.scalars(select(DeviceSensor)))
    history = list(db.scalars(select(SensorReading)))

    assert len(sensors) == 1
    assert sensors[0].status == "failed"
    assert history == []


def test_history_can_be_turned_off(db, org, device):
    poll(
        db, org, [device.id],
        returns=result(readings=[reading("entity:1", value=40.0)]),
        keep_history=False,
    )

    assert list(db.scalars(select(SensorReading))) == []


def test_pruning_drops_readings_past_the_window(db, org, device):
    """
    The only table here that grows with time rather than with the estate

    One switch with twenty sensors polled every half hour writes about a
    million rows a year.
    """
    poll(db, org, [device.id], returns=result(readings=[reading("entity:1", value=40.0)]))

    sensor = db.scalars(select(DeviceSensor)).one()
    old = SensorReading(
        sensor_id=sensor.id,
        value=1.0,
        recorded_at=datetime.now(timezone.utc) - timedelta(days=45),
    )
    db.add(old)
    db.commit()

    removed = TelemetryService(db).prune_history(older_than_days=30)

    remaining = list(db.scalars(select(SensorReading)))
    assert removed == 1
    assert len(remaining) == 1
    assert remaining[0].value == 40.0


def test_a_device_with_no_snmp_is_reported_rather_than_walked(db, org):
    """Walking it would cost a full timeout per table to learn nothing"""
    bare = Device(
        organization_id=org.id,
        hostname="sw-no-snmp",
        ip_address="10.40.0.9",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(bare)
    db.commit()

    summary = poll(db, org, [bare.id], returns=result())

    assert summary.failed == 1
    assert "No SNMP credentials" in summary.devices[0]["error"]


def test_a_device_that_does_not_answer_does_not_stop_the_sweep(db, org, device):
    second = Device(
        organization_id=org.id,
        hostname="sw-quiet",
        ip_address="10.40.0.2",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        snmp_version="2c",
        snmp_community=encryption_service.encrypt("public"),
    )
    db.add(second)
    db.commit()

    def by_host(client, **kwargs):
        if client.host == "10.40.0.2":
            return result(error="OSError: timed out")
        return result(readings=[reading("entity:1", value=40.0)])

    with mock.patch.object(snmp_inventory, "poll", side_effect=by_host):
        summary = TelemetryService(db).poll(organization_id=org.id)

    assert summary.polled == 2
    assert summary.answered == 1
    assert summary.failed == 1


def test_only_snmp_capable_devices_are_polled_by_default(db, org, device):
    Device_without = Device(
        organization_id=org.id,
        hostname="sw-cli-only",
        ip_address="10.40.0.3",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(Device_without)
    db.commit()

    summary = poll(db, org, returns=result(readings=[reading("entity:1", value=40.0)]))

    assert summary.polled == 1
    assert summary.devices[0]["hostname"] == "sw-telemetry"


# --------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------


def test_device_components_endpoint(client, admin, db, org, device):
    poll(
        db, org, [device.id],
        returns=result(components=[
            component("1", name="Switch 1", component_class="chassis",
                      model_name="C9300-48P", serial_number="FOC2137L0AB"),
        ]),
    )

    body = as_user(client, admin).get(f"/api/v1/devices/{device.id}/components").json()

    assert body["hostname"] == "sw-telemetry"
    assert body["components"][0]["serial_number"] == "FOC2137L0AB"


def test_device_sensors_endpoint_returns_history_per_sensor(
    client, admin, db, org, device
):
    """The shape a chart wants, rather than one flat list to regroup"""
    for value in (40.0, 41.0, 42.0):
        poll(db, org, [device.id], returns=result(readings=[
            reading("entity:1", name="Inlet", value=value),
        ]))

    body = as_user(client, admin).get(f"/api/v1/devices/{device.id}/sensors").json()

    sensor = body["sensors"][0]
    assert sensor["name"] == "Inlet"
    assert sensor["value"] == 42.0
    assert [point["value"] for point in sensor["history"]] == [40.0, 41.0, 42.0]


def test_history_can_be_asked_for_by_window(client, admin, db, org, device):
    poll(db, org, [device.id], returns=result(readings=[reading("entity:1", value=40.0)]))

    body = (
        as_user(client, admin)
        .get(f"/api/v1/devices/{device.id}/sensors", params={"history_hours": 0})
        .json()
    )

    assert body["sensors"][0]["history"] == []


def test_the_estate_wide_component_list_can_be_searched_by_serial(
    client, admin, db, org, device
):
    """"Which chassis is serial FOC2137L0AB in" is the asset-register question"""
    poll(
        db, org, [device.id],
        returns=result(components=[
            component("1", name="Switch 1", component_class="chassis",
                      serial_number="FOC2137L0AB"),
            component("2000", name="PSU A", component_class="power",
                      serial_number="ART2101B3QQ"),
        ]),
    )

    session = as_user(client, admin)

    everything = session.get("/api/v1/devices/components").json()
    assert everything["total"] == 2
    assert everything["items"][0]["device_hostname"] == "sw-telemetry"

    found = session.get(
        "/api/v1/devices/components", params={"search": "FOC2137"}
    ).json()
    assert found["total"] == 1
    assert found["items"][0]["name"] == "Switch 1"

    supplies = session.get(
        "/api/v1/devices/components", params={"component_class": "power"}
    ).json()
    assert supplies["total"] == 1


def test_the_estate_wide_sensor_list_summarises_by_type(client, admin, db, org, device):
    """
    The summary is what a chart is drawn from

    A dashboard should not have to pull every sensor row to draw one bar.
    """
    poll(
        db, org, [device.id],
        returns=result(readings=[
            reading("entity:1", name="Inlet", value=38.0, unit="°C"),
            reading("entity:2", name="Outlet", value=46.0, unit="°C"),
            reading("entity:3", name="PSU", sensor_type="power", value=None,
                    status="failed"),
        ]),
    )

    body = as_user(client, admin).get("/api/v1/devices/sensors").json()

    by_type = {entry["sensor_type"]: entry for entry in body["summary"]}

    assert by_type["temperature"]["count"] == 2
    assert by_type["temperature"]["min"] == 38.0
    assert by_type["temperature"]["max"] == 46.0
    assert by_type["temperature"]["avg"] == 42.0
    assert by_type["power"]["unhealthy"] == 1
    assert len(body["items"]) == 3

    # One entry per type. A state-only sensor carries no unit, and splitting
    # the group on the unit would give it a tile of its own with no numbers
    # in it.
    assert len(body["summary"]) == len(by_type)


def test_a_state_only_sensor_shares_its_types_summary_row(
    client, admin, db, org, device
):
    """
    A supply that reports "failed" and nothing else belongs to the power tile

    It has no unit, so grouping the summary on the unit would give it a second
    tile with a count of one and no reading in it.
    """
    poll(
        db, org, [device.id],
        returns=result(readings=[
            reading("entity:1", name="Draw", sensor_type="power", unit="W",
                    value=412.0),
            reading("entity:2", name="PSU B", sensor_type="power", value=None,
                    status="failed"),
        ]),
    )

    body = as_user(client, admin).get("/api/v1/devices/sensors").json()
    power = [entry for entry in body["summary"] if entry["sensor_type"] == "power"]

    assert len(power) == 1
    assert power[0]["count"] == 2
    assert power[0]["unhealthy"] == 1
    # The unit survives the one row that does not carry it.
    assert power[0]["unit"] == "W"
    assert power[0]["max"] == 412.0


def test_polling_one_device_from_the_api(client, admin, db, org, device):
    with mock.patch.object(
        snmp_inventory,
        "poll",
        return_value=result(readings=[reading("entity:1", value=40.0)]),
    ):
        response = as_user(client, admin).post(
            f"/api/v1/devices/{device.id}/poll-telemetry"
        )

    assert response.status_code == 200, response.text
    assert response.json()["sensors"] == 1


def test_bulk_polling_is_queued(client, admin, device):
    with mock.patch("app.tasks.telemetry.poll_telemetry_task.delay") as delay:
        delay.return_value = mock.Mock(id="task-telemetry-1")

        response = as_user(client, admin).post(
            "/api/v1/devices/poll-telemetry", json={"device_ids": [device.id]}
        )

    assert response.status_code == 202, response.text
    assert response.json()["task_id"] == "task-telemetry-1"


def test_telemetry_is_scoped_to_the_organization(client, admin, db, org, device):
    """Another tenant's hardware must not appear in this one's asset register"""
    other = Organization(name="Someone else")
    db.add(other)
    db.commit()

    theirs = Device(
        organization_id=other.id,
        hostname="their-switch",
        ip_address="10.99.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        snmp_version="2c",
        snmp_community=encryption_service.encrypt("public"),
    )
    db.add(theirs)
    db.commit()

    poll(db, other, [theirs.id], returns=result(components=[
        component("1", name="Their chassis", component_class="chassis",
                  serial_number="NOTYOURS1"),
    ]))
    poll(db, org, [device.id], returns=result(components=[
        component("1", name="Our chassis", component_class="chassis",
                  serial_number="OURS0001"),
    ]))

    body = as_user(client, admin).get("/api/v1/devices/components").json()

    serials = {item["serial_number"] for item in body["items"]}
    assert serials == {"OURS0001"}
