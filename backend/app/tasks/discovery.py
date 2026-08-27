"""
Discovery tasks for Celery
"""
import logging
from datetime import datetime, timedelta, timezone

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.repositories.audit_log import AuditLogRepository
from app.services.discovery import DiscoveryService
from app.services.rediscovery import RediscoveryService

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.discovery.discovery_crawl_task")
def discovery_crawl_task(
    self,
    organization_id: int,
    seed_device_id: int,
    max_hops: int = 2,
    auto_add: bool = False,
    collect_inventory: bool = True,
    user_id: int = None,
):
    """
    Run a discovery crawl

    Args:
        organization_id: Tenant scope
        seed_device_id: Device to start from
        max_hops: How far to walk
        auto_add: Register neighbours that are not managed yet
        collect_inventory: Also collect MAC tables and ARP
        user_id: Who asked

    Returns:
        dict summarising the crawl
    """
    logger.info(
        f"Discovery crawl starting from device {seed_device_id} "
        f"(max_hops={max_hops}, auto_add={auto_add})"
    )

    db = SessionLocal()
    try:
        service = DiscoveryService(db)
        summary = service.crawl(
            organization_id=organization_id,
            seed_device_id=seed_device_id,
            max_hops=max_hops,
            auto_add=auto_add,
            collect_inventory=collect_inventory,
            user_id=user_id,
        )

        return {
            "success": True,
            "run_id": summary.run_id,
            "devices_probed": summary.devices_probed,
            "devices_failed": summary.devices_failed,
            "neighbors_found": summary.neighbors_found,
            "hosts_found": summary.hosts_found,
            "devices_created": summary.devices_created,
            "unmanaged_count": len(summary.unmanaged),
        }

    except Exception as e:
        logger.exception(f"Discovery crawl from device {seed_device_id} failed")
        return {"success": False, "error": str(e)}

    finally:
        db.close()


@celery_app.task(name="app.tasks.discovery.refresh_inventory_task")
def refresh_inventory_task(organization_id: int, device_ids: list = None):
    """
    Re-read MAC tables and ARP from devices without walking the topology

    Cheaper than a full crawl, so it can run often enough for last-seen to
    mean something.

    Args:
        organization_id: Tenant scope
        device_ids: Devices to sweep; all active ones when omitted

    Returns:
        dict summarising the sweep
    """
    from sqlalchemy import select

    from app.models.device import Device

    db = SessionLocal()
    try:
        service = DiscoveryService(db)

        if device_ids:
            statement = select(Device).where(
                Device.id.in_(device_ids),
                Device.organization_id == organization_id,
            )
        else:
            statement = select(Device).where(
                Device.organization_id == organization_id,
                Device.is_active.is_(True),
            )

        devices = list(db.execute(statement).scalars())
        if not devices:
            return {"success": True, "devices": 0, "hosts": 0}

        probed = 0
        hosts = 0
        seen_at = datetime.now(timezone.utc)

        results = service._probe_many(
            [device.id for device in devices], ("mac", "arp"), 10
        )

        for result in results:
            if result.error:
                continue
            probed += 1
            hosts += service.save_inventory(
                organization_id,
                result.snapshot.id,
                result.mac_entries,
                result.arp_entries,
                seen_at,
                neighbors=result.neighbors,
                device_hostname=result.snapshot.hostname,
            )

        db.commit()

        return {"success": True, "devices": probed, "hosts": hosts}

    except Exception as e:
        logger.exception("Inventory refresh failed")
        db.rollback()
        return {"success": False, "error": str(e)}

    finally:
        db.close()


@celery_app.task(
    name="app.tasks.discovery.age_inventory_task", ignore_result=True
)
def age_inventory_task(stale_after_hours: int = 48):
    """
    Mark adjacencies and hosts that have stopped being seen as inactive

    Rows are never deleted: the useful answer to "where did that host go" is
    "last seen on port 12 three weeks ago", which needs the row to survive.

    Args:
        stale_after_hours: How long without a sighting counts as gone

    Returns:
        dict with the counts marked
    """
    from sqlalchemy import select

    from app.models.organization import Organization

    db = SessionLocal()
    try:
        service = DiscoveryService(db)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)

        organizations = list(
            db.execute(select(Organization.id).where(Organization.is_active.is_(True)))
            .scalars()
        )

        neighbors_total = 0
        hosts_total = 0

        for organization_id in organizations:
            neighbors, hosts = service.mark_stale(organization_id, cutoff)
            neighbors_total += neighbors
            hosts_total += hosts

        db.commit()

        if neighbors_total or hosts_total:
            logger.info(
                f"Aged out {neighbors_total} adjacencies and {hosts_total} hosts "
                f"not seen for {stale_after_hours}h"
            )

        return {
            "success": True,
            "neighbors_marked": neighbors_total,
            "hosts_marked": hosts_total,
        }

    except Exception as e:
        logger.exception("Inventory ageing failed")
        db.rollback()
        return {"success": False, "error": str(e)}

    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.discovery.rediscover_devices_task",
    max_retries=0,
)
def rediscover_devices_task(
    self,
    organization_id: int,
    device_ids: list = None,
    include_inactive: bool = True,
    user_id: int = None,
):
    """
    Re-probe existing devices over SSH, telnet and SNMP

    Queued rather than run inline because a probe walks every vault credential
    over SSH and then telnet: one unreachable device can take a minute, and an
    estate takes as long as it takes.

    No retries. A rediscovery that failed has already written whatever it
    learned about the devices it reached, and running the whole pass again
    would re-probe those too - the operator can simply ask again.

    Args:
        organization_id: Tenant scope
        device_ids: Devices to probe; every device when omitted
        include_inactive: Probe devices that are off the backup list too
        user_id: Who asked, for the audit trail

    Returns:
        dict summary
    """
    db = SessionLocal()

    try:
        service = RediscoveryService(db)
        summary = service.rediscover(
            organization_id=organization_id,
            device_ids=device_ids,
            include_inactive=include_inactive,
        )

        AuditLogRepository(db).log_action(
            user_id=user_id,
            action="devices_rediscovered",
            resource_type="device",
            resource_id=None,
            details={
                "probed": summary.probed,
                "authenticated": summary.authenticated,
                "changed": summary.changed,
                "failed": summary.failed,
            },
        )
        db.commit()

        logger.info(
            f"Rediscovery probed {summary.probed} device(s): "
            f"{summary.authenticated} authenticated, {summary.changed} changed, "
            f"{summary.failed} failed"
        )

        return {"success": True, **summary.as_dict()}

    except Exception as e:
        logger.exception("Rediscovery failed")
        db.rollback()
        return {"success": False, "error": str(e)}

    finally:
        db.close()
