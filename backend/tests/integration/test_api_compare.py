"""
Comparing two stored configurations, through the real API

The bug these were written for: the page showed only changed lines whatever
the "changes only" switch was set to. The response carried a unified diff and
nothing else, so the viewer had no unchanged lines to reveal - it was
rendering the diff text itself as one side of a comparison against an empty
string, which also meant every line of it read as a deletion.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Configuration, Device, Organization
from app.services import user_admin
from app.services.config_comparison import ConfigurationComparison
from app.utils.encryption import encryption_service


# Long enough that a three-line context window leaves a good deal out, which
# is what makes "changes only" worth turning off.
_TAIL = "\n".join(f"ip access-list extended ACL-{n}" for n in range(1, 40))

BEFORE = f"""hostname core-01
!
interface GigabitEthernet1/0/1
 description uplink
 switchport mode trunk
!
interface GigabitEthernet1/0/2
 switchport access vlan 10
!
{_TAIL}
!
line vty 0 4
 transport input ssh
"""

AFTER = f"""hostname core-01
!
interface GigabitEthernet1/0/1
 description uplink to dist-01
 switchport mode trunk
!
interface GigabitEthernet1/0/2
 switchport access vlan 20
!
{_TAIL}
!
line vty 0 4
 transport input ssh
"""


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

    organization = Organization(name="CompareTest", is_active=True)
    db.add(organization)
    db.commit()
    user_admin.seed_system_roles(db, organization.id)
    return organization


@pytest.fixture
def roles(db, org):
    from app.models.administration import Role
    from sqlalchemy import select

    return {
        role.name: role
        for role in db.execute(
            select(Role).where(Role.organization_id == org.id)
        ).scalars()
    }


@pytest.fixture
def admin(db, org, roles):
    return user_admin.create_user(
        db, org.id, "cmpadmin", "cmpadmin@example.com",
        password="comparepass1", role_id=roles["Administrator"].id,
    )["user"]


@pytest.fixture
def device(db, org):
    made = Device(
        organization_id=org.id,
        hostname="core-01",
        ip_address="10.0.0.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        is_active=True,
    )
    db.add(made)
    db.commit()
    return made


@pytest.fixture
def configs(db, device, tmp_path):
    """Two stored configurations of the same device, an hour apart"""
    made = []
    now = datetime.now(timezone.utc)

    for index, (body, when) in enumerate(
        ((BEFORE, now - timedelta(hours=1)), (AFTER, now)), start=1
    ):
        path = tmp_path / f"core-01_{index}.cfg"
        path.write_text(body)

        config = Configuration(
            device_id=device.id,
            filename=path.name,
            file_path=str(path),
            file_size=len(body),
            backed_up_at=when,
            status="success",
        )
        db.add(config)
        made.append(config)

    db.commit()
    return made


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


def compare(client, admin, configs, **extra):
    response = as_user(client, admin).post(
        "/api/v1/compare/",
        json={
            "config1_id": configs[0].id,
            "config2_id": configs[1].id,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------


def test_the_diff_itself(client, admin, configs):
    body = compare(client, admin, configs)

    assert body["is_identical"] is False
    assert "description uplink to dist-01" in body["unified_diff"]
    assert body["statistics"]["added_lines"] == 2
    assert body["statistics"]["removed_lines"] == 2


def test_content_is_not_sent_unless_asked_for(client, admin, configs):
    """
    A caller that wants statistics should not be sent two configurations

    The dashboard's change summary and the scheduled-backup comparison both go
    through here.
    """
    body = compare(client, admin, configs)

    assert body["config1"].get("content") is None
    assert "content_omitted" not in body["config1"]


def test_include_content_returns_both_configurations_in_full(
    client, admin, configs
):
    """
    What "changes only" needs to be a real switch

    Turning it off means showing the lines the diff left out, and only the
    configurations themselves have those. Both files have already been read to
    produce the diff, so returning them costs nothing more.
    """
    body = compare(client, admin, configs, include_content=True)

    assert body["config1"]["content"] == BEFORE
    assert body["config2"]["content"] == AFTER
    assert body["config1"]["content_omitted"] is False
    assert body["config2"]["content_omitted"] is False

    # The unchanged lines are there, which is the whole point - a unified diff
    # with three lines of context does not carry the vty block.
    assert "transport input ssh" in body["config1"]["content"]
    assert "transport input ssh" not in body["unified_diff"]


def test_an_oversized_configuration_is_withheld_rather_than_sent(
    client, admin, configs, db, monkeypatch
):
    """
    A response no browser will render is worse than no content at all

    The viewer falls back to the unified diff and says so, rather than being
    handed 60 MB.
    """
    monkeypatch.setattr(ConfigurationComparison, "CONTENT_MAX_CHARS", 32)

    body = compare(client, admin, configs, include_content=True)

    assert body["config1"]["content"] is None
    assert body["config1"]["content_omitted"] is True
    # The comparison itself still happened.
    assert body["is_identical"] is False
    assert body["unified_diff"]


def test_identical_configurations_still_return_their_content(
    client, admin, device, db, tmp_path
):
    same = []
    for index in range(2):
        path = tmp_path / f"same_{index}.cfg"
        path.write_text(BEFORE)
        config = Configuration(
            device_id=device.id,
            filename=path.name,
            file_path=str(path),
            file_size=len(BEFORE),
            backed_up_at=datetime.now(timezone.utc) - timedelta(hours=index),
            status="success",
        )
        db.add(config)
        same.append(config)
    db.commit()

    body = compare(client, admin, same, include_content=True)

    assert body["is_identical"] is True
    assert body["unified_diff"] == ""
    assert body["config1"]["content"] == BEFORE


def test_configurations_from_different_devices_are_refused(
    client, admin, org, db, configs, tmp_path
):
    other = Device(
        organization_id=org.id,
        hostname="access-01",
        ip_address="10.0.0.2",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        is_active=True,
    )
    db.add(other)
    db.commit()

    path = tmp_path / "access-01.cfg"
    path.write_text(AFTER)
    stray = Configuration(
        device_id=other.id,
        filename=path.name,
        file_path=str(path),
        file_size=len(AFTER),
        backed_up_at=datetime.now(timezone.utc),
        status="success",
    )
    db.add(stray)
    db.commit()

    response = as_user(client, admin).post(
        "/api/v1/compare/",
        json={"config1_id": configs[0].id, "config2_id": stray.id},
    )

    assert response.status_code == 400
    assert "same device" in response.json()["detail"]
