"""
BackupJob repository
"""
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.backup_job import BackupJob
from app.repositories.base import BaseRepository


class BackupJobRepository(BaseRepository[BackupJob]):
    """Repository for BackupJob operations"""

    def __init__(self, db: Session):
        super().__init__(BackupJob, db)

    def get_by_organization(
        self,
        organization_id: int,
        skip: int = 0,
        limit: int = 100,
        enabled_only: bool = False,
    ) -> List[BackupJob]:
        """
        Get backup jobs by organization

        Args:
            organization_id: Organization ID (tenant scope)
            skip: Number of records to skip
            limit: Maximum number of records
            enabled_only: Only return enabled jobs

        Returns:
            List of backup jobs
        """
        query = self.db.query(BackupJob).filter(
            BackupJob.organization_id == organization_id
        )

        if enabled_only:
            query = query.filter(BackupJob.is_enabled == True)

        return query.order_by(BackupJob.name).offset(skip).limit(limit).all()

    def get_by_id_and_organization(
        self, id: int, organization_id: int
    ) -> Optional[BackupJob]:
        """
        Get backup job by ID with organization scope

        Args:
            id: Job ID
            organization_id: Organization ID (tenant scope)

        Returns:
            BackupJob instance or None
        """
        return (
            self.db.query(BackupJob)
            .filter(
                BackupJob.id == id,
                BackupJob.organization_id == organization_id,
            )
            .first()
        )

    def get_enabled_jobs(self) -> List[BackupJob]:
        """
        Get all enabled backup jobs (for scheduler)

        Returns:
            List of enabled backup jobs
        """
        return self.db.query(BackupJob).filter(BackupJob.is_enabled == True).all()

    def get_jobs_due(self, current_time: datetime) -> List[BackupJob]:
        """
        Get jobs that are due to run

        Args:
            current_time: Current datetime

        Returns:
            List of jobs due for execution
        """
        return (
            self.db.query(BackupJob)
            .filter(
                BackupJob.is_enabled == True,
                BackupJob.next_run_at <= current_time,
            )
            .all()
        )

    def update_last_run(
        self, job_id: int, last_run: datetime, next_run: datetime
    ) -> Optional[BackupJob]:
        """
        Update job's last and next run times

        Args:
            job_id: Job ID
            last_run: Last run timestamp
            next_run: Next scheduled run timestamp

        Returns:
            Updated job or None
        """
        job = self.get(job_id)
        if job:
            job.last_run_at = last_run
            job.next_run_at = next_run
            self.db.commit()
            self.db.refresh(job)
        return job

    def count_by_organization(
        self, organization_id: int, is_enabled: Optional[bool] = None
    ) -> int:
        """
        Count backup jobs in organization

        Args:
            organization_id: Organization ID (tenant scope)
            is_enabled: Filter by enabled status (optional)

        Returns:
            Job count
        """
        query = self.db.query(BackupJob).filter(
            BackupJob.organization_id == organization_id
        )

        if is_enabled is not None:
            query = query.filter(BackupJob.is_enabled == is_enabled)

        return query.count()

    def get_by_name_and_organization(
        self, name: str, organization_id: int
    ) -> Optional[BackupJob]:
        """
        Get backup job by name and organization

        Args:
            name: Job name
            organization_id: Organization ID (tenant scope)

        Returns:
            BackupJob instance or None
        """
        return (
            self.db.query(BackupJob)
            .filter(
                BackupJob.name == name,
                BackupJob.organization_id == organization_id,
            )
            .first()
        )
