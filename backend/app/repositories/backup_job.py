"""
BackupJob repository
"""
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func, select, update as sql_update
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
            query = query.filter(BackupJob.is_enabled.is_(True))

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
        return list(
            self.db.scalars(
                select(BackupJob).where(BackupJob.is_enabled.is_(True))
            ).all()
        )

    def get_jobs_due(self, current_time: datetime) -> List[BackupJob]:
        """
        Get jobs that are due to run

        Args:
            current_time: Current datetime

        Returns:
            List of jobs due for execution
        """
        return list(
            self.db.scalars(
                select(BackupJob).where(
                    BackupJob.is_enabled.is_(True),
                    BackupJob.next_run_at <= current_time,
                )
            ).all()
        )

    def get_due_job_identifiers(self, current_time: datetime) -> List[tuple]:
        """
        Get (id, name) of jobs due to run

        This runs once a minute forever, so it selects two columns instead of
        hydrating whole BackupJob entities (including their JSONB filters)
        just to read an ID off each one.

        Args:
            current_time: Current datetime

        Returns:
            List of (job_id, job_name) tuples
        """
        return [
            (row.id, row.name)
            for row in self.db.execute(
                select(BackupJob.id, BackupJob.name).where(
                    BackupJob.is_enabled.is_(True),
                    BackupJob.next_run_at <= current_time,
                )
            ).all()
        ]

    def update_last_run(
        self, job_id: int, last_run: datetime, next_run: Optional[datetime]
    ) -> None:
        """
        Update job's last and next run times

        Issues a targeted UPDATE rather than loading the row, mutating it and
        re-reading it.

        Args:
            job_id: Job ID
            last_run: Last run timestamp
            next_run: Next scheduled run timestamp
        """
        self.db.execute(
            sql_update(BackupJob)
            .where(BackupJob.id == job_id)
            .values(last_run_at=last_run, next_run_at=next_run)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()

    def count_totals_by_organization(self, organization_id: int) -> dict:
        """
        Get total and enabled job counts in a single query

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            dict with 'total' and 'enabled'
        """
        row = self.db.execute(
            select(
                func.count(BackupJob.id),
                func.count(BackupJob.id).filter(BackupJob.is_enabled.is_(True)),
            ).where(BackupJob.organization_id == organization_id)
        ).one()

        return {"total": row[0], "enabled": row[1]}

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
        stmt = select(func.count(BackupJob.id)).where(
            BackupJob.organization_id == organization_id
        )

        if is_enabled is not None:
            stmt = stmt.where(BackupJob.is_enabled.is_(is_enabled))

        return self.db.scalar(stmt) or 0

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
