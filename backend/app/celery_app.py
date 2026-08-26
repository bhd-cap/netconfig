"""
Celery application configuration
"""
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

# Create Celery application
celery_app = Celery(
    "netconfig_backup",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.backup", "app.tasks.cleanup"],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

# Celery Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    # Check for scheduled backup jobs every minute
    "check-scheduled-jobs": {
        "task": "app.tasks.backup.check_scheduled_jobs_task",
        "schedule": 60.0,  # Run every 60 seconds
    },
    # Cleanup old backups daily at 3 AM
    "cleanup-old-backups": {
        "task": "app.tasks.cleanup.cleanup_old_backups_task",
        "schedule": crontab(hour=3, minute=0),
    },
}

if __name__ == "__main__":
    celery_app.start()
