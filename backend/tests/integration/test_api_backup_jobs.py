"""
API-level tests for a backup job's device filter.

Covers the seam the job editor uses: that a bad filter is refused while
someone is looking at the form rather than at 2am, that the preview and
options endpoints answer, and that route ordering puts the literal paths ahead
of /{job_id}.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import BackupJob, Device, Organization
from app.models.administration import Role
from app.services import user_admin
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

    organization = Organization(name="ApiJobTest", is_active=True)
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
        db, org.id, "jobadmin", "jobadmin@example.com",
        password="jobpass1234", role_id=role.id,
    )["user"]


@pytest.fixture
def devices(db, org):
    made = {}
    spec = [
        ("core-nyc-01", "cisco_ios", "NYC", {"role": "core"}, "ssh"),
        ("core-lon-01", "arista_eos", "LON", {"role": "core"}, "ssh"),
        ("access-nyc-01", "hp_procurve", "NYC", {"role": "access"}, "telnet"),
        ("sensor-01", "fortinet", "NYC", {}, "snmp"),
    ]

    for index, (hostname, device_type, location, tags, transport) in enumerate(spec, 1):
        device = Device(
            organization_id=org.id,
            hostname=hostname,
            ip_address=f"10.8.0.{index}",
            device_type=device_type,
            location=location,
            tags=tags,
            transport=transport,
            port=23 if transport == "telnet" else 22,
            username="admin",
            encrypted_password=encryption_service.encrypt("secret"),
            is_active=True,
        )
        db.add(device)
        made[hostname] = device

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


# --------------------------------------------------------------------------
# Route ordering
# --------------------------------------------------------------------------


def test_literal_paths_are_not_swallowed_by_job_id(client, admin, devices):
    """
    /preview-filter and /filter-options must be declared before /{job_id}

    Otherwise FastAPI tries to parse 'preview-filter' as an integer job id and
    returns 422.
    """
    session = as_user(client, admin)

    assert session.post("/api/v1/backup-jobs/preview-filter", json={}).status_code == 200
    assert session.get("/api/v1/backup-jobs/filter-options").status_code == 200


# --------------------------------------------------------------------------
# Creating and editing a job with a filter
# --------------------------------------------------------------------------


def test_create_a_job_with_a_filter(client, db, admin, devices):
    response = as_user(client, admin).post(
        "/api/v1/backup-jobs",
        json={
            "name": "NYC core",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"locations": ["NYC"], "tags": {"role": "core"}},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["device_filter"]["locations"] == ["NYC"]
    assert body["device_filter"]["tags"] == {"role": "core"}

    stored = db.execute(select(BackupJob).where(BackupJob.id == body["id"])).scalar_one()
    assert stored.device_filter["locations"] == ["NYC"]


def test_creating_a_job_without_a_filter_stores_an_empty_one(client, admin, devices):
    body = as_user(client, admin).post(
        "/api/v1/backup-jobs",
        json={"name": "Everything", "schedule_cron": "0 3 * * *"},
    ).json()

    assert body["device_filter"] == {}


def test_a_bad_filter_key_is_refused_at_create(client, admin, devices):
    response = as_user(client, admin).post(
        "/api/v1/backup-jobs",
        json={
            "name": "Typo",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"tags.location": "NYC"},
        },
    )

    assert response.status_code == 400
    assert "Unknown filter key" in response.json()["detail"]


def test_a_bad_device_type_is_refused_at_create(client, admin, devices):
    response = as_user(client, admin).post(
        "/api/v1/backup-jobs",
        json={
            "name": "Bad type",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"device_types": ["juniper_srx"]},
        },
    )

    assert response.status_code == 400
    assert "Unknown device type" in response.json()["detail"]


def test_updating_a_filter(client, db, admin, devices):
    session = as_user(client, admin)

    job_id = session.post(
        "/api/v1/backup-jobs",
        json={"name": "Editable", "schedule_cron": "0 2 * * *"},
    ).json()["id"]

    updated = session.put(
        f"/api/v1/backup-jobs/{job_id}",
        json={"device_filter": {"hostname_pattern": "core-*"}},
    )

    assert updated.status_code == 200
    assert updated.json()["device_filter"] == {"hostname_pattern": "core-*"}


def test_a_bad_filter_is_refused_at_update_and_changes_nothing(
    client, db, admin, devices
):
    session = as_user(client, admin)

    job_id = session.post(
        "/api/v1/backup-jobs",
        json={
            "name": "Guarded",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"locations": ["NYC"]},
        },
    ).json()["id"]

    response = session.put(
        f"/api/v1/backup-jobs/{job_id}",
        json={"device_filter": {"transports": ["smoke-signal"]}},
    )

    assert response.status_code == 400
    assert session.get(f"/api/v1/backup-jobs/{job_id}").json()["device_filter"] == {
        "locations": ["NYC"]
    }


def test_sending_null_clears_the_filter(client, admin, devices):
    session = as_user(client, admin)

    job_id = session.post(
        "/api/v1/backup-jobs",
        json={
            "name": "Narrowed",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"locations": ["NYC"]},
        },
    ).json()["id"]

    cleared = session.put(
        f"/api/v1/backup-jobs/{job_id}", json={"device_filter": None}
    )

    assert cleared.status_code == 200
    assert cleared.json()["device_filter"] == {}


# --------------------------------------------------------------------------
# Preview and options
# --------------------------------------------------------------------------


def test_preview_reports_what_a_filter_would_select(client, admin, devices):
    response = as_user(client, admin).post(
        "/api/v1/backup-jobs/preview-filter",
        json={"device_filter": {"locations": ["NYC"], "tags": {"role": "core"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [entry["hostname"] for entry in body["devices"]] == ["core-nyc-01"]
    assert "NYC" in body["summary"]


def test_preview_with_no_filter_excludes_snmp(client, admin, devices):
    body = as_user(client, admin).post(
        "/api/v1/backup-jobs/preview-filter", json={}
    ).json()

    hostnames = [entry["hostname"] for entry in body["devices"]]
    assert body["total"] == 3
    assert "sensor-01" not in hostnames
    assert body["summary"] == "Every device that can be backed up"


def test_preview_rejects_a_bad_filter(client, admin, devices):
    response = as_user(client, admin).post(
        "/api/v1/backup-jobs/preview-filter",
        json={"device_filter": {"nonsense": True}},
    )

    assert response.status_code == 400
    assert "Unknown filter key" in response.json()["detail"]


def test_filter_options_offer_only_what_exists(client, admin, devices):
    body = as_user(client, admin).get("/api/v1/backup-jobs/filter-options").json()

    assert body["locations"] == ["LON", "NYC"]
    assert set(body["device_types"]) == {
        "cisco_ios",
        "arista_eos",
        "hp_procurve",
        "fortinet",
    }
    assert body["tag_keys"] == ["role"]
    assert body["transports"] == ["ssh", "telnet", "snmp"]
    assert "hostname_pattern" in body["filter_keys"]


def test_filter_options_are_tenant_scoped(client, db, admin, devices):
    other = Organization(name="OtherJobs", is_active=True)
    db.add(other)
    db.commit()

    db.add(
        Device(
            organization_id=other.id,
            hostname="their-switch",
            ip_address="192.168.70.1",
            device_type="cisco_nxos",
            location="MARS",
            tags={"secret": "yes"},
            username="admin",
            encrypted_password=encryption_service.encrypt("secret"),
        )
    )
    db.commit()

    body = as_user(client, admin).get("/api/v1/backup-jobs/filter-options").json()

    assert "MARS" not in body["locations"]
    assert "cisco_nxos" not in body["device_types"]
    assert "secret" not in body["tag_keys"]


# --------------------------------------------------------------------------
# A saved job's current scope
# --------------------------------------------------------------------------


def test_a_jobs_devices_are_resolved_live(client, db, admin, devices, org):
    session = as_user(client, admin)

    job_id = session.post(
        "/api/v1/backup-jobs",
        json={
            "name": "Core devices",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"tags": {"role": "core"}},
        },
    ).json()["id"]

    before = session.get(f"/api/v1/backup-jobs/{job_id}/devices").json()
    assert before["total"] == 2
    assert before["job_name"] == "Core devices"

    # A device tagged after the job was saved is picked up without editing it.
    db.add(
        Device(
            organization_id=org.id,
            hostname="core-fra-01",
            ip_address="10.8.0.9",
            device_type="cisco_ios",
            location="FRA",
            tags={"role": "core"},
            username="admin",
            encrypted_password=encryption_service.encrypt("secret"),
        )
    )
    db.commit()

    after = session.get(f"/api/v1/backup-jobs/{job_id}/devices").json()
    assert after["total"] == 3
    assert "core-fra-01" in [entry["hostname"] for entry in after["devices"]]


def test_a_job_with_a_broken_stored_filter_reports_a_conflict(
    client, db, admin, devices, org
):
    """
    A filter that no longer validates must not read as "covers everything"
    """
    job = BackupJob(
        organization_id=org.id,
        name="Broken",
        schedule_cron="0 2 * * *",
        is_enabled=True,
        device_filter={"device_types": ["a_type_that_was_removed"]},
    )
    db.add(job)
    db.commit()

    response = as_user(client, admin).get(f"/api/v1/backup-jobs/{job.id}/devices")

    assert response.status_code == 409
    assert "no longer valid" in response.json()["detail"]


def test_another_organizations_job_is_not_reachable(client, db, admin):
    other = Organization(name="Elsewhere", is_active=True)
    db.add(other)
    db.commit()

    job = BackupJob(
        organization_id=other.id,
        name="Theirs",
        schedule_cron="0 2 * * *",
        is_enabled=True,
        device_filter={},
    )
    db.add(job)
    db.commit()

    response = as_user(client, admin).get(f"/api/v1/backup-jobs/{job.id}/devices")
    assert response.status_code == 403


def test_clearing_a_device_filter_actually_clears_it(client, admin, org, db):
    """
    Sending null must widen the job back to every device

    The endpoint documents this, and it silently did nothing: the repository
    skipped None values, so the job kept its old filter while the API reported
    success. A job that quietly holds a narrower scope than the operator
    believes is the failure mode the filter validation exists to prevent.
    """
    created = as_user(client, admin).post(
        "/api/v1/backup-jobs",
        json={
            "name": "clear-me",
            "schedule_cron": "0 2 * * *",
            "device_filter": {"locations": ["NYC"]},
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    updated = as_user(client, admin).put(
        f"/api/v1/backup-jobs/{job_id}", json={"device_filter": None}
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["device_filter"] in (None, {})

    db.expire_all()
    stored = db.get(BackupJob, job_id)
    assert stored.device_filter in (None, {})
