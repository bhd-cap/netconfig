"""
Scheduled SNMP polling for hardware inventory and environmental readings

Two tasks: one that polls, and one that prunes the history it writes. The
second is not optional - sensor_readings is the only table in this application
that grows with time rather than with the size of the estate.
"""
import logging

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.repositories.audit_log import AuditLogRepository
from app.services.telemetry import TelemetryService

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.telemetry.poll_telemetry_task",
    max_retries=0,
)
def poll_telemetry_task(
    self,
    organization_id: int = None,
    device_ids: list = None,
    user_id: int = None,
):
    """
    Poll devices over SNMP for hardware and readings

    No retries. A pass that failed part way has already stored what it learned
    from the devices that answered, and running the whole thing again would
    re-walk those too; the next scheduled run is soon enough.

    Args:
        organization_id: Tenant scope; every organization when omitted, which
            is how the scheduled run calls it
        device_ids: Devices to poll; every SNMP-capable device when omitted
        user_id: Who asked, for the audit trail

    Returns:
        dict summary
    """
    db = SessionLocal()

    try:
        service = TelemetryService(db)

        if organization_id is not None:
            organizations = [organization_id]
        else:
            from sqlalchemy import select

            from app.models.organization import Organization

            organizations = list(
                db.scalars(
                    select(Organization.id).where(Organization.is_active.is_(True))
                ).all()
            )

        totals = {"polled": 0, "answered": 0, "failed": 0, "components": 0, "sensors": 0}
        per_organization = []

        for scope in organizations:
            summary = service.poll(organization_id=scope, device_ids=device_ids)

            for key in totals:
                totals[key] += getattr(summary, key)

            per_organization.append({"organization_id": scope, **summary.as_dict()})

        if user_id is not None:
            AuditLogRepository(db).log_action(
                user_id=user_id,
                action="telemetry_polled",
                resource_type="device",
                resource_id=None,
                details=totals,
            )
            db.commit()

        logger.info(
            f"SNMP telemetry: polled {totals['polled']} device(s), "
            f"{totals['answered']} answered, {totals['components']} components, "
            f"{totals['sensors']} sensors"
        )

        return {"success": True, **totals, "organizations": per_organization}

    except Exception as e:
        logger.exception("SNMP telemetry polling failed")
        db.rollback()
        return {"success": False, "error": str(e)}

    finally:
        db.close()


@celery_app.task(
    name="app.tasks.telemetry.prune_sensor_history_task",
)
def prune_sensor_history_task(older_than_days: int = 30):
    """
    Drop sensor readings past the retention window

    At a poll every thirty minutes, one switch with twenty sensors writes
    roughly a million rows a year. Without this the table is the largest thing
    in the database within a season.

    Args:
        older_than_days: Days of history to keep

    Returns:
        dict with the number of rows removed
    """
    db = SessionLocal()

    try:
        removed = TelemetryService(db).prune_history(older_than_days=older_than_days)

        if removed:
            logger.info(
                f"Pruned {removed} sensor reading(s) older than {older_than_days} days"
            )

        return {"success": True, "removed": removed}

    except Exception as e:
        logger.exception("Pruning sensor history failed")
        db.rollback()
        return {"success": False, "error": str(e)}

    finally:
        db.close()
