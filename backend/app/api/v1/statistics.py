"""
Dashboard Statistics API endpoints
"""
from typing import Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.database import get_db
from app.api.deps import get_current_user, get_organization_id
from app.models.user import User
from app.models.device import Device
from app.models.configuration import Configuration
from app.models.backup_job import BackupJob
from app.repositories.device import DeviceRepository
from app.repositories.configuration import ConfigurationRepository
from app.repositories.backup_job import BackupJobRepository
from pydantic import BaseModel


router = APIRouter()


class DashboardStats(BaseModel):
    """Dashboard statistics response"""
    devices: Dict[str, Any]
    backups: Dict[str, Any]
    jobs: Dict[str, Any]
    storage: Dict[str, Any]
    recent_activity: Dict[str, Any]


@router.get("/dashboard", response_model=DashboardStats)
def get_dashboard_statistics(
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get comprehensive dashboard statistics for the organization

    Args:
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        DashboardStats: Aggregated statistics
    """
    device_repo = DeviceRepository(db)
    config_repo = ConfigurationRepository(db)
    job_repo = BackupJobRepository(db)

    # Device statistics
    total_devices = device_repo.count_by_organization(organization_id)
    active_devices = device_repo.count_by_organization(organization_id, is_active=True)
    inactive_devices = total_devices - active_devices

    # Backup statistics
    total_backups = config_repo.count_by_organization(organization_id)
    successful_backups = config_repo.count_by_organization(organization_id, status="success")
    failed_backups = config_repo.count_by_organization(organization_id, status="failed")

    # Recent backup activity (last 24 hours)
    last_24h = datetime.utcnow() - timedelta(hours=24)
    recent_successful = db.query(Configuration).join(Device).filter(
        Device.organization_id == organization_id,
        Configuration.backed_up_at >= last_24h,
        Configuration.status == "success",
    ).count()

    recent_failed = db.query(Configuration).join(Device).filter(
        Device.organization_id == organization_id,
        Configuration.backed_up_at >= last_24h,
        Configuration.status == "failed",
    ).count()

    # Backup job statistics
    total_jobs = job_repo.count_by_organization(organization_id)
    enabled_jobs = job_repo.count_by_organization(organization_id, is_enabled=True)

    # Storage statistics
    storage_result = db.query(
        func.sum(Configuration.file_size).label("total_size"),
        func.avg(Configuration.file_size).label("avg_size"),
    ).join(Device).filter(
        Device.organization_id == organization_id
    ).first()

    total_storage = storage_result.total_size or 0
    avg_backup_size = storage_result.avg_size or 0

    # Device type breakdown
    device_types = db.query(
        Device.device_type,
        func.count(Device.id).label("count")
    ).filter(
        Device.organization_id == organization_id
    ).group_by(Device.device_type).all()

    device_type_breakdown = {dt: count for dt, count in device_types}

    # Recent backups (last 10)
    recent_backups = db.query(Configuration).join(Device).filter(
        Device.organization_id == organization_id
    ).order_by(Configuration.backed_up_at.desc()).limit(10).all()

    recent_activity_list = []
    for backup in recent_backups:
        recent_activity_list.append({
            "config_id": backup.id,
            "device_id": backup.device_id,
            "device_hostname": backup.device.hostname if backup.device else None,
            "backed_up_at": backup.backed_up_at.isoformat(),
            "status": backup.status,
            "file_size": backup.file_size,
            "duration": backup.backup_duration,
        })

    # Success rate
    success_rate = (
        (successful_backups / total_backups * 100) if total_backups > 0 else 0
    )

    return {
        "devices": {
            "total": total_devices,
            "active": active_devices,
            "inactive": inactive_devices,
            "by_type": device_type_breakdown,
        },
        "backups": {
            "total": total_backups,
            "successful": successful_backups,
            "failed": failed_backups,
            "success_rate": round(success_rate, 2),
            "last_24h": {
                "successful": recent_successful,
                "failed": recent_failed,
                "total": recent_successful + recent_failed,
            },
        },
        "jobs": {
            "total": total_jobs,
            "enabled": enabled_jobs,
            "disabled": total_jobs - enabled_jobs,
        },
        "storage": {
            "total_bytes": int(total_storage),
            "total_mb": round(total_storage / (1024 * 1024), 2),
            "total_gb": round(total_storage / (1024 * 1024 * 1024), 2),
            "avg_backup_bytes": int(avg_backup_size),
            "avg_backup_mb": round(avg_backup_size / (1024 * 1024), 2),
        },
        "recent_activity": {
            "items": recent_activity_list,
            "count": len(recent_activity_list),
        },
    }


@router.get("/backup-trends")
def get_backup_trends(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get backup trends over time

    Args:
        days: Number of days to analyze
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Backup trends data for charting
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    # Query backup counts by day
    daily_stats = db.query(
        func.date(Configuration.backed_up_at).label("date"),
        func.count(Configuration.id).label("total"),
        func.sum(func.case(
            (Configuration.status == "success", 1),
            else_=0
        )).label("successful"),
        func.sum(func.case(
            (Configuration.status == "failed", 1),
            else_=0
        )).label("failed"),
    ).join(Device).filter(
        Device.organization_id == organization_id,
        Configuration.backed_up_at >= start_date
    ).group_by(
        func.date(Configuration.backed_up_at)
    ).order_by(
        func.date(Configuration.backed_up_at)
    ).all()

    trends = []
    for stat in daily_stats:
        trends.append({
            "date": stat.date.isoformat(),
            "total": stat.total,
            "successful": stat.successful,
            "failed": stat.failed,
        })

    return {
        "period": {
            "start_date": start_date.date().isoformat(),
            "end_date": datetime.utcnow().date().isoformat(),
            "days": days,
        },
        "trends": trends,
        "summary": {
            "total_backups": sum(t["total"] for t in trends),
            "total_successful": sum(t["successful"] for t in trends),
            "total_failed": sum(t["failed"] for t in trends),
            "avg_per_day": round(
                sum(t["total"] for t in trends) / len(trends) if trends else 0,
                2
            ),
        },
    }


@router.get("/device-health")
def get_device_health(
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get device health status based on recent backups

    Args:
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Device health statistics
    """
    device_repo = DeviceRepository(db)

    # Get all devices
    devices = device_repo.get_by_organization(organization_id, skip=0, limit=1000)

    now = datetime.utcnow()
    healthy = 0
    warning = 0
    critical = 0
    unknown = 0

    device_health_list = []

    for device in devices:
        if not device.last_backup_at:
            status = "unknown"
            unknown += 1
        elif device.last_backup_status == "failed":
            status = "critical"
            critical += 1
        else:
            # Check time since last backup
            time_since_backup = (now - device.last_backup_at).total_seconds() / 3600  # hours

            if time_since_backup <= 24:
                status = "healthy"
                healthy += 1
            elif time_since_backup <= 72:
                status = "warning"
                warning += 1
            else:
                status = "critical"
                critical += 1

        device_health_list.append({
            "device_id": device.id,
            "hostname": device.hostname,
            "status": status,
            "last_backup_at": device.last_backup_at.isoformat() if device.last_backup_at else None,
            "last_backup_status": device.last_backup_status,
        })

    return {
        "summary": {
            "total_devices": len(devices),
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "unknown": unknown,
        },
        "devices": device_health_list,
    }


@router.get("/storage-by-device")
def get_storage_by_device(
    limit: int = Query(10, ge=1, le=100, description="Number of devices to return"),
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get storage usage breakdown by device

    Args:
        limit: Number of top devices to return
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Storage usage by device
    """
    # Query storage usage per device
    storage_by_device = db.query(
        Device.id,
        Device.hostname,
        func.count(Configuration.id).label("backup_count"),
        func.sum(Configuration.file_size).label("total_size"),
        func.avg(Configuration.file_size).label("avg_size"),
    ).join(
        Configuration, Device.id == Configuration.device_id
    ).filter(
        Device.organization_id == organization_id
    ).group_by(
        Device.id, Device.hostname
    ).order_by(
        func.sum(Configuration.file_size).desc()
    ).limit(limit).all()

    devices = []
    for device in storage_by_device:
        devices.append({
            "device_id": device.id,
            "hostname": device.hostname,
            "backup_count": device.backup_count,
            "total_bytes": int(device.total_size),
            "total_mb": round(device.total_size / (1024 * 1024), 2),
            "avg_bytes": int(device.avg_size),
            "avg_mb": round(device.avg_size / (1024 * 1024), 2),
        })

    return {
        "devices": devices,
        "total_devices_analyzed": len(devices),
    }
