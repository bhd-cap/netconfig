"""
Remote backup export tasks

Uploading is deliberately kept off the backup path itself. A backup that
succeeded is a backup that succeeded, even if the archive server is down, so
the copy runs as its own task and the retriever never waits on it.
"""
import logging
import os
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.administration import BackupTarget
from app.models.configuration import Configuration
from app.models.device import Device
from app.services import remote_backup

logger = logging.getLogger(__name__)


def _files_for_configurations(
    db: Session,
    organization_id: int,
    configuration_ids: Optional[Sequence[int]] = None,
    device_ids: Optional[Sequence[int]] = None,
    limit: int = 200,
) -> List[Dict]:
    """
    Collect the files to send

    With no configuration ids the latest successful backup of each device is
    used, which is what seeding a fresh archive means.

    Args:
        db: Database session
        organization_id: Tenant scope
        configuration_ids: Specific configurations
        device_ids: Restrict to these devices
        limit: Cap on how many files one run sends

    Returns:
        List of dicts for remote_backup.upload_files
    """
    statement = (
        select(
            Configuration.id,
            Configuration.filename,
            Configuration.file_path,
            Device.hostname,
        )
        .join(Device, Device.id == Configuration.device_id)
        .where(
            Device.organization_id == organization_id,
            Configuration.status == "success",
        )
    )

    if configuration_ids:
        statement = statement.where(Configuration.id.in_(list(configuration_ids)))
    else:
        # DISTINCT ON gives the newest row per device in one pass; the ORDER BY
        # is what picks which row survives, so it has to lead with device_id.
        statement = statement.distinct(Configuration.device_id).order_by(
            Configuration.device_id, Configuration.backed_up_at.desc()
        )

    if device_ids:
        statement = statement.where(Configuration.device_id.in_(list(device_ids)))

    if configuration_ids:
        statement = statement.order_by(Configuration.backed_up_at.desc())

    rows = db.execute(statement.limit(limit)).all()

    files = []
    for row in rows:
        if not row.file_path or not os.path.exists(row.file_path):
            logger.warning(
                f"Skipping configuration {row.id}: {row.file_path} is missing"
            )
            continue

        files.append(
            {
                "local_path": row.file_path,
                "filename": row.filename,
                "hostname": row.hostname,
                "organization_id": organization_id,
            }
        )

    return files


def run_upload(
    db: Session,
    organization_id: int,
    target: BackupTarget,
    configuration_ids: Optional[Sequence[int]] = None,
    device_ids: Optional[Sequence[int]] = None,
    limit: int = 200,
) -> Dict:
    """
    Send configurations to one target and record the outcome

    Args:
        db: Database session
        organization_id: Tenant scope
        target: The target to send to
        configuration_ids: Specific configurations; latest per device when omitted
        device_ids: Restrict to these devices
        limit: Cap on how many files one run sends

    Returns:
        dict describing what happened
    """
    files = _files_for_configurations(
        db, organization_id, configuration_ids, device_ids, limit
    )

    if not files:
        return {
            "success": True,
            "target": target.name,
            "uploaded": 0,
            "failed": 0,
            "message": "Nothing to upload",
        }

    config = remote_backup.TargetConfig.from_model(target)
    result = remote_backup.upload_files(config, files)
    remote_backup.record_result(db, target, result)

    logger.info(
        f"Uploaded {result.succeeded}/{len(files)} configurations to "
        f"'{target.name}' ({target.protocol}://{target.host})"
    )

    return {
        "success": result.ok,
        "target": target.name,
        "uploaded": result.succeeded,
        "failed": result.failed,
        "errors": result.errors[:10],
        "message": (
            f"Uploaded {result.succeeded} file(s) to '{target.name}'"
            if result.ok
            else f"{result.failed} of {len(files)} upload(s) failed"
        ),
    }


@celery_app.task(name="app.tasks.remote_backup.upload_to_target_task")
def upload_to_target_task(
    organization_id: int,
    target_id: int,
    configuration_ids: list = None,
    device_ids: list = None,
    limit: int = 200,
    user_id: int = None,
):
    """
    Send configurations to one target

    Args:
        organization_id: Tenant scope
        target_id: The target
        configuration_ids: Specific configurations
        device_ids: Restrict to these devices
        limit: Cap on how many files one run sends
        user_id: Who asked, for the audit log

    Returns:
        dict describing what happened
    """
    db = SessionLocal()
    try:
        target = db.execute(
            select(BackupTarget).where(
                BackupTarget.id == target_id,
                BackupTarget.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not target:
            return {"success": False, "message": f"Target {target_id} not found"}

        result = run_upload(
            db,
            organization_id=organization_id,
            target=target,
            configuration_ids=configuration_ids,
            device_ids=device_ids,
            limit=limit,
        )

        from app.repositories.audit_log import AuditLogRepository

        AuditLogRepository(db).log_action(
            user_id=user_id,
            action="upload_to_backup_target",
            resource_type="backup_target",
            resource_id=target_id,
            details=result,
            status="success" if result.get("success") else "failed",
        )

        return result

    except Exception as e:
        logger.exception(f"Upload to target {target_id} failed")
        db.rollback()
        return {"success": False, "message": str(e)}

    finally:
        db.close()


@celery_app.task(name="app.tasks.remote_backup.export_new_configurations_task")
def export_new_configurations_task(organization_id: int, configuration_ids: list):
    """
    Copy freshly stored configurations to every target that asked for them

    Queued after a backup completes. A target that is disabled, or that has
    upload_on_backup turned off, is skipped.

    Args:
        organization_id: Tenant scope
        configuration_ids: The configurations just written

    Returns:
        dict with one entry per target
    """
    if not configuration_ids:
        return {"success": True, "targets": 0, "results": []}

    db = SessionLocal()
    try:
        targets = list(
            db.execute(
                select(BackupTarget).where(
                    BackupTarget.organization_id == organization_id,
                    BackupTarget.is_enabled.is_(True),
                    BackupTarget.upload_on_backup.is_(True),
                )
            ).scalars()
        )

        if not targets:
            return {"success": True, "targets": 0, "results": []}

        results = []
        for target in targets:
            try:
                results.append(
                    run_upload(
                        db,
                        organization_id=organization_id,
                        target=target,
                        configuration_ids=configuration_ids,
                        limit=len(configuration_ids),
                    )
                )
            except Exception as e:  # noqa: BLE001 - one bad target must not stop the rest
                logger.exception(f"Export to target '{target.name}' failed")
                results.append(
                    {"success": False, "target": target.name, "message": str(e)}
                )

        return {
            "success": all(entry.get("success") for entry in results),
            "targets": len(targets),
            "results": results,
        }

    except Exception as e:
        logger.exception("Export of new configurations failed")
        db.rollback()
        return {"success": False, "message": str(e)}

    finally:
        db.close()


def queue_export(organization_id: int, configuration_ids: Sequence[int]) -> None:
    """
    Ask for new configurations to be exported, without ever failing the caller

    Called from the backup path. If the broker is unreachable the backup is
    still a success - the archive copy is a separate concern and the next
    backup, or a manual upload, will catch up.

    Args:
        organization_id: Tenant scope
        configuration_ids: The configurations just written
    """
    ids = [int(value) for value in configuration_ids if value]
    if not ids:
        return

    try:
        export_new_configurations_task.delay(organization_id, ids)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not queue remote export of {len(ids)} configuration(s): {e}")
