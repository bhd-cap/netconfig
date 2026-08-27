"""
Backup tasks for Celery
"""
from datetime import datetime
from croniter import croniter
import logging

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.services.config_retriever import ConfigurationRetriever
from app.repositories.backup_job import BackupJobRepository
from app.repositories.device import DeviceRepository
from app.tasks.remote_backup import queue_export

logger = logging.getLogger(__name__)


def _export_new_configurations(db, organization_id, result):
    """
    Hand freshly written configurations to the remote-export task

    Split out so every backup entry point copies to the configured SFTP/FTP
    archives the same way. Failures here are logged and swallowed: the backup
    itself already succeeded.
    """
    if not organization_id:
        return

    if "devices" in result:
        ids = [
            entry.get("configuration_id")
            for entry in result.get("devices", [])
            if entry.get("configuration_id")
        ]
    else:
        ids = [result["configuration_id"]] if result.get("configuration_id") else []

    queue_export(organization_id, ids)


@celery_app.task(bind=True, name="app.tasks.backup.backup_device_task", max_retries=3)
def backup_device_task(self, device_id: int, user_id: int = None):
    """
    Backup a single device configuration

    Args:
        device_id: Device ID to backup
        user_id: User ID triggering the backup (optional)

    Returns:
        dict: Backup result
    """
    logger.info(f"Starting backup task for device_id={device_id}")

    db = SessionLocal()
    try:
        retriever = ConfigurationRetriever(db)
        result = retriever.backup_device(device_id, user_id)

        if not result["success"]:
            logger.warning(f"Backup failed for device {device_id}: {result['message']}")

            # Retry on connection errors
            if "connection" in result["message"].lower() or "timeout" in result["message"].lower():
                logger.info(f"Retrying backup for device {device_id} (attempt {self.request.retries + 1}/3)")
                raise self.retry(exc=Exception(result["message"]), countdown=60)

        elif result.get("configuration_id"):
            device = DeviceRepository(db).get(device_id)
            _export_new_configurations(
                db, device.organization_id if device else None, result
            )

        return result

    except Exception as e:
        logger.exception(f"Error in backup task for device {device_id}")
        return {
            "success": False,
            "device_id": device_id,
            "message": f"Task error: {str(e)}",
            "duration": 0,
        }

    finally:
        db.close()


@celery_app.task(name="app.tasks.backup.bulk_backup_task")
def bulk_backup_task(device_ids: list, user_id: int = None):
    """
    Backup multiple devices

    Args:
        device_ids: List of device IDs to backup
        user_id: User ID triggering the backup (optional)

    Returns:
        dict: Bulk backup result
    """
    logger.info(f"Starting bulk backup task for {len(device_ids)} devices")

    db = SessionLocal()
    try:
        retriever = ConfigurationRetriever(db)
        result = retriever.backup_multiple_devices(device_ids, user_id)

        logger.info(
            f"Bulk backup completed: {result['successful']}/{result['total']} successful"
        )

        first = DeviceRepository(db).get(device_ids[0]) if device_ids else None
        _export_new_configurations(
            db, first.organization_id if first else None, result
        )

        return result

    except Exception as e:
        logger.exception(f"Error in bulk backup task")
        return {
            "total": len(device_ids),
            "successful": 0,
            "failed": len(device_ids),
            "error": str(e),
        }

    finally:
        db.close()


@celery_app.task(name="app.tasks.backup.scheduled_backup_task")
def scheduled_backup_task(job_id: int):
    """
    Execute a scheduled backup job

    Args:
        job_id: Backup job ID

    Returns:
        dict: Job execution result
    """
    logger.info(f"Executing scheduled backup job {job_id}")

    db = SessionLocal()
    try:
        job_repo = BackupJobRepository(db)
        retriever = ConfigurationRetriever(db)

        # Get the job
        job = job_repo.get(job_id)
        if not job:
            logger.error(f"Backup job {job_id} not found")
            return {
                "success": False,
                "message": f"Job {job_id} not found",
            }

        if not job.is_enabled:
            logger.warning(f"Backup job {job_id} is disabled, skipping")
            return {
                "success": False,
                "message": "Job is disabled",
            }

        # A maintenance window means "leave the network alone", so a job that
        # comes due inside one is held rather than run. The next run time is
        # still advanced, otherwise the job would fire the moment the window
        # closes and every held run would stack up.
        from app.services import app_settings

        org_settings = app_settings.get_or_create(db, job.organization_id)
        suppressed, window_name = app_settings.backups_suppressed(org_settings)

        if suppressed:
            logger.info(
                f"Backup job {job_id} held by maintenance window '{window_name}'"
            )

            current_time = datetime.utcnow()
            try:
                next_run = croniter(job.schedule_cron, current_time).get_next(datetime)
            except Exception:
                next_run = None

            job_repo.update_last_run(job_id, current_time, next_run)

            return {
                "success": True,
                "job_id": job_id,
                "skipped": True,
                "message": f"Held by maintenance window '{window_name}'",
                "devices_backed_up": 0,
                "next_run": next_run.isoformat() if next_run else None,
            }

        # Which devices this job covers. An empty filter means every device
        # that can be backed up, so a job created before filtering existed
        # behaves exactly as it always did. Only IDs are selected, so the
        # encrypted credentials on every candidate are not materialised just
        # to pick a set.
        from app.services import device_filter as device_filter_service

        try:
            device_ids = device_filter_service.resolve(
                db, job.organization_id, job.device_filter
            )
        except device_filter_service.FilterError as e:
            # A stored filter that no longer validates - a device type removed
            # from the catalogue, say. Backing up everything instead would
            # silently widen the job, so fail loudly and leave it to be fixed.
            logger.error(f"Backup job {job_id} has an invalid device filter: {e}")

            current_time = datetime.utcnow()
            try:
                next_run = croniter(job.schedule_cron, current_time).get_next(datetime)
            except Exception:
                next_run = None
            job_repo.update_last_run(job_id, current_time, next_run)

            return {
                "success": False,
                "job_id": job_id,
                "error": f"Invalid device filter: {e}",
            }

        if not device_ids:
            # A filter that matches nothing today may match something
            # tomorrow, so the job stays enabled - but its next run has to be
            # advanced, or it comes due again on the very next check and
            # re-fires every minute.
            logger.warning(
                f"Job {job_id} matched no devices "
                f"({device_filter_service.describe(job.device_filter)})"
            )

            current_time = datetime.utcnow()
            try:
                next_run = croniter(job.schedule_cron, current_time).get_next(datetime)
            except Exception:
                next_run = None
            job_repo.update_last_run(job_id, current_time, next_run)

            return {
                "success": True,
                "job_id": job_id,
                "message": "No devices matched this job's filter",
                "devices_backed_up": 0,
                "next_run": next_run.isoformat() if next_run else None,
            }

        logger.info(f"Job {job_id}: backing up {len(device_ids)} devices")

        result = retriever.backup_multiple_devices(device_ids, user_id=None)

        # Update job's last run and calculate next run
        current_time = datetime.utcnow()
        try:
            cron = croniter(job.schedule_cron, current_time)
            next_run = cron.get_next(datetime)
        except Exception as e:
            logger.error(f"Error calculating next run for job {job_id}: {e}")
            next_run = None

        job_repo.update_last_run(job_id, current_time, next_run)

        _export_new_configurations(db, job.organization_id, result)

        if result["failed"]:
            failed = [
                entry["hostname"] or f"device {entry['device_id']}"
                for entry in result["devices"]
                if not entry["success"]
            ]
            app_settings.notify(
                db,
                job.organization_id,
                "backup_failure",
                subject=f"NetConfig Backup: {result['failed']} device(s) failed",
                body=(
                    f"Scheduled job '{job.name}' backed up "
                    f"{result['successful']} of {result['total']} devices.\n\n"
                    "Failed:\n" + "\n".join(f"  - {name}" for name in failed[:50])
                ),
            )
        elif result["successful"]:
            app_settings.notify(
                db,
                job.organization_id,
                "backup_success",
                subject=f"NetConfig Backup: job '{job.name}' completed",
                body=(
                    f"Scheduled job '{job.name}' backed up all "
                    f"{result['successful']} device(s) successfully."
                ),
            )

        logger.info(
            f"Scheduled job {job_id} completed: "
            f"{result['successful']}/{result['total']} successful"
        )

        return {
            "success": True,
            "job_id": job_id,
            "devices_backed_up": result["successful"],
            "devices_failed": result["failed"],
            "next_run": next_run.isoformat() if next_run else None,
        }

    except Exception as e:
        logger.exception(f"Error executing scheduled backup job {job_id}")
        return {
            "success": False,
            "job_id": job_id,
            "error": str(e),
        }

    finally:
        db.close()


@celery_app.task(
    name="app.tasks.backup.apply_retention_policy_task", ignore_result=True
)
def apply_retention_policy_task(device_id: int, keep_count: int = None):
    """
    Apply retention policy to a device's configurations

    Args:
        device_id: Device ID
        keep_count: Number of configurations to keep (optional)

    Returns:
        dict: Retention policy result
    """
    logger.info(f"Applying retention policy to device {device_id}")

    db = SessionLocal()
    try:
        retriever = ConfigurationRetriever(db)
        deleted_count = retriever.apply_retention_policy(device_id, keep_count)

        logger.info(f"Retention policy applied to device {device_id}: {deleted_count} configs deleted")

        return {
            "success": True,
            "device_id": device_id,
            "deleted_count": deleted_count,
        }

    except Exception as e:
        logger.exception(f"Error applying retention policy to device {device_id}")
        return {
            "success": False,
            "device_id": device_id,
            "error": str(e),
        }

    finally:
        db.close()


@celery_app.task(
    name="app.tasks.backup.check_scheduled_jobs_task", ignore_result=True
)
def check_scheduled_jobs_task():
    """
    Check for scheduled backup jobs that are due to run

    This task is triggered periodically by Celery Beat (every minute)
    and executes any jobs that are scheduled to run.

    Returns:
        dict: Summary of jobs checked and triggered
    """
    logger.info("Checking for scheduled backup jobs")

    db = SessionLocal()
    try:
        job_repo = BackupJobRepository(db)
        current_time = datetime.utcnow()

        # Get jobs that are due. Only (id, name) is selected: this runs every
        # 60 seconds forever and almost always finds nothing to do.
        due_jobs = job_repo.get_due_job_identifiers(current_time)

        if not due_jobs:
            logger.debug("No scheduled jobs due at this time")
            return {
                "success": True,
                "jobs_checked": 0,
                "jobs_triggered": 0,
            }

        logger.info(f"Found {len(due_jobs)} job(s) due for execution")

        triggered_count = 0
        for job_id, job_name in due_jobs:
            try:
                # Trigger the scheduled backup task
                task = scheduled_backup_task.delay(job_id)
                logger.info(
                    f"Triggered job '{job_name}' (ID: {job_id}, Task: {task.id})"
                )
                triggered_count += 1

            except Exception as e:
                logger.error(f"Failed to trigger job {job_id}: {e}")

        return {
            "success": True,
            "jobs_checked": len(due_jobs),
            "jobs_triggered": triggered_count,
            "timestamp": current_time.isoformat(),
        }

    except Exception as e:
        logger.exception("Error checking scheduled jobs")
        return {
            "success": False,
            "error": str(e),
        }

    finally:
        db.close()
