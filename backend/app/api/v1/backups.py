"""
Backup/Configuration API endpoints
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from celery.result import AsyncResult

from app.core.database import get_db
from app.api.deps import get_current_user, get_organization_id
from app.models.user import User
from app.repositories.configuration import ConfigurationRepository
from app.repositories.device import DeviceRepository
from app.repositories.audit_log import AuditLogRepository
from app.schemas.configuration import (
    ConfigurationResponse,
    BackupTriggerRequest,
    BackupTriggerResponse,
)
from app.schemas.common import PaginatedResponse, TaskStatusResponse
from app.tasks.backup import backup_device_task, bulk_backup_task
from app.celery_app import celery_app
from app.services.storage import storage_service

router = APIRouter()


@router.post("/trigger", response_model=BackupTriggerResponse)
def trigger_backup(
    request: BackupTriggerRequest,
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Trigger backup for one or more devices

    Args:
        request: Backup trigger request with device IDs
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        BackupTriggerResponse: Task information

    Raises:
        HTTPException: If devices not found or access denied
    """
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    # Verify all devices belong to user's organization. One IN query rather
    # than a round trip per requested device.
    found_ids = {
        device.id
        for device in device_repo.get_by_ids_and_organization(
            request.device_ids, organization_id
        )
    }
    missing = [
        device_id for device_id in request.device_ids if device_id not in found_ids
    ]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device {missing[0]} not found or access denied",
        )

    # Trigger backup task(s)
    if len(request.device_ids) == 1:
        # Single device backup
        task = backup_device_task.delay(request.device_ids[0], current_user.id)
        task_type = "single"
    else:
        # Bulk backup
        task = bulk_backup_task.delay(request.device_ids, current_user.id)
        task_type = "bulk"

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_triggered",
        resource_type="device",
        details={
            "device_ids": request.device_ids,
            "device_count": len(request.device_ids),
            "task_id": task.id,
            "task_type": task_type,
        },
    )

    return {
        "task_id": task.id,
        "device_count": len(request.device_ids),
        "message": f"Backup started for {len(request.device_ids)} device(s)",
    }


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Get status of a backup task

    Args:
        task_id: Celery task ID
        current_user: Current authenticated user

    Returns:
        TaskStatusResponse: Task status and result
    """
    task_result = AsyncResult(task_id, app=celery_app)

    # Read state and payload once. Every state predicate on AsyncResult
    # (successful(), failed(), .state) can hit the result backend again.
    state = task_result.state
    info = task_result.info

    response = {
        "task_id": task_id,
        "status": state,
        "result": None,
        "error": None,
        "progress": None,
    }

    if state == "SUCCESS":
        response["result"] = info
    elif state == "FAILURE":
        response["error"] = str(info)
    elif state == "PROGRESS" and isinstance(info, dict):
        response["progress"] = info.get("progress", 0)

    return response


@router.get("", response_model=PaginatedResponse[ConfigurationResponse])
def list_configurations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    device_id: int = Query(None),
    status: str = Query(None),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List configuration backups with filtering

    Args:
        skip: Number of records to skip
        limit: Maximum number of records
        device_id: Filter by device ID (optional)
        status: Filter by backup status (optional)
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        PaginatedResponse: Paginated list of configurations
    """
    config_repo = ConfigurationRepository(db)
    device_repo = DeviceRepository(db)

    if device_id:
        # Verify device belongs to organization
        device = device_repo.get_by_id_and_organization(device_id, organization_id)
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found",
            )

    # The listing query already joins devices for the tenant filter, so the
    # hostname/IP come from that join. Reading config.device per row used to
    # fire an extra SELECT for every item on the page.
    configurations = config_repo.get_by_organization(
        organization_id,
        skip=skip,
        limit=limit,
        status=status,
        device_id=device_id,
        with_device=True,
    )
    total = config_repo.count_by_organization(
        organization_id, status=status, device_id=device_id
    )

    # Enrich with device information
    response_items = [
        ConfigurationResponse.model_validate(config).model_copy(
            update={
                "device_hostname": config.device.hostname if config.device else None,
                "device_ip": config.device.ip_address if config.device else None,
            }
        )
        for config in configurations
    ]

    return {
        "total": total,
        "page": (skip // limit) + 1,
        "page_size": limit,
        "total_pages": (total + limit - 1) // limit,
        "items": response_items,
    }


@router.get("/{config_id}", response_model=ConfigurationResponse)
def get_configuration(
    config_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get configuration backup details

    Args:
        config_id: Configuration ID
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        ConfigurationResponse: Configuration details

    Raises:
        HTTPException: If configuration not found or access denied
    """
    config_repo = ConfigurationRepository(db)

    # Configuration and device in one joined query instead of two.
    found = config_repo.get_with_device(config_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    config, device = found

    if device.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Enrich with device info
    return ConfigurationResponse.model_validate(config).model_copy(
        update={
            "device_hostname": device.hostname,
            "device_ip": device.ip_address,
        }
    )


@router.get("/{config_id}/download")
def download_configuration(
    config_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download configuration file

    Args:
        config_id: Configuration ID
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        FileResponse: Configuration file

    Raises:
        HTTPException: If configuration not found or access denied
    """
    config_repo = ConfigurationRepository(db)
    audit_repo = AuditLogRepository(db)

    found = config_repo.get_with_device(config_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    config, device = found

    if device.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Check the file is present with a stat() call. This used to read the
    # entire configuration into memory and discard it, only for FileResponse
    # to stream the same file again from disk.
    if not storage_service.exists(config.file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration file not found: {config.filename}",
        )

    # Log download
    audit_repo.log_action(
        user_id=current_user.id,
        action="configuration_downloaded",
        resource_type="configuration",
        resource_id=config.id,
        details={
            "device_hostname": device.hostname,
            "filename": config.filename,
        },
    )

    # Return file
    return FileResponse(
        path=config.file_path,
        filename=config.filename,
        media_type="text/plain",
    )


@router.delete("/{config_id}")
def delete_configuration(
    config_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a configuration backup

    Args:
        config_id: Configuration ID
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        dict: Success message

    Raises:
        HTTPException: If configuration not found or access denied
    """
    config_repo = ConfigurationRepository(db)
    audit_repo = AuditLogRepository(db)

    found = config_repo.get_with_device(config_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    config, device = found

    if device.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    filename = config.filename
    file_path = config.file_path

    # Delete from database
    config_repo.delete(config_id)

    # Delete file from storage; a missing file is not an error here.
    storage_service.delete_file(file_path)

    # Log deletion
    audit_repo.log_action(
        user_id=current_user.id,
        action="configuration_deleted",
        resource_type="configuration",
        resource_id=config_id,
        details={
            "device_hostname": device.hostname,
            "filename": filename,
        },
    )

    return {
        "success": True,
        "message": f"Configuration {filename} deleted successfully",
    }
