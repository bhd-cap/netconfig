"""
Dashboard Statistics API endpoints
"""
from typing import Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from app.core.database import get_db
from app.api.deps import get_current_user, get_organization_id
from app.models.user import User
from app.models.device import Device
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

    This is the most frequently polled endpoint in the application. It used to
    issue ten separate queries plus one lazy load per recent-activity row; the
    counters are now folded into conditional aggregates, so it costs four.

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

    # 1: device totals and per-type breakdown
    device_stats = device_repo.count_grouped_by_status(organization_id)

    # 2: every backup counter plus storage totals
    backup_stats = config_repo.get_organization_summary(organization_id)

    # 3: job totals
    job_stats = job_repo.count_totals_by_organization(organization_id)

    # 4: recent activity, hostname read from the join
    recent_activity_list = config_repo.get_recent_activity(organization_id, limit=10)

    total_backups = backup_stats["total"]
    success_rate = (
        (backup_stats["successful"] / total_backups * 100) if total_backups else 0
    )

    total_storage = backup_stats["total_size"]
    avg_backup_size = backup_stats["avg_size"]

    return {
        "devices": {
            "total": device_stats["total"],
            "active": device_stats["active"],
            "inactive": device_stats["total"] - device_stats["active"],
            "by_type": device_stats["by_type"],
        },
        "backups": {
            "total": total_backups,
            "successful": backup_stats["successful"],
            "failed": backup_stats["failed"],
            "success_rate": round(success_rate, 2),
            "last_24h": {
                "successful": backup_stats["recent_successful"],
                "failed": backup_stats["recent_failed"],
                "total": backup_stats["recent_successful"]
                + backup_stats["recent_failed"],
            },
        },
        "jobs": {
            "total": job_stats["total"],
            "enabled": job_stats["enabled"],
            "disabled": job_stats["total"] - job_stats["enabled"],
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
    config_repo = ConfigurationRepository(db)

    # The per-day success/failure split is computed with aggregate FILTER
    # clauses. The previous implementation called func.case(...), which emits a
    # call to a SQL function literally named "case" - Postgres has no such
    # function, so this endpoint raised as soon as it was hit.
    trends = config_repo.get_daily_trends(organization_id, days)

    total = sum(t["total"] for t in trends)
    start_date = datetime.utcnow().date()

    if trends:
        start_date = datetime.fromisoformat(trends[0]["date"]).date()

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": datetime.utcnow().date().isoformat(),
            "days": days,
        },
        "trends": trends,
        "summary": {
            "total_backups": total,
            "total_successful": sum(t["successful"] for t in trends),
            "total_failed": sum(t["failed"] for t in trends),
            "avg_per_day": round(total / len(trends), 2) if trends else 0,
        },
    }


@router.get("/device-health")
def get_device_health(
    limit: int = Query(500, ge=1, le=2000, description="Maximum devices to list"),
    current_user: User = Depends(get_current_user),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get device health status based on recent backups

    The summary counts are aggregated in the database over all devices; the
    per-device list selects three columns rather than loading whole Device
    entities (encrypted credentials included) to bucket them in Python.

    Args:
        limit: Maximum devices to include in the detail list
        current_user: Current authenticated user
        organization_id: Organization ID (from token)
        db: Database session

    Returns:
        dict: Device health statistics
    """
    now = datetime.now(timezone.utc)

    # Health is defined by last_backup_status and the age of last_backup_at:
    # never backed up -> unknown, failed -> critical, under 24h -> healthy,
    # under 72h -> warning, older -> critical.
    #
    # Age is computed against the database's own now() so that the comparison
    # stays in one timezone-aware clock; last_backup_at is a timestamptz and
    # passing a client-side naive datetime would have it silently reinterpreted
    # in whatever timezone the session happens to be set to.
    age_hours = func.extract("epoch", func.now() - Device.last_backup_at) / 3600.0

    is_unknown = Device.last_backup_at.is_(None)
    is_failed = (~is_unknown) & (Device.last_backup_status == "failed")
    is_healthy = (~is_unknown) & (~is_failed) & (age_hours <= 24)
    is_warning = (~is_unknown) & (~is_failed) & (age_hours > 24) & (age_hours <= 72)
    is_critical = is_failed | ((~is_unknown) & (~is_failed) & (age_hours > 72))

    summary_row = db.execute(
        select(
            func.count(Device.id).label("total"),
            func.count(Device.id).filter(is_healthy).label("healthy"),
            func.count(Device.id).filter(is_warning).label("warning"),
            func.count(Device.id).filter(is_critical).label("critical"),
            func.count(Device.id).filter(is_unknown).label("unknown"),
        ).where(Device.organization_id == organization_id)
    ).one()

    rows = db.execute(
        select(
            Device.id,
            Device.hostname,
            Device.last_backup_at,
            Device.last_backup_status,
        )
        .where(Device.organization_id == organization_id)
        .order_by(Device.hostname)
        .limit(limit)
    ).all()

    device_health_list = []

    for row in rows:
        if row.last_backup_at is None:
            status = "unknown"
        elif row.last_backup_status == "failed":
            status = "critical"
        else:
            last_backup_at = row.last_backup_at
            if last_backup_at.tzinfo is None:
                last_backup_at = last_backup_at.replace(tzinfo=timezone.utc)

            hours_since = (now - last_backup_at).total_seconds() / 3600

            if hours_since <= 24:
                status = "healthy"
            elif hours_since <= 72:
                status = "warning"
            else:
                status = "critical"

        device_health_list.append(
            {
                "device_id": row.id,
                "hostname": row.hostname,
                "status": status,
                "last_backup_at": (
                    row.last_backup_at.isoformat() if row.last_backup_at else None
                ),
                "last_backup_status": row.last_backup_status,
            }
        )

    return {
        "summary": {
            "total_devices": summary_row.total,
            "healthy": summary_row.healthy,
            "warning": summary_row.warning,
            "critical": summary_row.critical,
            "unknown": summary_row.unknown,
        },
        "devices": device_health_list,
        "truncated": summary_row.total > len(device_health_list),
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
    config_repo = ConfigurationRepository(db)

    devices = [
        {
            **row,
            "total_mb": round(row["total_bytes"] / (1024 * 1024), 2),
            "avg_mb": round(row["avg_bytes"] / (1024 * 1024), 2),
        }
        for row in config_repo.get_storage_by_device(organization_id, limit)
    ]

    return {
        "devices": devices,
        "total_devices_analyzed": len(devices),
    }
