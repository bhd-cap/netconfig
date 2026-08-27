"""
Per-job device filtering, against a real database.

`BackupJob.device_filter` was stored and ignored: every scheduled job backed
up every active device in the organization. These cover the filter's semantics
(what each criterion selects, how they combine), its validation, and that the
scheduled task actually honours it.
"""
import pytest
from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models import BackupJob, Device, Organization
from app.services import device_filter
from app.services.device_filter import FilterError
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

    organization = Organization(name="FilterTest", is_active=True)
    db.add(organization)
    db.commit()
    return organization


def make_device(db, org, hostname, **kwargs):
    device = Device(
        organization_id=org.id,
        hostname=hostname,
        ip_address=kwargs.pop("ip_address", f"10.9.0.{abs(hash(hostname)) % 250 + 1}"),
        device_type=kwargs.pop("device_type", "cisco_ios"),
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
        **kwargs,
    )
    db.add(device)
    db.commit()
    return device


@pytest.fixture
def fleet(db, org):
    """A small mixed fleet, enough for every criterion to discriminate"""
    return {
        "core-nyc-01": make_device(
            db, org, "core-nyc-01", device_type="cisco_ios",
            location="NYC", tags={"role": "core", "env": "prod"},
        ),
        "core-lon-01": make_device(
            db, org, "core-lon-01", device_type="arista_eos",
            location="LON", tags={"role": "core", "env": "prod"},
        ),
        "access-nyc-01": make_device(
            db, org, "access-nyc-01", device_type="hp_procurve",
            location="NYC", tags={"role": "access", "env": "prod"},
            transport="telnet", port=23,
        ),
        "lab-01": make_device(
            db, org, "lab-01", device_type="juniper_junos",
            location="LAB", tags={"role": "access", "env": "lab"},
        ),
        "retired-01": make_device(
            db, org, "retired-01", device_type="cisco_ios",
            location="NYC", is_active=False,
        ),
        "sensor-01": make_device(
            db, org, "sensor-01", device_type="fortinet",
            location="NYC", transport="snmp", snmp_version="2c",
        ),
    }


def hostnames(db, org, device_filter_dict):
    """Resolve a filter to the hostnames it selects, sorted"""
    ids = device_filter.resolve(db, org.id, device_filter_dict)
    rows = db.scalars(select(Device.hostname).where(Device.id.in_(ids))).all()
    return sorted(rows)


def read_next_run(job_id):
    """
    Read a job's next_run_at through its own session

    scheduled_backup_task closes the session it was given in a finally block,
    which detaches anything the test is holding, so the assertion has to fetch
    the row again rather than refresh a stale instance.
    """
    session = SessionLocal()
    try:
        return session.scalar(
            select(BackupJob.next_run_at).where(BackupJob.id == job_id)
        )
    finally:
        session.close()


# --------------------------------------------------------------------------
# Default behaviour
# --------------------------------------------------------------------------


def test_an_empty_filter_covers_every_backable_device(db, org, fleet):
    """
    A job created before filtering existed has no filter

    It must keep meaning "everything", so upgrading does not silently narrow
    what already runs every night.
    """
    for empty in (None, {}, {"device_ids": [], "tags": {}}):
        assert hostnames(db, org, empty) == [
            "access-nyc-01",
            "core-lon-01",
            "core-nyc-01",
            "lab-01",
        ]


def test_snmp_devices_are_excluded_by_default(db, org, fleet):
    """
    SNMP cannot retrieve a configuration

    Including such a device in a scheduled backup guarantees a failure on
    every run, and a failure notification with it.
    """
    assert "sensor-01" not in hostnames(db, org, None)
    assert "sensor-01" in hostnames(db, org, {"include_snmp": True})
    assert hostnames(db, org, {"transports": ["snmp"]}) == ["sensor-01"]


def test_inactive_devices_are_excluded_by_default(db, org, fleet):
    assert "retired-01" not in hostnames(db, org, None)
    assert "retired-01" in hostnames(db, org, {"include_inactive": True})


def test_is_empty_recognises_every_no_op_form(db):
    assert device_filter.is_empty(None)
    assert device_filter.is_empty({})
    assert device_filter.is_empty({"device_ids": [], "locations": [], "tags": {}})
    # A flag alone does not constrain which devices are eligible.
    assert device_filter.is_empty({"include_snmp": True})
    assert not device_filter.is_empty({"locations": ["NYC"]})


# --------------------------------------------------------------------------
# Individual criteria
# --------------------------------------------------------------------------


def test_named_device_ids(db, org, fleet):
    selected = {fleet["core-nyc-01"].id, fleet["lab-01"].id}
    assert hostnames(db, org, {"device_ids": sorted(selected)}) == [
        "core-nyc-01",
        "lab-01",
    ]


def test_excluded_device_ids(db, org, fleet):
    result = hostnames(db, org, {"exclude_device_ids": [fleet["lab-01"].id]})
    assert "lab-01" not in result
    assert "core-nyc-01" in result


def test_device_types(db, org, fleet):
    assert hostnames(db, org, {"device_types": ["arista_eos", "juniper_junos"]}) == [
        "core-lon-01",
        "lab-01",
    ]


def test_locations(db, org, fleet):
    assert hostnames(db, org, {"locations": ["NYC"]}) == [
        "access-nyc-01",
        "core-nyc-01",
    ]


def test_hostname_glob(db, org, fleet):
    assert hostnames(db, org, {"hostname_pattern": "core-*"}) == [
        "core-lon-01",
        "core-nyc-01",
    ]
    assert hostnames(db, org, {"hostname_pattern": "*-nyc-*"}) == [
        "access-nyc-01",
        "core-nyc-01",
    ]
    # '?' is a single character.
    assert hostnames(db, org, {"hostname_pattern": "lab-0?"}) == ["lab-01"]


def test_a_literal_percent_in_a_pattern_is_not_a_wildcard(db, org):
    """
    Operators write globs, so % and _ must be escaped

    Without escaping, 'a%' would match every hostname starting with 'a'
    rather than the one device actually named 'a%b'.
    """
    organization = Organization(name="PatternTest", is_active=True)
    db.add(organization)
    db.commit()

    make_device(db, organization, "a%b")
    make_device(db, organization, "alpha")
    make_device(db, organization, "a_c")
    make_device(db, organization, "abc")

    assert hostnames(db, organization, {"hostname_pattern": "a%b"}) == ["a%b"]
    assert hostnames(db, organization, {"hostname_pattern": "a_c"}) == ["a_c"]
    assert hostnames(db, organization, {"hostname_pattern": "a?c"}) == ["a_c", "abc"]


def test_tags_require_every_pair(db, org, fleet):
    assert hostnames(db, org, {"tags": {"role": "core"}}) == [
        "core-lon-01",
        "core-nyc-01",
    ]
    assert hostnames(db, org, {"tags": {"role": "access", "env": "lab"}}) == ["lab-01"]
    assert hostnames(db, org, {"tags": {"role": "core", "env": "lab"}}) == []


def test_transports(db, org, fleet):
    assert hostnames(db, org, {"transports": ["telnet"]}) == ["access-nyc-01"]


# --------------------------------------------------------------------------
# Combining criteria
# --------------------------------------------------------------------------


def test_criteria_are_anded(db, org, fleet):
    assert hostnames(
        db, org, {"locations": ["NYC"], "tags": {"role": "core"}}
    ) == ["core-nyc-01"]

    assert hostnames(
        db,
        org,
        {"locations": ["NYC", "LON"], "device_types": ["arista_eos"]},
    ) == ["core-lon-01"]


def test_exclusion_wins_over_inclusion(db, org, fleet):
    both = {
        "device_ids": [fleet["core-nyc-01"].id, fleet["core-lon-01"].id],
        "exclude_device_ids": [fleet["core-lon-01"].id],
    }
    assert hostnames(db, org, both) == ["core-nyc-01"]


def test_a_filter_matching_nothing_returns_nothing(db, org, fleet):
    assert hostnames(db, org, {"locations": ["MARS"]}) == []


def test_filters_are_tenant_scoped(db, org, fleet):
    other = Organization(name="OtherFleet", is_active=True)
    db.add(other)
    db.commit()
    make_device(db, other, "their-core-01", location="NYC")

    # A wide-open filter still never crosses the organization boundary.
    assert "their-core-01" not in hostnames(db, org, None)
    assert hostnames(db, other, {"locations": ["NYC"]}) == ["their-core-01"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_unknown_keys_are_rejected(db):
    with pytest.raises(FilterError, match="Unknown filter key"):
        device_filter.validate({"tags.location": "NYC"})

    with pytest.raises(FilterError, match="Unknown filter key"):
        device_filter.validate({"hostnames": ["core-01"]})


def test_unknown_device_type_is_rejected(db):
    with pytest.raises(FilterError, match="Unknown device type"):
        device_filter.validate({"device_types": ["cisco_ios", "juniper_srx"]})


def test_unknown_transport_is_rejected(db):
    with pytest.raises(FilterError, match="Unknown transport"):
        device_filter.validate({"transports": ["carrier-pigeon"]})


def test_malformed_values_are_rejected(db):
    with pytest.raises(FilterError, match="must be a list"):
        device_filter.validate({"device_ids": 5})

    with pytest.raises(FilterError, match="only integers"):
        device_filter.validate({"device_ids": ["one"]})

    with pytest.raises(FilterError, match="invalid device id"):
        device_filter.validate({"device_ids": [0]})

    with pytest.raises(FilterError, match="must be an object"):
        device_filter.validate({"tags": ["role", "core"]})

    with pytest.raises(FilterError, match="must be a string"):
        device_filter.validate({"hostname_pattern": 42})

    with pytest.raises(FilterError, match="must be an object"):
        device_filter.validate("everything")


def test_validate_normalises_for_storage(db):
    normalised = device_filter.validate(
        {
            "device_ids": [3, 1, 3],
            "device_types": ["cisco_ios", "cisco_ios"],
            "locations": ["  NYC  ", ""],
            "hostname_pattern": "  core-*  ",
            "include_snmp": "yes",
        }
    )

    assert normalised == {
        "device_ids": [1, 3],
        "device_types": ["cisco_ios"],
        "locations": ["NYC"],
        "hostname_pattern": "core-*",
        "include_snmp": True,
    }


def test_a_nested_tag_value_is_rejected(db):
    with pytest.raises(FilterError, match="must be a string, number or boolean"):
        device_filter.validate({"tags": {"role": {"nested": "value"}}})


# --------------------------------------------------------------------------
# Counting, preview and description
# --------------------------------------------------------------------------


def test_count_matches_resolve(db, org, fleet):
    for candidate in (None, {"locations": ["NYC"]}, {"tags": {"role": "core"}}):
        assert device_filter.count(db, org.id, candidate) == len(
            device_filter.resolve(db, org.id, candidate)
        )


def test_preview_names_the_devices_and_summarises(db, org, fleet):
    result = device_filter.preview(db, org.id, {"locations": ["NYC"]})

    assert result["total"] == 2
    assert [entry["hostname"] for entry in result["devices"]] == [
        "access-nyc-01",
        "core-nyc-01",
    ]
    assert result["truncated"] is False
    assert "NYC" in result["summary"]


def test_preview_reports_truncation(db, org, fleet):
    result = device_filter.preview(db, org.id, None, limit=2)

    assert result["total"] == 4
    assert len(result["devices"]) == 2
    assert result["truncated"] is True


def test_describe_reads_as_a_sentence(db):
    assert device_filter.describe(None) == "Every device that can be backed up"
    assert device_filter.describe({}) == "Every device that can be backed up"

    summary = device_filter.describe(
        {"locations": ["NYC"], "tags": {"role": "core"}, "hostname_pattern": "core-*"}
    )
    assert "NYC" in summary
    assert "role=core" in summary
    assert "core-*" in summary


# --------------------------------------------------------------------------
# The scheduled task honours the filter
# --------------------------------------------------------------------------


def test_scheduled_job_backs_up_only_the_matching_devices(db, org, fleet, monkeypatch):
    """
    The whole point: a job's filter decides which devices it touches

    The retrieval is mocked at the retriever, since what matters here is the
    set of device IDs the task decides to hand it.
    """
    from datetime import datetime, timedelta, timezone
    from unittest import mock

    job = BackupJob(
        organization_id=org.id,
        name="NYC core only",
        schedule_cron="0 2 * * *",
        is_enabled=True,
        device_filter={"locations": ["NYC"], "tags": {"role": "core"}},
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(job)
    db.commit()

    from app.tasks import backup as backup_tasks

    captured = {}

    def fake_backup(device_ids, user_id=None, max_workers=None):
        captured["device_ids"] = list(device_ids)
        return {
            "total": len(device_ids),
            "successful": len(device_ids),
            "failed": 0,
            "unchanged": 0,
            "devices": [],
        }

    with mock.patch.object(backup_tasks, "SessionLocal", lambda: db), mock.patch(
        "app.services.config_retriever.ConfigurationRetriever.backup_multiple_devices",
        side_effect=fake_backup,
    ), mock.patch.object(backup_tasks, "queue_export"):
        result = backup_tasks.scheduled_backup_task(job.id)

    assert result["success"] is True
    assert captured["device_ids"] == [fleet["core-nyc-01"].id]


def test_a_job_matching_nothing_still_advances_its_next_run(db, org, fleet):
    """
    Otherwise the job comes due again on the very next check

    check_scheduled_jobs_task runs every 60 seconds and picks up anything
    whose next_run_at has passed, so a path that returns without advancing it
    re-fires every minute forever.
    """
    from datetime import datetime, timedelta, timezone
    from unittest import mock

    job = BackupJob(
        organization_id=org.id,
        name="Nothing matches",
        schedule_cron="0 2 * * *",
        is_enabled=True,
        device_filter={"locations": ["MARS"]},
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    job_id, was_due_at = job.id, job.next_run_at

    from app.tasks import backup as backup_tasks

    with mock.patch.object(backup_tasks, "SessionLocal", lambda: db):
        result = backup_tasks.scheduled_backup_task(job_id)

    assert result["devices_backed_up"] == 0
    assert result["next_run"] is not None
    assert read_next_run(job_id) > was_due_at


def test_an_invalid_stored_filter_fails_loudly(db, org, fleet):
    """
    A stored filter that no longer validates must not fall back to everything

    Backing up the whole fleet because a filter went bad is a silent widening
    of scope; refusing makes it visible and fixable.
    """
    from datetime import datetime, timedelta, timezone
    from unittest import mock

    job = BackupJob(
        organization_id=org.id,
        name="Broken filter",
        schedule_cron="0 2 * * *",
        is_enabled=True,
        device_filter={"device_types": ["a_type_that_was_removed"]},
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(job)
    db.commit()
    job_id, was_due_at = job.id, job.next_run_at

    from app.tasks import backup as backup_tasks

    with mock.patch.object(backup_tasks, "SessionLocal", lambda: db), mock.patch(
        "app.services.config_retriever.ConfigurationRetriever.backup_multiple_devices"
    ) as retriever:
        result = backup_tasks.scheduled_backup_task(job_id)

    assert result["success"] is False
    assert "Invalid device filter" in result["error"]
    retriever.assert_not_called()

    # And it still advances, so it does not spin every minute.
    assert read_next_run(job_id) > was_due_at
