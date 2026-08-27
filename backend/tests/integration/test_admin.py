"""
User administration and application settings tests against a real database.
"""
from datetime import datetime, time, timezone

import pytest
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.core.permissions import all_permissions, has_permission
from app.models import AppSettings, Organization, Role, User
from app.services import app_settings as settings_service
from app.services import user_admin
from app.services.user_admin import UserAdminError


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

    organization = Organization(name="AdminTest", is_active=True)
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
        db, org.id, "admin1", "admin1@example.com",
        password="adminpass", role_id=roles["Administrator"].id,
    )["user"]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_system_roles_are_seeded(db, org, roles):
    assert set(roles) == {"Administrator", "Operator", "Viewer"}
    assert all(role.is_system for role in roles.values())
    assert roles["Administrator"].permissions == ["*"]


def test_seeding_is_idempotent(db, org):
    user_admin.seed_system_roles(db, org.id)
    user_admin.seed_system_roles(db, org.id)

    count = len(db.execute(select(Role).where(Role.organization_id == org.id)).scalars().all())
    assert count == 3


def test_create_role_validates_permissions(db, org):
    with pytest.raises(UserAdminError, match="Unknown permission"):
        user_admin.create_role(db, org.id, "Broken", ["devices:fly"])

    role = user_admin.create_role(
        db, org.id, "Backup Operator", ["devices:read", "backups:trigger"]
    )
    assert role.id
    assert role.is_system is False


def test_duplicate_role_name_rejected(db, org):
    user_admin.create_role(db, org.id, "Auditor", ["audit:read"])
    with pytest.raises(UserAdminError, match="already exists"):
        user_admin.create_role(db, org.id, "Auditor", ["audit:read"])


def test_system_role_cannot_be_renamed_or_deleted(db, org, roles):
    with pytest.raises(UserAdminError, match="cannot be renamed"):
        user_admin.update_role(db, roles["Viewer"], name="Spectator")

    with pytest.raises(UserAdminError, match="cannot be deleted"):
        user_admin.delete_role(db, roles["Viewer"])


def test_role_with_holders_cannot_be_deleted(db, org, roles):
    role = user_admin.create_role(db, org.id, "Temp", ["devices:read"])
    user_admin.create_user(
        db, org.id, "tempuser", "temp@example.com", password="x", role_id=role.id
    )

    with pytest.raises(UserAdminError, match="still hold this role"):
        user_admin.delete_role(db, role)


def test_editing_a_role_updates_its_holders_admin_flag(db, org, roles):
    role = user_admin.create_role(db, org.id, "Elevated", ["devices:read"])
    created = user_admin.create_user(
        db, org.id, "elev", "elev@example.com", password="x", role_id=role.id
    )["user"]
    assert created.is_admin is False

    user_admin.update_role(db, role, permissions=["users:write", "devices:read"])
    db.refresh(created)

    assert created.is_admin is True


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------


def test_effective_permissions_come_from_the_role(db, org, roles):
    viewer = user_admin.create_user(
        db, org.id, "viewer1", "viewer1@example.com",
        password="x", role_id=roles["Viewer"].id,
    )["user"]

    permissions = user_admin.effective_permissions(db, viewer)
    assert "devices:read" in permissions
    assert "devices:write" not in permissions

    assert user_admin.user_has_permission(db, viewer, "devices:read")
    assert not user_admin.user_has_permission(db, viewer, "devices:write")


def test_administrator_wildcard_grants_everything(db, org, admin):
    permissions = user_admin.effective_permissions(db, admin)
    assert set(permissions) == set(all_permissions())
    assert user_admin.user_has_permission(db, admin, "settings:write")


def test_legacy_account_without_a_role_still_works(db, org):
    """Accounts created before roles existed must keep their access."""
    legacy = User(
        organization_id=org.id, username="legacy", email="legacy@example.com",
        hashed_password="x", is_active=True, is_admin=True, is_superuser=False,
    )
    db.add(legacy)
    db.commit()

    assert user_admin.user_has_permission(db, legacy, "users:write")

    plain = User(
        organization_id=org.id, username="plain", email="plain@example.com",
        hashed_password="x", is_active=True, is_admin=False, is_superuser=False,
    )
    db.add(plain)
    db.commit()

    assert user_admin.user_has_permission(db, plain, "devices:read")
    assert not user_admin.user_has_permission(db, plain, "devices:write")


def test_superuser_bypasses_role_checks(db, org, roles):
    root = user_admin.create_user(
        db, org.id, "root", "root@example.com", password="x",
        role_id=roles["Viewer"].id,
    )["user"]
    root.is_superuser = True
    db.commit()

    assert user_admin.user_has_permission(db, root, "settings:write")


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


def test_create_user_generates_a_password_when_none_given(db, org, roles):
    result = user_admin.create_user(
        db, org.id, "generated", "gen@example.com", role_id=roles["Viewer"].id
    )

    assert result["generated_password"]
    assert len(result["generated_password"]) >= 16
    assert result["user"].must_change_password is True


def test_create_user_rejects_duplicates(db, org, admin):
    with pytest.raises(UserAdminError, match="already taken"):
        user_admin.create_user(db, org.id, "admin1", "other@example.com", password="x")

    with pytest.raises(UserAdminError, match="already registered"):
        user_admin.create_user(db, org.id, "other", "admin1@example.com", password="x")


def test_deactivate_and_reactivate(db, org, roles, admin):
    user = user_admin.create_user(
        db, org.id, "temp", "temp@example.com", password="x",
        role_id=roles["Viewer"].id,
    )["user"]

    user_admin.set_active(db, user, False)
    assert user.is_active is False
    assert user.deactivated_at is not None

    user_admin.set_active(db, user, True)
    assert user.is_active is True
    assert user.deactivated_at is None


def test_last_active_administrator_cannot_be_deactivated(db, org, admin):
    with pytest.raises(UserAdminError, match="only active administrator"):
        user_admin.set_active(db, admin, False)


def test_last_active_administrator_cannot_be_deleted(db, org, admin):
    with pytest.raises(UserAdminError, match="only active administrator"):
        user_admin.delete_user(db, admin)


def test_last_administrator_cannot_be_demoted(db, org, roles, admin):
    with pytest.raises(UserAdminError, match="only active administrator"):
        user_admin.update_user(db, admin, role_id=roles["Viewer"].id)

    with pytest.raises(UserAdminError, match="only active administrator"):
        user_admin.update_user(db, admin, clear_role=True)


def test_administrator_can_be_demoted_once_another_exists(db, org, roles, admin):
    second = user_admin.create_user(
        db, org.id, "admin2", "admin2@example.com", password="x",
        role_id=roles["Administrator"].id,
    )["user"]
    assert second.is_admin is True

    user_admin.update_user(db, admin, role_id=roles["Operator"].id)
    assert admin.is_admin is False


def test_an_inactive_administrator_does_not_count(db, org, roles, admin):
    """A deactivated admin must not be treated as cover for demoting the last one."""
    inactive = user_admin.create_user(
        db, org.id, "admin3", "admin3@example.com", password="x",
        role_id=roles["Administrator"].id, is_active=False,
    )["user"]
    assert inactive.is_active is False

    with pytest.raises(UserAdminError, match="only active administrator"):
        user_admin.set_active(db, admin, False)


def test_reset_password_returns_it_once(db, org, roles):
    from app.core.security import verify_password

    user = user_admin.create_user(
        db, org.id, "resetme", "reset@example.com", password="original",
        role_id=roles["Viewer"].id,
    )["user"]

    new_password = user_admin.reset_password(db, user)

    assert new_password
    assert verify_password(new_password, user.hashed_password)
    assert user.must_change_password is True


def test_change_own_password_verifies_the_current_one(db, org, roles):
    from app.core.security import verify_password

    user = user_admin.create_user(
        db, org.id, "selfserve", "self@example.com", password="oldpassword",
        role_id=roles["Viewer"].id,
    )["user"]

    with pytest.raises(UserAdminError, match="current password is incorrect"):
        user_admin.change_own_password(db, user, "wrong", "newpassword123")

    with pytest.raises(UserAdminError, match="at least 8 characters"):
        user_admin.change_own_password(db, user, "oldpassword", "short")

    with pytest.raises(UserAdminError, match="must differ"):
        user_admin.change_own_password(db, user, "oldpassword", "oldpassword")

    user_admin.change_own_password(db, user, "oldpassword", "newpassword123")
    assert verify_password("newpassword123", user.hashed_password)
    assert user.must_change_password is False


# --------------------------------------------------------------------------
# Settings: retention and schedule
# --------------------------------------------------------------------------


def test_settings_created_on_first_use(db, org):
    settings = settings_service.get_or_create(db, org.id)
    assert settings.retention_days == 90
    assert settings.default_schedule_cron == "0 2 * * *"

    again = settings_service.get_or_create(db, org.id)
    assert again.id == settings.id


def test_retention_validation():
    settings_service.validate_retention(30, 10)

    with pytest.raises(settings_service.SettingsError, match="at least 1 day"):
        settings_service.validate_retention(0, None)
    with pytest.raises(settings_service.SettingsError, match="10 years"):
        settings_service.validate_retention(99999, None)
    with pytest.raises(settings_service.SettingsError, match="at least 1"):
        settings_service.validate_retention(30, 0)


def test_cron_validation():
    settings_service.validate_cron("0 2 * * *")
    with pytest.raises(settings_service.SettingsError, match="not a valid cron"):
        settings_service.validate_cron("every tuesday please")


# --------------------------------------------------------------------------
# Maintenance windows
# --------------------------------------------------------------------------


def _settings_with(db, org, windows, tz="UTC"):
    settings = settings_service.get_or_create(db, org.id)
    settings.maintenance_windows = settings_service.validate_windows(windows)
    settings.maintenance_timezone = tz
    db.commit()
    return settings


def test_simple_window_within_a_day(db, org):
    # Wednesday 01:00-05:00
    settings = _settings_with(db, org, [{"name": "Nightly", "days": [2],
                                         "start": "01:00", "end": "05:00"}])

    # 2025-01-01 was a Wednesday.
    inside = datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    outside = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
    wrong_day = datetime(2025, 1, 2, 2, 0, tzinfo=timezone.utc)

    assert settings_service.backups_suppressed(settings, inside)[0] is True
    assert settings_service.backups_suppressed(settings, outside)[0] is False
    assert settings_service.backups_suppressed(settings, wrong_day)[0] is False


def test_window_wrapping_midnight(db, org):
    """22:00-02:00 on Monday must cover Tuesday 01:00."""
    settings = _settings_with(db, org, [{"name": "Change freeze", "days": [0],
                                         "start": "22:00", "end": "02:00"}])

    # 2024-12-30 was a Monday.
    monday_late = datetime(2024, 12, 30, 23, 0, tzinfo=timezone.utc)
    tuesday_early = datetime(2024, 12, 31, 1, 0, tzinfo=timezone.utc)
    tuesday_later = datetime(2024, 12, 31, 3, 0, tzinfo=timezone.utc)
    monday_early = datetime(2024, 12, 30, 1, 0, tzinfo=timezone.utc)

    assert settings_service.backups_suppressed(settings, monday_late)[0] is True
    assert settings_service.backups_suppressed(settings, tuesday_early)[0] is True
    assert settings_service.backups_suppressed(settings, tuesday_later)[0] is False
    # Monday 01:00 belongs to a Sunday-night window, which is not configured.
    assert settings_service.backups_suppressed(settings, monday_early)[0] is False


def test_window_is_evaluated_in_the_organization_timezone(db, org):
    """"No backups 22:00-23:00" means local time, not the server's."""
    settings = _settings_with(
        db, org,
        [{"name": "Local evening", "days": [2], "start": "22:00", "end": "23:00"}],
        tz="America/New_York",
    )

    # 2025-01-02 03:30 UTC is 2025-01-01 22:30 in New York, a Wednesday.
    moment = datetime(2025, 1, 2, 3, 30, tzinfo=timezone.utc)
    suppressed, name = settings_service.backups_suppressed(settings, moment)

    assert suppressed is True
    assert name == "Local evening"

    # The same wall-clock hour in UTC is outside the window.
    assert settings_service.backups_suppressed(
        settings, datetime(2025, 1, 1, 22, 30, tzinfo=timezone.utc)
    )[0] is False


def test_unknown_timezone_falls_back_to_utc(db, org):
    settings = _settings_with(
        db, org,
        [{"name": "W", "days": [2], "start": "01:00", "end": "05:00"}],
        tz="Mars/Olympus_Mons",
    )
    assert settings_service.backups_suppressed(
        settings, datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    )[0] is True


def test_notifications_suppressed_separately_from_backups(db, org):
    settings = _settings_with(db, org, [{
        "name": "Quiet hours", "days": [2], "start": "01:00", "end": "05:00",
        "suppress_backups": False, "suppress_notifications": True,
    }])

    moment = datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    assert settings_service.backups_suppressed(settings, moment)[0] is False
    assert settings_service.notifications_suppressed(settings, moment)[0] is True


def test_malformed_window_is_ignored_not_fatal(db, org):
    """A bad window must not suppress everything or break a scheduled run."""
    settings = settings_service.get_or_create(db, org.id)
    settings.maintenance_windows = [
        {"name": "Broken", "days": [], "start": "nonsense", "end": "x"},
        {"name": "Good", "days": [2], "start": "01:00", "end": "05:00"},
    ]
    db.commit()

    suppressed, name = settings_service.backups_suppressed(
        settings, datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    )
    assert suppressed is True
    assert name == "Good"


def test_window_validation_rejects_bad_input():
    with pytest.raises(settings_service.SettingsError, match="at least one day"):
        settings_service.validate_windows([{"name": "x", "days": [], "start": "01:00", "end": "02:00"}])

    with pytest.raises(settings_service.SettingsError, match="out of range"):
        settings_service.validate_windows([{"name": "x", "days": [9], "start": "01:00", "end": "02:00"}])

    with pytest.raises(settings_service.SettingsError, match="not a HH:MM"):
        settings_service.validate_windows([{"name": "x", "days": [1], "start": "banana", "end": "02:00"}])

    with pytest.raises(settings_service.SettingsError, match="zero-length"):
        settings_service.validate_windows([{"name": "x", "days": [1], "start": "02:00", "end": "02:00"}])


def test_next_window_start(db, org):
    settings = _settings_with(db, org, [{"name": "Sunday", "days": [6],
                                         "start": "23:00", "end": "23:59"}])

    # 2025-01-01 is a Wednesday; the next Sunday is the 5th.
    following = settings_service.next_window_start(
        settings, after=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    )
    assert following.date().isoformat() == "2025-01-05"
    assert following.hour == 23


def test_no_windows_means_nothing_suppressed(db, org):
    settings = settings_service.get_or_create(db, org.id)
    assert settings_service.backups_suppressed(settings)[0] is False
    assert settings_service.next_window_start(settings) is None


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------


def test_email_reports_missing_configuration_rather_than_raising(db, org):
    settings = settings_service.get_or_create(db, org.id)

    outcome = settings_service.send_email(settings, "Subject", "Body")
    assert outcome["success"] is False
    assert "SMTP" in outcome["message"]

    settings.smtp_host = "localhost"
    settings.smtp_from_address = "netconfig@example.com"
    db.commit()

    outcome = settings_service.send_email(settings, "Subject", "Body")
    assert outcome["success"] is False
    assert "recipients" in outcome["message"]


def test_notify_respects_the_event_switches(db, org):
    settings = settings_service.get_or_create(db, org.id)
    settings.notifications_enabled = False
    db.commit()

    outcome = settings_service.notify(db, org.id, "backup_failure", "s", "b")
    assert outcome["success"] is False
    assert "disabled" in outcome["message"]

    settings.notifications_enabled = True
    settings.notify_on_backup_success = False
    db.commit()

    outcome = settings_service.notify(db, org.id, "backup_success", "s", "b")
    assert "not enabled" in outcome["message"]


def test_notify_is_held_during_a_quiet_window(db, org):
    settings = settings_service.get_or_create(db, org.id)
    settings.notifications_enabled = True
    settings.notify_on_backup_failure = True
    settings.smtp_host = "localhost"
    settings.smtp_from_address = "n@example.com"
    settings.notify_recipients = ["ops@example.com"]
    # A window covering every day, all day.
    settings.maintenance_windows = settings_service.validate_windows([{
        "name": "Always quiet", "days": [0, 1, 2, 3, 4, 5, 6],
        "start": "00:00", "end": "23:59", "suppress_notifications": True,
    }])
    db.commit()

    outcome = settings_service.notify(db, org.id, "backup_failure", "s", "b")
    assert outcome["success"] is False
    assert "maintenance window" in outcome["message"]


def test_smtp_password_round_trips(db, org):
    from app.utils.encryption import encryption_service

    settings = settings_service.get_or_create(db, org.id)
    settings.smtp_password_encrypted = encryption_service.encrypt("mailpass")
    db.commit()

    assert settings_service.smtp_password(settings) == "mailpass"
