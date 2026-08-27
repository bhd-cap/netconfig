"""
API-level tests for discovery, topology diagrams, host inventory and reports.

Celery is never reached: the endpoints that queue work are exercised with the
task's .delay patched out, so route wiring and permissions are covered without
needing a broker.
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Device, Organization
from app.models.administration import Role
from app.models.network import HostInventory, Neighbor, OuiVendor, TopologyDiagram
from app.services import user_admin
from app.services.oui import clear as clear_oui, import_entries, oui_lookup
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

    organization = Organization(name="ApiDiscoveryTest", is_active=True)
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
def operator(db, org, roles):
    return user_admin.create_user(
        db, org.id, "netop", "netop@example.com",
        password="netoppass1", role_id=roles["Operator"].id,
    )["user"]


@pytest.fixture
def viewer(db, org, roles):
    return user_admin.create_user(
        db, org.id, "netviewer", "netviewer@example.com",
        password="viewpass1", role_id=roles["Viewer"].id,
    )["user"]


@pytest.fixture
def switches(db, org):
    """Two managed switches with a link between them"""
    made = []
    for index, hostname in enumerate(("core-01", "access-01"), start=1):
        device = Device(
            organization_id=org.id,
            hostname=hostname,
            ip_address=f"10.0.0.{index}",
            device_type="cisco_ios",
            username="admin",
            encrypted_password=encryption_service.encrypt("secret"),
            is_active=True,
        )
        db.add(device)
        made.append(device)

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


def add_neighbor(db, org, device, remote_hostname, **kwargs):
    neighbor = Neighbor(
        organization_id=org.id,
        device_id=device.id,
        local_interface=kwargs.pop("local_interface", "Gi1/0/1"),
        remote_hostname=remote_hostname,
        remote_interface=kwargs.pop("remote_interface", "Gi1/0/2"),
        protocol=kwargs.pop("protocol", "lldp"),
        last_seen=kwargs.pop("last_seen", datetime.now(timezone.utc)),
        **kwargs,
    )
    db.add(neighbor)
    db.commit()
    return neighbor


def add_host(db, org, device, mac, **kwargs):
    host = HostInventory(
        organization_id=org.id,
        device_id=device.id,
        interface=kwargs.pop("interface", "Gi1/0/5"),
        mac_address=mac,
        vlan=kwargs.pop("vlan", 10),
        entry_type=kwargs.pop("entry_type", "dynamic"),
        first_seen=kwargs.pop("first_seen", datetime.now(timezone.utc)),
        last_seen=kwargs.pop("last_seen", datetime.now(timezone.utc)),
        **kwargs,
    )
    db.add(host)
    db.commit()
    return host


# --------------------------------------------------------------------------
# Discovery runs
# --------------------------------------------------------------------------


def test_discovery_run_is_queued(client, operator, switches):
    with mock.patch("app.tasks.discovery.discovery_crawl_task.delay") as delay:
        delay.return_value = mock.Mock(id="task-123")

        response = as_user(client, operator).post(
            "/api/v1/discovery/run",
            json={"seed_device_id": switches[0].id, "max_hops": 3, "auto_add": True},
        )

    assert response.status_code == 202
    assert response.json() == {
        "queued": True,
        "task_id": "task-123",
        "seed": "core-01",
        "message": "Discovery started from core-01",
    }

    kwargs = delay.call_args.kwargs
    assert kwargs["seed_device_id"] == switches[0].id
    assert kwargs["max_hops"] == 3
    assert kwargs["auto_add"] is True


def test_discovery_run_needs_a_real_seed(client, operator):
    response = as_user(client, operator).post(
        "/api/v1/discovery/run", json={"seed_device_id": 999}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Seed device not found"


def test_max_hops_is_bounded(client, operator, switches):
    response = as_user(client, operator).post(
        "/api/v1/discovery/run",
        json={"seed_device_id": switches[0].id, "max_hops": 99},
    )

    assert response.status_code == 422


def test_viewer_cannot_start_discovery(client, viewer, switches):
    response = as_user(client, viewer).post(
        "/api/v1/discovery/run", json={"seed_device_id": switches[0].id}
    )

    assert response.status_code == 403
    assert "discovery:run" in response.json()["detail"]


def test_synchronous_run_reports_the_crawl(client, operator, switches):
    from app.services.discovery import CrawlSummary

    summary = CrawlSummary(
        run_id=7, devices_probed=2, devices_failed=0, neighbors_found=3,
        hosts_found=11, devices_created=1, unmanaged=["edge-99"], errors=[],
    )

    with mock.patch(
        "app.services.discovery.DiscoveryService.crawl", return_value=summary
    ):
        response = as_user(client, operator).post(
            "/api/v1/discovery/run",
            json={"seed_device_id": switches[0].id, "run_async": False},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["queued"] is False
    assert body["run_id"] == 7
    assert body["hosts_found"] == 11
    assert body["unmanaged"] == ["edge-99"]


def test_discovery_runs_are_listed_newest_first(client, db, org, operator, switches):
    from app.models.network import DiscoveryRun

    base = datetime.now(timezone.utc)
    for offset in (2, 0, 1):
        db.add(
            DiscoveryRun(
                organization_id=org.id,
                seed_device_id=switches[0].id,
                status="completed",
                max_hops=2,
                started_at=base - timedelta(hours=offset),
            )
        )
    db.commit()

    runs = as_user(client, operator).get("/api/v1/discovery/runs").json()

    assert len(runs) == 3
    starts = [run["started_at"] for run in runs]
    assert starts == sorted(starts, reverse=True)


def test_a_run_from_another_organization_is_not_found(client, db, operator):
    from app.models.network import DiscoveryRun

    other = Organization(name="OtherDiscovery", is_active=True)
    db.add(other)
    db.commit()

    run = DiscoveryRun(organization_id=other.id, status="completed", max_hops=1)
    db.add(run)
    db.commit()

    assert as_user(client, operator).get(
        f"/api/v1/discovery/runs/{run.id}"
    ).status_code == 404


# --------------------------------------------------------------------------
# Neighbours
# --------------------------------------------------------------------------


def test_neighbors_are_listed_with_the_reporting_device(
    client, db, org, operator, switches
):
    add_neighbor(db, org, switches[0], "access-01", remote_device_id=switches[1].id)
    add_neighbor(db, org, switches[1], "printer-lobby", protocol="cdp",
                 local_interface="Gi1/0/9")

    rows = as_user(client, operator).get("/api/v1/discovery/neighbors").json()

    assert len(rows) == 2
    by_remote = {row["remote_hostname"]: row for row in rows}
    assert by_remote["access-01"]["device_hostname"] == "core-01"
    assert by_remote["access-01"]["remote_device_id"] == switches[1].id
    assert by_remote["printer-lobby"]["device_hostname"] == "access-01"


def test_neighbors_filter_by_device_and_protocol(client, db, org, operator, switches):
    add_neighbor(db, org, switches[0], "access-01", protocol="lldp")
    add_neighbor(db, org, switches[0], "ap-3", protocol="cdp",
                 local_interface="Gi1/0/7")

    session = as_user(client, operator)

    only_cdp = session.get(
        "/api/v1/discovery/neighbors", params={"protocol": "cdp"}
    ).json()
    assert [row["remote_hostname"] for row in only_cdp] == ["ap-3"]

    other_switch = session.get(
        "/api/v1/discovery/neighbors", params={"device_id": switches[1].id}
    ).json()
    assert other_switch == []

    bad_protocol = session.get(
        "/api/v1/discovery/neighbors", params={"protocol": "eigrp"}
    )
    assert bad_protocol.status_code == 422


def test_inactive_neighbors_are_hidden_unless_asked_for(
    client, db, org, operator, switches
):
    stale = add_neighbor(db, org, switches[0], "gone-switch")
    stale.is_active = False
    db.commit()

    session = as_user(client, operator)

    assert session.get("/api/v1/discovery/neighbors").json() == []

    everything = session.get(
        "/api/v1/discovery/neighbors", params={"active_only": False}
    ).json()
    assert [row["remote_hostname"] for row in everything] == ["gone-switch"]


def test_delete_a_neighbor(client, db, org, operator, switches):
    neighbor = add_neighbor(db, org, switches[0], "decommissioned")

    session = as_user(client, operator)
    assert session.delete(f"/api/v1/discovery/neighbors/{neighbor.id}").status_code == 200
    assert session.get("/api/v1/discovery/neighbors").json() == []
    assert session.delete(f"/api/v1/discovery/neighbors/{neighbor.id}").status_code == 404


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------


def test_topology_graph_joins_managed_devices(client, db, org, operator, switches):
    add_neighbor(db, org, switches[0], "access-01", remote_device_id=switches[1].id)

    graph = as_user(client, operator).get("/api/v1/discovery/topology").json()

    keys = {node["key"] for node in graph["nodes"]}
    assert keys == {f"device:{switches[0].id}", f"device:{switches[1].id}"}
    assert graph["stats"]["managed_nodes"] == 2
    assert graph["stats"]["links"] == 1
    assert graph["links"][0]["source_interface"] == "Gi1/0/1"


def test_both_ends_of_a_link_collapse_into_one_edge(
    client, db, org, operator, switches
):
    add_neighbor(
        db, org, switches[0], "access-01",
        local_interface="Gi1/0/1", remote_interface="Gi1/0/2",
        remote_device_id=switches[1].id,
    )
    add_neighbor(
        db, org, switches[1], "core-01",
        local_interface="Gi1/0/2", remote_interface="Gi1/0/1",
        remote_device_id=switches[0].id,
    )

    graph = as_user(client, operator).get("/api/v1/discovery/topology").json()

    assert graph["stats"]["links"] == 1
    assert graph["links"][0]["confirmed_both_ends"] is True


def test_unmanaged_neighbours_can_be_excluded(client, db, org, operator, switches):
    add_neighbor(db, org, switches[0], "unmanaged-sw", remote_mgmt_ip="10.9.9.9")

    session = as_user(client, operator)

    included = session.get("/api/v1/discovery/topology").json()
    assert included["stats"]["unmanaged_nodes"] == 1
    unmanaged = next(n for n in included["nodes"] if not n["managed"])
    assert unmanaged["label"] == "unmanaged-sw"
    assert unmanaged["ip_address"] == "10.9.9.9"

    excluded = session.get(
        "/api/v1/discovery/topology", params={"include_unmanaged": False}
    ).json()
    assert excluded["stats"]["unmanaged_nodes"] == 0
    assert excluded["stats"]["links"] == 0


def test_a_saved_layout_is_applied_over_a_fresh_graph(
    client, db, org, operator, switches
):
    add_neighbor(db, org, switches[0], "access-01", remote_device_id=switches[1].id)

    session = as_user(client, operator)

    created = session.post(
        "/api/v1/discovery/diagrams",
        json={
            "name": "Ground floor",
            "layout": {
                "nodes": {
                    f"device:{switches[0].id}": {
                        "x": 120, "y": 40, "label": "Core (renamed)"
                    },
                    "device:99999": {"x": 1, "y": 1},
                },
                "links": [
                    {
                        "source": f"device:{switches[0].id}",
                        "target": f"device:{switches[1].id}",
                        "source_interface": "Te1/1/1",
                        "target_interface": "Te1/1/2",
                        "label": "dark fibre",
                    }
                ],
            },
            "is_default": True,
        },
    )
    assert created.status_code == 201
    diagram_id = created.json()["id"]

    graph = session.get(
        "/api/v1/discovery/topology", params={"diagram_id": diagram_id}
    ).json()

    core = next(n for n in graph["nodes"] if n["key"] == f"device:{switches[0].id}")
    assert core["x"] == 120
    assert core["label"] == "Core (renamed)"

    # A discovered link plus the hand-drawn one; the edit for a node that does
    # not exist is ignored rather than creating a phantom.
    assert graph["stats"]["links"] == 2
    assert graph["stats"]["manual_links"] == 1
    assert graph["diagram"]["name"] == "Ground floor"
    assert all(node["key"] != "device:99999" for node in graph["nodes"])


def test_topology_with_an_unknown_diagram_is_404(client, operator):
    response = as_user(client, operator).get(
        "/api/v1/discovery/topology", params={"diagram_id": 4242}
    )

    assert response.status_code == 404


def test_diagram_crud_and_default_switching(client, db, org, operator):
    session = as_user(client, operator)

    first = session.post(
        "/api/v1/discovery/diagrams",
        json={"name": "First", "is_default": True},
    ).json()

    second = session.post(
        "/api/v1/discovery/diagrams",
        json={"name": "Second", "is_default": True},
    ).json()

    listed = session.get("/api/v1/discovery/diagrams").json()
    defaults = {entry["name"]: entry["is_default"] for entry in listed}
    assert defaults == {"First": False, "Second": True}

    renamed = session.put(
        f"/api/v1/discovery/diagrams/{first['id']}",
        json={"name": "Renamed", "description": "notes"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"

    clash = session.put(
        f"/api/v1/discovery/diagrams/{first['id']}", json={"name": "Second"}
    )
    assert clash.status_code == 409

    assert session.delete(
        f"/api/v1/discovery/diagrams/{second['id']}"
    ).status_code == 200
    assert len(session.get("/api/v1/discovery/diagrams").json()) == 1


def test_duplicate_diagram_name_conflicts(client, operator):
    session = as_user(client, operator)

    assert session.post(
        "/api/v1/discovery/diagrams", json={"name": "Only"}
    ).status_code == 201
    assert session.post(
        "/api/v1/discovery/diagrams", json={"name": "Only"}
    ).status_code == 409


def test_viewer_cannot_save_a_diagram(client, viewer):
    response = as_user(client, viewer).post(
        "/api/v1/discovery/diagrams", json={"name": "Nope"}
    )

    assert response.status_code == 403


def test_a_diagram_from_another_organization_is_not_found(client, db, operator):
    other = Organization(name="OtherDiagrams", is_active=True)
    db.add(other)
    db.commit()

    diagram = TopologyDiagram(
        organization_id=other.id, name="Theirs", layout={}, is_default=False
    )
    db.add(diagram)
    db.commit()

    assert as_user(client, operator).get(
        f"/api/v1/discovery/diagrams/{diagram.id}"
    ).status_code == 404


# --------------------------------------------------------------------------
# Host inventory
# --------------------------------------------------------------------------


def test_inventory_lists_hosts_with_their_switch(client, db, org, operator, switches):
    add_host(db, org, switches[0], "aa:bb:cc:11:22:33", vendor="Acme",
             ip_address="10.0.5.20")

    body = as_user(client, operator).get("/api/v1/inventory").json()

    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["mac_address"] == "aa:bb:cc:11:22:33"
    assert entry["device_hostname"] == "core-01"
    assert entry["vendor"] == "Acme"
    assert entry["ip_address"] == "10.0.5.20"


def test_inventory_filters(client, db, org, operator, switches):
    add_host(db, org, switches[0], "aa:aa:aa:00:00:01", vendor="Acme", vlan=10,
             interface="Gi1/0/1", ip_address="10.0.5.1")
    add_host(db, org, switches[1], "bb:bb:bb:00:00:02", vendor="Globex", vlan=20,
             interface="Gi1/0/2", ip_address="10.0.5.2")

    session = as_user(client, operator)

    assert session.get(
        "/api/v1/inventory", params={"device_id": switches[1].id}
    ).json()["total"] == 1
    assert session.get("/api/v1/inventory", params={"vlan": 10}).json()["total"] == 1
    assert session.get(
        "/api/v1/inventory", params={"vendor": "globe"}
    ).json()["total"] == 1
    assert session.get(
        "/api/v1/inventory", params={"interface": "Gi1/0/2"}
    ).json()["total"] == 1
    assert session.get(
        "/api/v1/inventory", params={"search": "10.0.5.2"}
    ).json()["total"] == 1
    assert session.get(
        "/api/v1/inventory", params={"search": "aa:aa"}
    ).json()["total"] == 1


def test_inventory_hides_aged_out_rows_by_default(client, db, org, operator, switches):
    old = add_host(db, org, switches[0], "cc:cc:cc:00:00:03")
    old.is_active = False
    old.last_seen = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()

    session = as_user(client, operator)

    assert session.get("/api/v1/inventory").json()["total"] == 0
    assert session.get(
        "/api/v1/inventory", params={"active_only": False}
    ).json()["total"] == 1


def test_seen_within_hours_narrows_to_recent_sightings(
    client, db, org, operator, switches
):
    add_host(db, org, switches[0], "dd:dd:dd:00:00:04")
    add_host(
        db, org, switches[0], "ee:ee:ee:00:00:05",
        interface="Gi1/0/6",
        last_seen=datetime.now(timezone.utc) - timedelta(hours=48),
    )

    body = as_user(client, operator).get(
        "/api/v1/inventory", params={"seen_within_hours": 6}
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["mac_address"] == "dd:dd:dd:00:00:04"


def test_inventory_paginates(client, db, org, operator, switches):
    for index in range(7):
        add_host(
            db, org, switches[0], f"aa:00:00:00:00:{index:02x}",
            interface=f"Gi1/0/{index}",
        )

    body = as_user(client, operator).get(
        "/api/v1/inventory", params={"limit": 3, "skip": 3}
    ).json()

    assert body["total"] == 7
    assert body["page"] == 2
    assert body["total_pages"] == 3
    assert len(body["items"]) == 3


def test_annotate_an_inventory_entry(client, db, org, operator, switches):
    host = add_host(db, org, switches[0], "ff:ff:ff:00:00:06")

    response = as_user(client, operator).patch(
        f"/api/v1/inventory/{host.id}",
        json={"hostname": "lab-printer", "notes": "Ground floor copier"},
    )

    assert response.status_code == 200
    assert response.json()["hostname"] == "lab-printer"
    assert response.json()["notes"] == "Ground floor copier"

    db.refresh(host)
    assert host.hostname == "lab-printer"


def test_annotating_a_missing_entry_is_404(client, operator):
    response = as_user(client, operator).patch(
        "/api/v1/inventory/98765", json={"notes": "x"}
    )

    assert response.status_code == 404


def test_inventory_refresh_is_queued(client, operator, switches):
    with mock.patch("app.tasks.discovery.refresh_inventory_task.delay") as delay:
        delay.return_value = mock.Mock(id="refresh-1")

        response = as_user(client, operator).post(
            "/api/v1/inventory/refresh", json={"device_ids": [switches[0].id]}
        )

    assert response.status_code == 202
    assert response.json() == {"queued": True, "task_id": "refresh-1"}
    assert delay.call_args.kwargs["device_ids"] == [switches[0].id]


def test_refresh_defaults_to_every_device(client, operator):
    with mock.patch("app.tasks.discovery.refresh_inventory_task.delay") as delay:
        delay.return_value = mock.Mock(id="refresh-2")

        response = as_user(client, operator).post("/api/v1/inventory/refresh")

    assert response.status_code == 202
    assert delay.call_args.kwargs["device_ids"] is None


def test_viewer_cannot_annotate_or_refresh(client, db, org, viewer, switches):
    host = add_host(db, org, switches[0], "11:11:11:00:00:07")
    session = as_user(client, viewer)

    assert session.patch(
        f"/api/v1/inventory/{host.id}", json={"notes": "no"}
    ).status_code == 403
    assert session.post("/api/v1/inventory/refresh").status_code == 403


def test_an_entry_from_another_organization_is_not_found(client, db, operator):
    other = Organization(name="OtherInventory", is_active=True)
    db.add(other)
    db.commit()

    device = Device(
        organization_id=other.id,
        hostname="their-switch",
        ip_address="192.168.50.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(device)
    db.commit()

    host = HostInventory(
        organization_id=other.id,
        device_id=device.id,
        interface="Gi0/1",
        mac_address="22:22:22:00:00:08",
        vlan=1,
    )
    db.add(host)
    db.commit()

    session = as_user(client, operator)
    assert session.get("/api/v1/inventory").json()["total"] == 0
    assert session.patch(
        f"/api/v1/inventory/{host.id}", json={"notes": "x"}
    ).status_code == 404


# --------------------------------------------------------------------------
# OUI vendor data
# --------------------------------------------------------------------------


def test_oui_status_reports_what_is_loaded(client, db, operator):
    clear_oui(db)
    import_entries(db, [("001122", "Test Vendor A"), ("334455", "Test Vendor B")])

    body = as_user(client, operator).get("/api/v1/inventory/oui/status").json()

    assert body["prefixes"] == 2
    assert "bundled" in body["sources"]
    assert body["ieee_url"].startswith("http")


def test_importing_the_bundled_set_populates_the_table(client, db, org, roles):
    admin = user_admin.create_user(
        db, org.id, "ouiadmin", "ouiadmin@example.com",
        password="ouipass1", role_id=roles["Administrator"].id,
    )["user"]

    clear_oui(db)

    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/import", json={"source": "bundled"}
    )

    assert response.status_code == 200
    assert response.json()["imported"] > 0
    assert db.scalar(select(OuiVendor.vendor_name).limit(1))


def test_oui_import_needs_settings_write(client, operator):
    response = as_user(client, operator).post(
        "/api/v1/inventory/oui/import", json={"source": "bundled"}
    )

    assert response.status_code == 403
    assert "settings:write" in response.json()["detail"]


def test_url_import_requires_a_url(client, db, org, roles):
    admin = user_admin.create_user(
        db, org.id, "urladmin", "urladmin@example.com",
        password="urlpass1", role_id=roles["Administrator"].id,
    )["user"]

    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/import", json={"source": "url"}
    )

    assert response.status_code == 400
    assert "url is required" in response.json()["detail"]


def test_an_unknown_source_is_rejected(client, db, org, roles):
    admin = user_admin.create_user(
        db, org.id, "srcadmin", "srcadmin@example.com",
        password="srcpass1", role_id=roles["Administrator"].id,
    )["user"]

    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/import", json={"source": "carrier-pigeon"}
    )

    assert response.status_code == 422


def test_prefixes_are_stored_lowercase_however_they_arrive(client, db, operator):
    """
    The IEEE registry writes prefixes in uppercase and lookups normalise to
    lowercase, so an un-normalised row would never match anything.
    """
    clear_oui(db)
    import_entries(db, [("00-1B-2C", "Dashed Upper"), ("AA:BB:CC", "Colon Upper")])

    stored = dict(db.execute(select(OuiVendor.oui, OuiVendor.vendor_name)).all())
    assert stored == {"001b2c": "Dashed Upper", "aabbcc": "Colon Upper"}

    oui_lookup.load(db, force=True)
    assert oui_lookup.lookup("00:1b:2c:99:88:77") == "Dashed Upper"


def test_a_short_prefix_is_dropped_rather_than_stored(db):
    clear_oui(db)
    written = import_entries(db, [("00AA", "Too short"), ("001122", "Fine")])

    assert written == 1
    assert db.scalar(select(func.count(OuiVendor.oui))) == 1


def test_oui_upload_imports_a_manuf_file(client, db, org, roles):
    """
    The escape hatch for an install with no outbound internet access

    A browser cannot hand the server a local path, which made the 'file'
    source unusable from the UI - so "Import IEEE" was the only button, and it
    fails on any network that cannot reach IEEE.
    """
    admin = user_admin.create_user(
        db, org.id, "uploadadmin", "uploadadmin@example.com",
        password="uploadpass1", role_id=roles["Administrator"].id,
    )["user"]

    clear_oui(db)

    content = b"F0D5BF Intel Corporate\n001B2C Atron electronic GmbH\n"
    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/upload",
        files={"file": ("nmap-mac-prefixes", content, "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 2

    stored = dict(db.execute(select(OuiVendor.oui, OuiVendor.vendor_name)).all())
    # The whole vendor name, not just its last word.
    assert stored["f0d5bf"] == "Intel Corporate"


def test_oui_upload_rejects_an_empty_file(client, db, org, roles):
    admin = user_admin.create_user(
        db, org.id, "emptyadmin", "emptyadmin@example.com",
        password="emptypass1", role_id=roles["Administrator"].id,
    )["user"]

    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/upload",
        files={"file": ("empty.csv", b"", "text/plain")},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_oui_upload_rejects_a_file_it_cannot_parse(client, db, org, roles):
    """
    An HTML error page must not become 39,000 rows of nonsense
    """
    admin = user_admin.create_user(
        db, org.id, "junkadmin", "junkadmin@example.com",
        password="junkpass1", role_id=roles["Administrator"].id,
    )["user"]

    response = as_user(client, admin).post(
        "/api/v1/inventory/oui/upload",
        files={"file": ("403.html", b"<html><body>Forbidden</body></html>", "text/html")},
    )

    assert response.status_code == 400
    assert "No usable OUI entries" in response.json()["detail"]


def test_oui_upload_needs_settings_write(client, operator):
    response = as_user(client, operator).post(
        "/api/v1/inventory/oui/upload",
        files={"file": ("x.csv", b"001122 Acme\n", "text/plain")},
    )

    assert response.status_code == 403


def test_a_failed_ieee_import_says_what_it_tried(client, db, org, roles):
    """
    The old handler let anything that was not a RuntimeError become a bare 500

    An operator got "500 Internal Server Error" with nothing to act on. Now
    the response names each source and suggests the upload route.
    """
    from unittest import mock

    admin = user_admin.create_user(
        db, org.id, "ieeeadmin", "ieeeadmin@example.com",
        password="ieeepass1", role_id=roles["Administrator"].id,
    )["user"]

    with mock.patch(
        "app.services.oui.import_from_ieee",
        side_effect=RuntimeError(
            "Could not fetch the OUI registry from any source. Tried:\n  "
            "https://standards-oui.ieee.org/oui/oui.csv: 403 Forbidden"
        ),
    ):
        response = as_user(client, admin).post(
            "/api/v1/inventory/oui/import", json={"source": "ieee"}
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "403 Forbidden" in detail
    assert "standards-oui.ieee.org" in detail


def test_a_write_failure_is_reported_not_swallowed(client, db, org, roles):
    """
    A database error mid-import used to surface as an opaque 500

    It still returns 500 - it is a server fault - but names the exception and
    points at the log, which is the difference between diagnosable and not.
    """
    from unittest import mock

    admin = user_admin.create_user(
        db, org.id, "writeadmin", "writeadmin@example.com",
        password="writepass1", role_id=roles["Administrator"].id,
    )["user"]

    with mock.patch(
        "app.services.oui.import_bundled",
        side_effect=ValueError("column overflowed"),
    ):
        response = as_user(client, admin).post(
            "/api/v1/inventory/oui/import", json={"source": "bundled"}
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "ValueError" in detail
    assert "column overflowed" in detail
    assert "journalctl" in detail


def test_backfill_resolves_vendors_for_existing_rows(
    client, db, org, operator, switches
):
    clear_oui(db)
    add_host(db, org, switches[0], "00:1a:2b:33:44:55")

    import_entries(db, [("001A2B", "Backfilled Vendor")])
    oui_lookup.load(db, force=True)

    response = as_user(client, operator).post("/api/v1/inventory/oui/backfill")

    assert response.status_code == 200
    assert response.json()["updated"] == 1

    vendor = db.scalar(
        select(HostInventory.vendor).where(
            HostInventory.mac_address == "00:1a:2b:33:44:55"
        )
    )
    assert vendor == "Backfilled Vendor"


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def test_summary_counts(client, db, org, operator, switches):
    add_host(db, org, switches[0], "aa:00:00:00:01:01", vendor="Acme",
             ip_address="10.0.1.1")
    add_host(db, org, switches[1], "aa:00:00:00:01:02", interface="Gi1/0/8")

    stale = add_host(db, org, switches[0], "aa:00:00:00:01:03", interface="Gi1/0/9",
                     last_seen=datetime.now(timezone.utc) - timedelta(days=10),
                     first_seen=datetime.now(timezone.utc) - timedelta(days=40))
    stale.is_active = False
    db.commit()

    body = as_user(client, operator).get("/api/v1/inventory/reports/summary").json()

    assert body["total_entries"] == 3
    assert body["active_entries"] == 2
    assert body["unique_macs"] == 3
    assert body["switches_reporting"] == 2
    assert body["seen_last_24h"] == 2
    assert body["new_last_24h"] == 2
    assert body["with_ip_address"] == 1
    assert body["unknown_vendor"] == 2


def test_by_vendor_groups_and_labels_unknowns(client, db, org, operator, switches):
    add_host(db, org, switches[0], "aa:00:00:00:02:01", vendor="Acme")
    add_host(db, org, switches[0], "aa:00:00:00:02:02", vendor="Acme",
             interface="Gi1/0/2")
    add_host(db, org, switches[0], "aa:00:00:00:02:03", interface="Gi1/0/3")

    body = as_user(client, operator).get(
        "/api/v1/inventory/reports/by-vendor"
    ).json()

    counts = {entry["vendor"]: entry["hosts"] for entry in body["vendors"]}
    assert counts == {"Acme": 2, "Unknown": 1}
    assert body["vendors"][0]["vendor"] == "Acme"


def test_by_port_flags_a_likely_uplink(client, db, org, operator, switches):
    for index in range(7):
        add_host(
            db, org, switches[0], f"aa:00:00:01:00:{index:02x}",
            interface="Gi1/0/24",
        )
    add_host(db, org, switches[0], "aa:00:00:02:00:01", interface="Gi1/0/1")

    body = as_user(client, operator).get("/api/v1/inventory/reports/by-port").json()

    by_interface = {entry["interface"]: entry for entry in body["ports"]}
    assert by_interface["Gi1/0/24"]["hosts"] == 7
    assert by_interface["Gi1/0/24"]["likely_uplink"] is True
    assert by_interface["Gi1/0/1"]["likely_uplink"] is False
    assert by_interface["Gi1/0/24"]["device_hostname"] == "core-01"

    busy = as_user(client, operator).get(
        "/api/v1/inventory/reports/by-port", params={"min_hosts": 5}
    ).json()
    assert [entry["interface"] for entry in busy["ports"]] == ["Gi1/0/24"]


def test_changes_report_splits_appeared_and_disappeared(
    client, db, org, operator, switches
):
    now = datetime.now(timezone.utc)

    add_host(db, org, switches[0], "aa:00:00:03:00:01", first_seen=now)

    gone = add_host(
        db, org, switches[0], "aa:00:00:03:00:02",
        interface="Gi1/0/2",
        first_seen=now - timedelta(days=30),
        last_seen=now - timedelta(days=2),
    )
    gone.is_active = False

    long_gone = add_host(
        db, org, switches[0], "aa:00:00:03:00:03",
        interface="Gi1/0/3",
        first_seen=now - timedelta(days=90),
        last_seen=now - timedelta(days=60),
    )
    long_gone.is_active = False
    db.commit()

    body = as_user(client, operator).get(
        "/api/v1/inventory/reports/changes", params={"days": 7}
    ).json()

    assert body["appeared_count"] == 1
    assert body["appeared"][0]["mac_address"] == "aa:00:00:03:00:01"
    assert body["disappeared_count"] == 1
    assert body["disappeared"][0]["mac_address"] == "aa:00:00:03:00:02"
    assert body["disappeared"][0]["device_hostname"] == "core-01"


def test_csv_export(client, db, org, operator, switches):
    add_host(
        db, org, switches[0], "aa:00:00:04:00:01",
        vendor="Acme", ip_address="10.0.9.9", hostname="host-a",
        notes="rack 3",
    )

    response = as_user(client, operator).get("/api/v1/inventory/reports/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=inventory_" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["switch"] == "core-01"
    assert rows[0]["host_name"] == "host-a"
    assert rows[0]["mac_address"] == "aa:00:00:04:00:01"
    assert rows[0]["vendor"] == "Acme"
    assert rows[0]["active"] == "yes"
    assert rows[0]["notes"] == "rack 3"


def test_reports_are_tenant_scoped(client, db, operator):
    other = Organization(name="OtherReports", is_active=True)
    db.add(other)
    db.commit()

    device = Device(
        organization_id=other.id,
        hostname="their-core",
        ip_address="192.168.60.1",
        device_type="cisco_ios",
        username="admin",
        encrypted_password=encryption_service.encrypt("secret"),
    )
    db.add(device)
    db.commit()

    db.add(
        HostInventory(
            organization_id=other.id,
            device_id=device.id,
            interface="Gi0/1",
            mac_address="99:99:99:00:00:01",
            vlan=1,
        )
    )
    db.commit()

    session = as_user(client, operator)
    assert session.get(
        "/api/v1/inventory/reports/summary"
    ).json()["total_entries"] == 0
    assert session.get("/api/v1/inventory/reports/export").text.count("\n") == 1
