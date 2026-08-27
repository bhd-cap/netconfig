"""
API-level tests for user administration, settings and remote backup targets.

These go through the real FastAPI stack - dependency overrides for auth only -
against the real database, so route ordering, permission dependencies, request
validation and response shapes are all exercised as a client would meet them.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Organization, User
from app.models.administration import AppSettings, BackupTarget, Role
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

    organization = Organization(name="ApiAdminTest", is_active=True)
    db.add(organization)
    db.commit()
    user_admin.seed_system_roles(db, organization.id)
    return organization


@pytest.fixture
def roles(db, org):
    return {
        role.name: role
        for role in db.execute(
            select(Role).where(Role.organization_id == org.id)
        ).scalars()
    }


@pytest.fixture
def admin(db, org, roles):
    return user_admin.create_user(
        db,
        org.id,
        "apiadmin",
        "apiadmin@example.com",
        password="adminpass1",
        role_id=roles["Administrator"].id,
    )["user"]


@pytest.fixture
def viewer(db, org, roles):
    return user_admin.create_user(
        db,
        org.id,
        "apiviewer",
        "apiviewer@example.com",
        password="viewerpass1",
        role_id=roles["Viewer"].id,
    )["user"]


@pytest.fixture
def client(db):
    """
    A test client sharing the fixture's session

    Sharing the session keeps everything one test writes visible to the
    request handlers without a commit dance, and lets the fixture roll the
    whole test back at the end.
    """

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def as_user(client, user):
    """Point the client's authentication at a given user"""
    app.dependency_overrides[get_current_user] = lambda: user
    return client


# --------------------------------------------------------------------------
# Permissions and the caller
# --------------------------------------------------------------------------


def test_me_reports_role_and_permissions(client, admin):
    response = as_user(client, admin).get("/api/v1/users/me")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "apiadmin"
    assert body["role"]["name"] == "Administrator"
    assert "users:write" in body["permissions"]


def test_me_for_a_viewer_lists_only_read_permissions(client, viewer):
    body = as_user(client, viewer).get("/api/v1/users/me").json()

    assert "devices:read" in body["permissions"]
    assert "users:write" not in body["permissions"]
    assert all(not p.endswith(":delete") for p in body["permissions"])


def test_permission_catalogue_is_served(client, admin):
    response = as_user(client, admin).get("/api/v1/users/permissions")

    assert response.status_code == 200
    entries = response.json()
    assert {"permission", "resource", "action", "description"} <= set(entries[0])
    assert any(entry["permission"] == "users:write" for entry in entries)


def test_viewer_cannot_list_users(client, viewer):
    response = as_user(client, viewer).get("/api/v1/users")

    assert response.status_code == 403
    assert "users:read" in response.json()["detail"]


def test_logging_in_records_the_time(client, db, admin):
    """
    The user administration screen shows a last-login column

    The column has always existed; nothing was writing to it, so every account
    read 'never' however often it was used.
    """
    assert admin.last_login_at is None

    response = client.post(
        "/api/v1/auth/login",
        data={"username": "apiadmin", "password": "adminpass1"},
    )

    assert response.status_code == 200

    db.refresh(admin)
    assert admin.last_login_at is not None


def test_a_failed_login_does_not_record_a_time(client, db, admin):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "apiadmin", "password": "wrong"},
    )

    assert response.status_code == 401

    db.refresh(admin)
    assert admin.last_login_at is None


def test_change_own_password_requires_the_current_one(client, db, admin):
    wrong = as_user(client, admin).post(
        "/api/v1/users/me/password",
        json={"current_password": "nope", "new_password": "brandnewpass"},
    )
    assert wrong.status_code == 400

    right = as_user(client, admin).post(
        "/api/v1/users/me/password",
        json={"current_password": "adminpass1", "new_password": "brandnewpass"},
    )
    assert right.status_code == 204

    from app.core.security import verify_password

    db.refresh(admin)
    assert verify_password("brandnewpass", admin.hashed_password)
    assert admin.must_change_password is False


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_list_roles_includes_holder_counts(client, admin, viewer):
    response = as_user(client, admin).get("/api/v1/users/roles")

    assert response.status_code == 200
    by_name = {role["name"]: role for role in response.json()}

    assert by_name["Administrator"]["user_count"] == 1
    assert by_name["Viewer"]["user_count"] == 1
    assert by_name["Operator"]["user_count"] == 0
    assert by_name["Administrator"]["is_system"] is True


def test_roles_path_is_not_swallowed_by_the_user_id_route(client, admin):
    # /users/roles has to be matched before /users/{user_id}, or it 422s.
    assert as_user(client, admin).get("/api/v1/users/roles").status_code == 200
    assert as_user(client, admin).get("/api/v1/users/permissions").status_code == 200
    assert as_user(client, admin).get("/api/v1/users/me").status_code == 200


def test_create_role_rejects_an_unknown_permission(client, admin):
    response = as_user(client, admin).post(
        "/api/v1/users/roles",
        json={"name": "Bogus", "permissions": ["devices:levitate"]},
    )

    assert response.status_code == 400
    assert "Unknown permission" in response.json()["detail"]


def test_create_update_and_delete_a_role(client, admin):
    session = as_user(client, admin)

    created = session.post(
        "/api/v1/users/roles",
        json={
            "name": "Backup Operator",
            "description": "Runs backups",
            "permissions": ["devices:read", "backups:trigger"],
        },
    )
    assert created.status_code == 201
    role_id = created.json()["id"]
    assert created.json()["is_system"] is False

    updated = session.put(
        f"/api/v1/users/roles/{role_id}",
        json={"permissions": ["devices:read", "backups:trigger", "backups:read"]},
    )
    assert updated.status_code == 200
    assert "backups:read" in updated.json()["permissions"]

    assert session.delete(f"/api/v1/users/roles/{role_id}").status_code == 204
    assert session.get(f"/api/v1/users/roles/{role_id}").status_code == 404


def test_deleting_a_role_with_holders_conflicts(client, db, org, admin):
    session = as_user(client, admin)

    role_id = session.post(
        "/api/v1/users/roles",
        json={"name": "Held", "permissions": ["devices:read"]},
    ).json()["id"]

    session.post(
        "/api/v1/users",
        json={
            "username": "holder",
            "email": "holder@example.com",
            "password": "holderpass",
            "role_id": role_id,
        },
    )

    response = session.delete(f"/api/v1/users/roles/{role_id}")
    assert response.status_code == 409
    assert "still hold this role" in response.json()["detail"]


def test_system_role_cannot_be_renamed_over_the_api(client, admin, roles):
    response = as_user(client, admin).put(
        f"/api/v1/users/roles/{roles['Viewer'].id}", json={"name": "Spectator"}
    )

    assert response.status_code == 400
    assert "cannot be renamed" in response.json()["detail"]


def test_a_role_from_another_organization_is_not_found(client, db, admin):
    other = Organization(name="Other", is_active=True)
    db.add(other)
    db.commit()
    other_roles = user_admin.seed_system_roles(db, other.id)

    response = as_user(client, admin).get(f"/api/v1/users/roles/{other_roles[0].id}")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def test_create_user_generates_a_password_once(client, admin, roles):
    response = as_user(client, admin).post(
        "/api/v1/users",
        json={
            "username": "newbie",
            "email": "newbie@example.com",
            "full_name": "New Bie",
            "role_id": roles["Operator"].id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["generated_password"]
    assert len(body["generated_password"]) >= 12
    assert body["user"]["must_change_password"] is True
    assert body["user"]["role"]["name"] == "Operator"

    # Reading the user back never exposes the password.
    fetched = as_user(client, admin).get(f"/api/v1/users/{body['user']['id']}").json()
    assert "password" not in fetched
    assert "hashed_password" not in fetched


def test_create_user_with_a_supplied_password_returns_none(client, admin):
    body = as_user(client, admin).post(
        "/api/v1/users",
        json={
            "username": "chosen",
            "email": "chosen@example.com",
            "password": "chosenpass1",
        },
    ).json()

    assert body["generated_password"] is None


def test_duplicate_username_conflicts(client, admin):
    session = as_user(client, admin)
    payload = {
        "username": "twice",
        "email": "twice@example.com",
        "password": "twicepass1",
    }

    assert session.post("/api/v1/users", json=payload).status_code == 201

    payload["email"] = "different@example.com"
    response = session.post("/api/v1/users", json=payload)

    assert response.status_code == 409
    assert "already taken" in response.json()["detail"]


def test_list_users_paginates_and_filters(client, admin, viewer, roles):
    session = as_user(client, admin)

    for index in range(5):
        session.post(
            "/api/v1/users",
            json={
                "username": f"bulk{index}",
                "email": f"bulk{index}@example.com",
                "password": "bulkpass1",
                "role_id": roles["Operator"].id,
            },
        )

    page = session.get("/api/v1/users", params={"limit": 3}).json()
    assert page["total"] == 7
    assert page["page"] == 1
    assert page["page_size"] == 3
    assert page["total_pages"] == 3
    assert len(page["items"]) == 3

    second = session.get("/api/v1/users", params={"limit": 3, "skip": 3}).json()
    assert second["page"] == 2

    filtered = session.get(
        "/api/v1/users", params={"role_id": roles["Operator"].id}
    ).json()
    assert filtered["total"] == 5

    searched = session.get("/api/v1/users", params={"search": "apiviewer"}).json()
    assert searched["total"] == 1
    assert searched["items"][0]["username"] == "apiviewer"


def test_update_user_changes_role_and_admin_flag(client, db, admin, viewer, roles):
    response = as_user(client, admin).put(
        f"/api/v1/users/{viewer.id}",
        json={"role_id": roles["Administrator"].id, "full_name": "Promoted"},
    )

    assert response.status_code == 200
    assert response.json()["is_admin"] is True
    assert response.json()["full_name"] == "Promoted"
    assert response.json()["role"]["name"] == "Administrator"


def test_deactivate_and_reactivate_a_user(client, db, admin, viewer):
    session = as_user(client, admin)

    off = session.post(
        f"/api/v1/users/{viewer.id}/activation", json={"is_active": False}
    )
    assert off.status_code == 200
    assert off.json()["is_active"] is False
    assert off.json()["deactivated_at"] is not None

    on = session.post(
        f"/api/v1/users/{viewer.id}/activation", json={"is_active": True}
    )
    assert on.json()["is_active"] is True
    assert on.json()["deactivated_at"] is None


def test_cannot_deactivate_or_delete_yourself(client, admin):
    session = as_user(client, admin)

    response = session.post(
        f"/api/v1/users/{admin.id}/activation", json={"is_active": False}
    )
    assert response.status_code == 400
    assert "your own account" in response.json()["detail"]

    assert session.delete(f"/api/v1/users/{admin.id}").status_code == 400


def test_last_administrator_is_protected(client, db, org, roles):
    """
    The last active administrator cannot be demoted or deleted

    Without this an organization can lock itself out with one careless click.
    """
    first = user_admin.create_user(
        db, org.id, "admin_a", "admin_a@example.com",
        password="apass1234", role_id=roles["Administrator"].id,
    )["user"]

    second = user_admin.create_user(
        db, org.id, "admin_b", "admin_b@example.com",
        password="bpass1234", role_id=roles["Administrator"].id,
    )["user"]

    session = as_user(client, first)

    # With two administrators, deactivating one is fine.
    assert session.post(
        f"/api/v1/users/{second.id}/activation", json={"is_active": False}
    ).status_code == 200

    # Now the first is the only active administrator left.
    demote = session.put(f"/api/v1/users/{first.id}", json={"clear_role": True})
    assert demote.status_code == 400
    assert "administrator" in demote.json()["detail"].lower()

    # And a second administrator cannot be deactivated into nonexistence
    # through the role either.
    strip = session.put(
        f"/api/v1/users/roles/{roles['Administrator'].id}",
        json={"permissions": ["devices:read"]},
    )
    assert strip.status_code == 400
    assert "administrator" in strip.json()["detail"].lower()


def test_reset_password_returns_it_once(client, db, admin, viewer):
    response = as_user(client, admin).post(
        f"/api/v1/users/{viewer.id}/reset-password", json={}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "apiviewer"
    assert body["password"]
    assert body["must_change_password"] is True

    from app.core.security import verify_password

    db.refresh(viewer)
    assert verify_password(body["password"], viewer.hashed_password)


def test_reset_password_with_a_chosen_value(client, db, admin, viewer):
    body = as_user(client, admin).post(
        f"/api/v1/users/{viewer.id}/reset-password",
        json={"new_password": "chosenreset1", "must_change": False},
    ).json()

    assert body["password"] == "chosenreset1"
    assert body["must_change_password"] is False


def test_user_from_another_organization_is_not_found(client, db, admin):
    other = Organization(name="Elsewhere", is_active=True)
    db.add(other)
    db.commit()
    user_admin.seed_system_roles(db, other.id)

    stranger = user_admin.create_user(
        db, other.id, "stranger", "stranger@example.com", password="strangerpass"
    )["user"]

    session = as_user(client, admin)
    assert session.get(f"/api/v1/users/{stranger.id}").status_code == 404
    assert session.delete(f"/api/v1/users/{stranger.id}").status_code == 404


def test_delete_a_user(client, db, admin, viewer):
    session = as_user(client, admin)

    assert session.delete(f"/api/v1/users/{viewer.id}").status_code == 204
    assert session.get(f"/api/v1/users/{viewer.id}").status_code == 404


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_settings_are_created_on_first_read(client, db, org, admin):
    response = as_user(client, admin).get("/api/v1/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == org.id
    assert body["retention"]["retention_days"] == 90
    assert body["schedule"]["default_schedule_cron"] == "0 2 * * *"
    assert body["email"]["smtp_password_set"] is False
    assert body["maintenance"]["maintenance_windows"] == []

    stored = db.execute(
        select(AppSettings).where(AppSettings.organization_id == org.id)
    ).scalar_one()
    assert stored.retention_days == 90


def test_update_retention_and_schedule(client, admin):
    response = as_user(client, admin).put(
        "/api/v1/settings",
        json={
            "retention_days": 30,
            "retention_max_per_device": 10,
            "default_schedule_cron": "30 3 * * 1-5",
            "max_concurrent_backups": 4,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["retention"]["retention_days"] == 30
    assert body["retention"]["retention_max_per_device"] == 10
    assert body["schedule"]["default_schedule_cron"] == "30 3 * * 1-5"
    assert body["schedule"]["max_concurrent_backups"] == 4


def test_a_bad_cron_expression_is_refused_and_changes_nothing(client, admin):
    session = as_user(client, admin)
    session.put("/api/v1/settings", json={"retention_days": 45})

    response = session.put(
        "/api/v1/settings",
        json={"retention_days": 7, "default_schedule_cron": "not a cron"},
    )

    assert response.status_code == 400
    assert "not a valid cron" in response.json()["detail"]

    # Validation runs before anything is written.
    assert session.get("/api/v1/settings").json()["retention"]["retention_days"] == 45


def test_smtp_password_is_write_only(client, db, org, admin):
    session = as_user(client, admin)

    response = session.put(
        "/api/v1/settings",
        json={
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "mailer",
            "smtp_password": "mailsecret",
            "smtp_from_address": "backups@example.com",
        },
    )

    assert response.status_code == 200
    assert response.json()["email"]["smtp_password_set"] is True
    assert "smtp_password" not in response.json()["email"]

    stored = db.execute(
        select(AppSettings).where(AppSettings.organization_id == org.id)
    ).scalar_one()
    assert stored.smtp_password_encrypted != "mailsecret"
    assert encryption_service.decrypt(stored.smtp_password_encrypted) == "mailsecret"

    # Omitting it on a later edit leaves it alone.
    session.put("/api/v1/settings", json={"smtp_host": "smtp2.example.com"})
    db.refresh(stored)
    assert encryption_service.decrypt(stored.smtp_password_encrypted) == "mailsecret"

    # Clearing it explicitly removes it.
    cleared = session.put("/api/v1/settings", json={"clear_smtp_password": True})
    assert cleared.json()["email"]["smtp_password_set"] is False


def test_maintenance_windows_round_trip(client, admin):
    response = as_user(client, admin).put(
        "/api/v1/settings",
        json={
            "maintenance_timezone": "America/New_York",
            "maintenance_windows": [
                {
                    "name": "Sunday night",
                    "days": [6],
                    "start": "22:00",
                    "end": "02:00",
                    "suppress_backups": True,
                    "suppress_notifications": True,
                }
            ],
        },
    )

    assert response.status_code == 200
    maintenance = response.json()["maintenance"]
    assert maintenance["maintenance_timezone"] == "America/New_York"
    assert maintenance["maintenance_windows"][0]["name"] == "Sunday night"
    assert maintenance["next_window_start"] is not None


def test_a_malformed_window_is_refused(client, admin):
    response = as_user(client, admin).put(
        "/api/v1/settings",
        json={
            "maintenance_windows": [
                {"name": "Bad", "days": [9], "start": "22:00", "end": "02:00"}
            ]
        },
    )

    assert response.status_code == 400


def test_an_unknown_timezone_is_refused(client, admin):
    response = as_user(client, admin).put(
        "/api/v1/settings", json={"maintenance_timezone": "Mars/Olympus_Mons"}
    )

    assert response.status_code == 400
    assert "Unknown timezone" in response.json()["detail"]


def test_maintenance_status_reflects_an_open_window(client, admin):
    from datetime import datetime, timedelta, timezone

    # A window covering the whole of today and tomorrow, in UTC, is open now.
    now = datetime.now(timezone.utc)
    days = sorted({now.weekday(), (now + timedelta(days=1)).weekday()})

    session = as_user(client, admin)
    session.put(
        "/api/v1/settings",
        json={
            "maintenance_timezone": "UTC",
            "maintenance_windows": [
                {
                    "name": "All day",
                    "days": days,
                    "start": "00:00",
                    "end": "23:59",
                    "suppress_backups": True,
                    "suppress_notifications": False,
                }
            ],
        },
    )

    status_body = session.get("/api/v1/settings/maintenance/status").json()
    assert status_body["open_windows"] == ["All day"]
    assert status_body["backups_suppressed"] is True
    assert status_body["backups_suppressed_by"] == "All day"
    assert status_body["notifications_suppressed"] is False


def test_viewer_cannot_read_or_write_settings(client, viewer):
    session = as_user(client, viewer)

    assert session.get("/api/v1/settings").status_code == 403
    assert session.put("/api/v1/settings", json={"retention_days": 1}).status_code == 403


# --------------------------------------------------------------------------
# Remote backup targets
# --------------------------------------------------------------------------


def test_create_target_encrypts_the_password(client, db, org, admin):
    response = as_user(client, admin).post(
        "/api/v1/settings/targets",
        json={
            "name": "Archive",
            "protocol": "sftp",
            "host": "archive.example.com",
            "username": "backup",
            "password": "targetsecret",
            "remote_path": "/srv/configs",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["port"] == 22
    assert body["has_password"] is True
    assert body["has_private_key"] is False
    assert "password" not in body

    stored = db.execute(select(BackupTarget).where(BackupTarget.id == body["id"])).scalar_one()
    assert stored.encrypted_password != "targetsecret"
    assert encryption_service.decrypt(stored.encrypted_password) == "targetsecret"


def test_ftp_target_defaults_to_port_21(client, admin):
    body = as_user(client, admin).post(
        "/api/v1/settings/targets",
        json={
            "name": "FtpArchive",
            "protocol": "ftp",
            "host": "ftp.example.com",
            "username": "ftpuser",
            "password": "ftppass",
        },
    ).json()

    assert body["port"] == 21


def test_a_target_needs_a_credential(client, admin):
    response = as_user(client, admin).post(
        "/api/v1/settings/targets",
        json={
            "name": "Naked",
            "protocol": "sftp",
            "host": "nowhere.example.com",
            "username": "someone",
        },
    )

    assert response.status_code == 400
    assert "password" in response.json()["detail"]


def test_a_private_key_is_rejected_for_ftp(client, admin):
    response = as_user(client, admin).post(
        "/api/v1/settings/targets",
        json={
            "name": "WrongKey",
            "protocol": "ftp",
            "host": "ftp.example.com",
            "username": "ftpuser",
            "private_key": "-----BEGIN PRIVATE KEY-----",
        },
    )

    assert response.status_code == 400
    assert "SFTP" in response.json()["detail"]


def test_duplicate_target_name_conflicts(client, admin):
    session = as_user(client, admin)
    payload = {
        "name": "Same",
        "protocol": "sftp",
        "host": "a.example.com",
        "username": "u",
        "password": "p",
    }

    assert session.post("/api/v1/settings/targets", json=payload).status_code == 201

    payload["host"] = "b.example.com"
    assert session.post("/api/v1/settings/targets", json=payload).status_code == 409


def test_update_target_keeps_the_password_when_omitted(client, db, admin):
    session = as_user(client, admin)

    target_id = session.post(
        "/api/v1/settings/targets",
        json={
            "name": "Keep",
            "protocol": "sftp",
            "host": "keep.example.com",
            "username": "u",
            "password": "original",
        },
    ).json()["id"]

    updated = session.put(
        f"/api/v1/settings/targets/{target_id}", json={"remote_path": "/new/path"}
    )

    assert updated.status_code == 200
    assert updated.json()["remote_path"] == "/new/path"
    assert updated.json()["has_password"] is True

    stored = db.execute(select(BackupTarget).where(BackupTarget.id == target_id)).scalar_one()
    assert encryption_service.decrypt(stored.encrypted_password) == "original"


def test_clearing_the_only_credential_is_refused(client, admin):
    session = as_user(client, admin)

    target_id = session.post(
        "/api/v1/settings/targets",
        json={
            "name": "Solo",
            "protocol": "sftp",
            "host": "solo.example.com",
            "username": "u",
            "password": "only",
        },
    ).json()["id"]

    response = session.put(
        f"/api/v1/settings/targets/{target_id}", json={"clear_password": True}
    )

    assert response.status_code == 400
    assert session.get(f"/api/v1/settings/targets/{target_id}").json()["has_password"]


def test_list_and_delete_targets(client, admin):
    session = as_user(client, admin)

    for name, enabled in (("Live", True), ("Paused", False)):
        session.post(
            "/api/v1/settings/targets",
            json={
                "name": name,
                "protocol": "sftp",
                "host": f"{name.lower()}.example.com",
                "username": "u",
                "password": "p",
                "is_enabled": enabled,
            },
        )

    everything = session.get("/api/v1/settings/targets").json()
    assert [target["name"] for target in everything] == ["Live", "Paused"]

    enabled_only = session.get(
        "/api/v1/settings/targets", params={"enabled_only": True}
    ).json()
    assert [target["name"] for target in enabled_only] == ["Live"]

    target_id = everything[0]["id"]
    assert session.delete(f"/api/v1/settings/targets/{target_id}").status_code == 204
    assert session.get(f"/api/v1/settings/targets/{target_id}").status_code == 404


def test_testing_an_unreachable_target_reports_failure(client, db, admin):
    session = as_user(client, admin)

    target_id = session.post(
        "/api/v1/settings/targets",
        json={
            "name": "Unreachable",
            "protocol": "sftp",
            # Reserved TEST-NET-1 address: never routable, so this fails fast
            # rather than resolving to something real.
            "host": "192.0.2.1",
            "port": 22,
            "username": "u",
            "password": "p",
        },
    ).json()["id"]

    with_timeout = db.execute(
        select(BackupTarget).where(BackupTarget.id == target_id)
    ).scalar_one()

    from unittest import mock

    with mock.patch(
        "app.services.remote_backup.check_target_connection",
        return_value={"success": False, "message": "Connection refused"},
    ):
        response = session.post(f"/api/v1/settings/targets/{target_id}/test")

    assert response.status_code == 200
    assert response.json()["success"] is False

    db.refresh(with_timeout)
    assert with_timeout.last_status == "failed"
    assert with_timeout.last_error == "Connection refused"


def test_a_target_from_another_organization_is_not_found(client, db, admin):
    other = Organization(name="OtherTargets", is_active=True)
    db.add(other)
    db.commit()

    target = BackupTarget(
        organization_id=other.id,
        name="Theirs",
        protocol="sftp",
        host="theirs.example.com",
        port=22,
        username="u",
        encrypted_password=encryption_service.encrypt("p"),
        remote_path="/",
    )
    db.add(target)
    db.commit()

    assert as_user(client, admin).get(
        f"/api/v1/settings/targets/{target.id}"
    ).status_code == 404
