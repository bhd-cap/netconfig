"""
Backup Job Scheduler API endpoints
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime
from croniter import croniter

from app.config.discovery_commands import SUPPORTED_TRANSPORTS
from app.core.database import get_db
from app.api.deps import get_current_user, get_current_admin_user, get_organization_id
from app.models.user import User
from app.repositories.backup_job import BackupJobRepository
from app.repositories.audit_log import AuditLogRepository
from app.schemas.backup_job import (
    BackupJobCreate,
    BackupJobUpdate,
    BackupJobResponse,
)
from app.schemas.common import PaginatedResponse
from app.services import device_filter as device_filter_service

router = APIRouter()


class DeviceFilterPreviewRequest(BaseModel):
    """Ask which devices a filter would select"""

    device_filter: Optional[Dict[str, Any]] = Field(
        None, description="The filter to try; every backable device when omitted"
    )
    limit: int = Field(100, ge=1, le=1000, description="How many devices to name")


def _validated_filter(raw) -> dict:
    """
    Normalise a job's device filter, or reject it

    Validating here rather than at run time means a typo surfaces while
    someone is looking at the form, not as a job that quietly backs up the
    wrong set of devices at 2am.
    """
    try:
        return device_filter_service.validate(raw)
    except device_filter_service.FilterError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )


@router.post("", response_model=BackupJobResponse, status_code=status.HTTP_201_CREATED)
def create_backup_job(
    job_in: BackupJobCreate,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Create a new scheduled backup job (Admin only)

    Args:
        job_in: Job creation data
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        BackupJobResponse: Created job

    Raises:
        HTTPException: If cron syntax is invalid
    """
    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    # Validate cron syntax
    try:
        base_time = datetime.utcnow()
        cron = croniter(job_in.schedule_cron, base_time)
        next_run = cron.get_next(datetime)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid cron syntax: {str(e)}",
        )

    # Check for duplicate name
    existing = job_repo.get_by_name_and_organization(job_in.name, organization_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job with name '{job_in.name}' already exists",
        )

    # Create job
    job_data = job_in.model_dump()
    job_data["device_filter"] = _validated_filter(job_data.get("device_filter"))
    job_data["organization_id"] = organization_id
    job_data["next_run_at"] = next_run
    job_data["created_by"] = current_user.id

    job = job_repo.create(job_data)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_created",
        resource_type="backup_job",
        resource_id=job.id,
        details={
            "job_name": job.name,
            "schedule": job.schedule_cron,
            "next_run": next_run.isoformat(),
            "device_filter": device_filter_service.describe(job.device_filter),
            "devices_matched": device_filter_service.count(
                db, organization_id, job.device_filter
            ),
        },
    )

    return job


@router.get("", response_model=PaginatedResponse[BackupJobResponse])
def list_backup_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    is_enabled: Optional[bool] = Query(None),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List backup jobs for the organization

    Args:
        skip: Number of records to skip
        limit: Maximum number of records
        is_enabled: Filter by enabled status (optional)
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        PaginatedResponse: Paginated list of jobs
    """
    job_repo = BackupJobRepository(db)

    jobs = job_repo.get_by_organization(organization_id, skip, limit, is_enabled)
    total = job_repo.count_by_organization(organization_id, is_enabled)

    return {
        "total": total,
        "page": (skip // limit) + 1,
        "page_size": limit,
        "total_pages": (total + limit - 1) // limit,
        "items": jobs,
    }


@router.post("/preview-filter")
def preview_device_filter(
    request: DeviceFilterPreviewRequest,
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Show which devices a filter would select, before saving a job

    Declared before /{job_id} so the literal path is matched first.
    """
    try:
        return device_filter_service.preview(
            db, organization_id, request.device_filter, limit=request.limit
        )
    except device_filter_service.FilterError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )


@router.get("/filter-options")
def device_filter_options(
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    The values a device filter can be built from

    Only device types, locations and tag keys actually present in this
    organization are offered, so the editor cannot suggest a criterion that
    would match nothing.
    """
    from sqlalchemy import distinct, select as sa_select

    from app.models.device import Device

    device_types = sorted(
        value
        for value in db.scalars(
            sa_select(distinct(Device.device_type)).where(
                Device.organization_id == organization_id
            )
        ).all()
        if value
    )

    locations = sorted(
        value
        for value in db.scalars(
            sa_select(distinct(Device.location)).where(
                Device.organization_id == organization_id
            )
        ).all()
        if value
    )

    # Tag keys live inside a JSONB column, so they come from the rows rather
    # than from a catalogue. Only the keys are collected; a value list would
    # be unbounded.
    tag_keys = set()
    for tags in db.scalars(
        sa_select(Device.tags).where(
            Device.organization_id == organization_id,
            Device.tags.isnot(None),
        )
    ).all():
        if isinstance(tags, dict):
            tag_keys.update(str(key) for key in tags)

    return {
        "device_types": device_types,
        "locations": locations,
        "tag_keys": sorted(tag_keys),
        "transports": list(SUPPORTED_TRANSPORTS),
        "filter_keys": sorted(device_filter_service.ALLOWED_KEYS),
    }


@router.get("/{job_id}", response_model=BackupJobResponse)
def get_backup_job(
    job_id: int,
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get backup job details

    Args:
        job_id: Job ID
        organization_id: Organization ID (from token)
        db: Database session
        current_user: Current user

    Returns:
        BackupJobResponse: Job details

    Raises:
        HTTPException: If job not found or access denied
    """
    job_repo = BackupJobRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return job


@router.put("/{job_id}", response_model=BackupJobResponse)
def update_backup_job(
    job_id: int,
    job_in: BackupJobUpdate,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Update backup job (Admin only)

    Args:
        job_id: Job ID
        job_in: Job update data
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        BackupJobResponse: Updated job

    Raises:
        HTTPException: If job not found, access denied, or invalid data
    """
    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # If cron schedule is being updated, validate it
    update_data = job_in.model_dump(exclude_unset=True)
    if "schedule_cron" in update_data:
        try:
            base_time = datetime.utcnow()
            cron = croniter(update_data["schedule_cron"], base_time)
            next_run = cron.get_next(datetime)
            update_data["next_run_at"] = next_run
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron syntax: {str(e)}",
            )

    # Normalise the device filter if it is being changed. Sending null clears
    # it back to "every device that can be backed up".
    if "device_filter" in update_data:
        update_data["device_filter"] = _validated_filter(update_data["device_filter"])

    # Check for duplicate name (if name is being changed)
    if "name" in update_data and update_data["name"] != job.name:
        existing = job_repo.get_by_name_and_organization(update_data["name"], organization_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job with name '{update_data['name']}' already exists",
            )

    # Update job
    updated_job = job_repo.update(job, update_data)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_updated",
        resource_type="backup_job",
        resource_id=job.id,
        details={
            "job_name": updated_job.name,
            "changes": list(update_data.keys()),
        },
    )

    return updated_job


@router.delete("/{job_id}")
def delete_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Delete backup job (Admin only)

    Args:
        job_id: Job ID
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Success message

    Raises:
        HTTPException: If job not found or access denied
    """
    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    job_name = job.name

    # Delete job
    job_repo.delete(job_id)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_deleted",
        resource_type="backup_job",
        resource_id=job_id,
        details={
            "job_name": job_name,
        },
    )

    return {
        "success": True,
        "message": f"Backup job '{job_name}' deleted successfully",
    }


@router.post("/{job_id}/enable")
def enable_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Enable a backup job (Admin only)

    Args:
        job_id: Job ID
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        BackupJobResponse: Updated job

    Raises:
        HTTPException: If job not found or access denied
    """
    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Enable job
    updated_job = job_repo.update(job, {"is_enabled": True})

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_enabled",
        resource_type="backup_job",
        resource_id=job.id,
        details={
            "job_name": job.name,
        },
    )

    return updated_job


@router.post("/{job_id}/disable")
def disable_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Disable a backup job (Admin only)

    Args:
        job_id: Job ID
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        BackupJobResponse: Updated job

    Raises:
        HTTPException: If job not found or access denied
    """
    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Disable job
    updated_job = job_repo.update(job, {"is_enabled": False})

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_disabled",
        resource_type="backup_job",
        resource_id=job.id,
        details={
            "job_name": job.name,
        },
    )

    return updated_job


@router.get("/{job_id}/devices")
def list_backup_job_devices(
    job_id: int,
    limit: int = Query(200, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Which devices a saved job currently covers

    Resolved live rather than stored, so a device added or retagged after the
    job was saved is reflected without editing the job.
    """
    job_repo = BackupJobRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    try:
        result = device_filter_service.preview(
            db, organization_id, job.device_filter, limit=limit
        )
    except device_filter_service.FilterError as e:
        # A stored filter that no longer validates: report it rather than
        # pretending the job covers everything.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This job's device filter is no longer valid: {e}",
        )

    result["job_id"] = job.id
    result["job_name"] = job.name
    return result


@router.post("/{job_id}/run-now", response_model=dict)
def run_backup_job_now(
    job_id: int,
    current_user: User = Depends(get_current_admin_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Manually trigger a backup job to run immediately (Admin only)

    Args:
        job_id: Job ID
        current_user: Current authenticated admin user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Task information

    Raises:
        HTTPException: If job not found or access denied
    """
    from app.tasks.backup import scheduled_backup_task

    job_repo = BackupJobRepository(db)
    audit_repo = AuditLogRepository(db)

    job = job_repo.get(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup job not found",
        )

    # Verify job belongs to organization
    if job.organization_id != organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Trigger job
    task = scheduled_backup_task.delay(job_id)

    # Log action
    audit_repo.log_action(
        user_id=current_user.id,
        action="backup_job_triggered_manually",
        resource_type="backup_job",
        resource_id=job.id,
        details={
            "job_name": job.name,
            "task_id": task.id,
        },
    )

    return {
        "task_id": task.id,
        "job_id": job_id,
        "job_name": job.name,
        "message": f"Job '{job.name}' triggered successfully",
    }
