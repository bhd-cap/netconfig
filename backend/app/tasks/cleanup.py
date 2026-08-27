"""
Cleanup tasks for Celery
"""
import logging

from app.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories.device import DeviceRepository
from app.services.config_retriever import ConfigurationRetriever

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.cleanup.cleanup_old_backups_task", ignore_result=True)
def cleanup_old_backups_task(keep_count: int = None):
    """
    Apply retention policy to clean up old backup configurations

    Runs nightly. Devices are walked in server-side batches so a deployment
    with many thousands of devices never materialises them all at once, and
    each device's excess records are deleted in one statement.

    Args:
        keep_count: Configurations to keep per device (defaults to
            DEFAULT_RETENTION_DAYS)

    Returns:
        dict: Cleanup result
    """
    if keep_count is None:
        keep_count = settings.DEFAULT_RETENTION_DAYS

    logger.info(f"Starting retention cleanup (keeping {keep_count} per device)")

    db = SessionLocal()
    try:
        device_repo = DeviceRepository(db)
        retriever = ConfigurationRetriever(db)

        devices_processed = 0
        deleted_total = 0

        for device_id in device_repo.iter_all_ids():
            devices_processed += 1
            try:
                deleted_total += retriever.apply_retention_policy(
                    device_id, keep_count
                )
            except Exception as e:
                logger.error(
                    f"Retention cleanup failed for device {device_id}: {e}"
                )
                db.rollback()

        logger.info(
            f"Retention cleanup finished: {deleted_total} configurations removed "
            f"across {devices_processed} devices"
        )

        return {
            "success": True,
            "devices_processed": devices_processed,
            "configurations_deleted": deleted_total,
            "keep_count": keep_count,
        }

    except Exception as e:
        logger.exception("Retention cleanup failed")
        return {
            "success": False,
            "error": str(e),
        }

    finally:
        db.close()
