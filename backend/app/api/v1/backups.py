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

    # Verify all devices belong to user's organization
    for device_id in request.device_ids:
        device = device_repo.get_by_id_and_organization(device_id, organization_id)
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Device {device_id} not found or access denied",
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

    response = {
        "task_id": task_id,
        "status": task_result.state,
        "result": None,
        "error": None,
        "progress": None,
    }

    if task_result.successful():
        response["result"] = task_result.result
    elif task_result.failed():
        response["error"] = str(task_result.info)
    elif task_result.state == "PROGRESS":
        response["progress"] = task_result.info.get("progress", 0)

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
        # Get configurations for specific device
        # Verify device belongs to organization
        device = device_repo.get_by_id_and_organization(device_id, organization_id)
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found",
            )

        configurations = config_repo.get_by_device(device_id, skip, limit)
        total = config_repo.count_by_device(device_id)
    else:
        # Get all configurations for organization
        configurations = config_repo.get_by_organization(
            organization_id, skip, limit, status
        )
        total = config_repo.count_by_organization(organization_id, status)

    # Enrich with device information
    response_items = []
    for config in configurations:
        config_dict = config.__dict__.copy()
        config_dict["device_hostname"] = config.device.hostname if config.device else None
        config_dict["device_ip"] = config.device.ip_address if config.device else None
        response_items.append(config_dict)

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
    device_repo = DeviceRepository(db)

    config = config_repo.get(config_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    # Verify device belongs to organization
    device = device_repo.get_by_id_and_organization(config.device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Enrich with device info
    config_dict = config.__dict__.copy()
    config_dict["device_hostname"] = device.hostname
    config_dict["device_ip"] = device.ip_address

    return config_dict


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
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    config = config_repo.get(config_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    # Verify device belongs to organization
    device = device_repo.get_by_id_and_organization(config.device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Check if file exists
    try:
        content = storage_service.get_config(config.file_path)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Configuration file not found: {str(e)}",
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
    device_repo = DeviceRepository(db)
    audit_repo = AuditLogRepository(db)

    config = config_repo.get(config_id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Configuration not found",
        )

    # Verify device belongs to organization
    device = device_repo.get_by_id_and_organization(config.device_id, organization_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    filename = config.filename
    file_path = config.file_path

    # Delete from database
    config_repo.delete(config_id)

    # Delete file from storage
    try:
        import os
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        # Log but don't fail
        import logging
        logging.warning(f"Failed to delete config file {file_path}: {e}")

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
