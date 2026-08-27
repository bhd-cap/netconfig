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
    include=["app.tasks.backup", "app.tasks.cleanup", "app.tasks.discovery"],
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
    # A backup task is long and I/O bound, so hand out one at a time and let
    # idle workers steal work instead of sitting on a prefetched queue.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
    # Recycle a worker that grows past this (KiB). Netmiko/paramiko sessions
    # and large configuration strings fragment the heap over time; without a
    # ceiling a long-lived worker's RSS only ever climbs.
    worker_max_memory_per_child=250_000,
    # Expire results after a day rather than keeping them in Redis forever.
    result_expires=86_400,
    # Bound the connection pools each process opens to Redis.
    broker_pool_limit=10,
    redis_max_connections=20,
    broker_connection_retry_on_startup=True,
)

# Celery Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    # Check for scheduled backup jobs every minute
    "check-scheduled-jobs": {
        "task": "app.tasks.backup.check_scheduled_jobs_task",
        "schedule": 60.0,  # Run every 60 seconds
        "options": {"expires": 55},
    },
    # Cleanup old backups daily at 3 AM
    "cleanup-old-backups": {
        "task": "app.tasks.cleanup.cleanup_old_backups_task",
        "schedule": crontab(hour=3, minute=0),
        "options": {"expires": 3600},
    },
    # Mark adjacencies and hosts that have stopped being seen. Hourly, so
    # last_seen stays meaningful without a full crawl.
    "age-inventory": {
        "task": "app.tasks.discovery.age_inventory_task",
        "schedule": crontab(minute=20),
        "options": {"expires": 3000},
    },
}

if __name__ == "__main__":
    celery_app.start()
