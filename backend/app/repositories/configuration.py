"""
Configuration repository
"""
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_
from app.models.configuration import Configuration
from app.models.device import Device
from app.repositories.base import BaseRepository


class ConfigurationRepository(BaseRepository[Configuration]):
    """Repository for Configuration (backup) operations"""

    def __init__(self, db: Session):
        super().__init__(Configuration, db)

    def get_by_device(
        self, device_id: int, skip: int = 0, limit: int = 100
    ) -> List[Configuration]:
        """
        Get configurations for a device

        Args:
            device_id: Device ID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of configurations ordered by backup time (newest first)
        """
        return (
            self.db.query(Configuration)
            .filter(Configuration.device_id == device_id)
            .order_by(Configuration.backed_up_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_by_organization(
        self,
        organization_id: int,
        skip: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> List[Configuration]:
        """
        Get configurations for an organization (through device relationship)

        Args:
            organization_id: Organization ID (tenant scope)
            skip: Number of records to skip
            limit: Maximum number of records
            status: Filter by backup status

        Returns:
            List of configurations
        """
        query = (
            self.db.query(Configuration)
            .join(Device)
            .filter(Device.organization_id == organization_id)
        )

        if status:
            query = query.filter(Configuration.status == status)

        return (
            query.order_by(Configuration.backed_up_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_latest_by_device(self, device_id: int) -> Optional[Configuration]:
        """
        Get the most recent configuration for a device

        Args:
            device_id: Device ID

        Returns:
            Latest configuration or None
        """
        return (
            self.db.query(Configuration)
            .filter(Configuration.device_id == device_id)
            .order_by(Configuration.backed_up_at.desc())
            .first()
        )

    def count_by_device(self, device_id: int) -> int:
        """
        Count configurations for a device

        Args:
            device_id: Device ID

        Returns:
            Configuration count
        """
        return (
            self.db.query(Configuration)
            .filter(Configuration.device_id == device_id)
            .count()
        )

    def count_by_organization(
        self, organization_id: int, status: Optional[str] = None
    ) -> int:
        """
        Count configurations for an organization

        Args:
            organization_id: Organization ID (tenant scope)
            status: Filter by backup status

        Returns:
            Configuration count
        """
        query = (
            self.db.query(Configuration)
            .join(Device)
            .filter(Device.organization_id == organization_id)
        )

        if status:
            query = query.filter(Configuration.status == status)

        return query.count()

    def get_successful_last_24h(self, organization_id: int) -> int:
        """
        Count successful backups in last 24 hours for organization

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            Count of successful backups
        """
        cutoff = datetime.utcnow() - timedelta(hours=24)
        return (
            self.db.query(Configuration)
            .join(Device)
            .filter(
                Device.organization_id == organization_id,
                Configuration.backed_up_at >= cutoff,
                Configuration.status == "success",
            )
            .count()
        )

    def get_failed_last_24h(self, organization_id: int) -> int:
        """
        Count failed backups in last 24 hours for organization

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            Count of failed backups
        """
        cutoff = datetime.utcnow() - timedelta(hours=24)
        return (
            self.db.query(Configuration)
            .join(Device)
            .filter(
                Device.organization_id == organization_id,
                Configuration.backed_up_at >= cutoff,
                Configuration.status == "failed",
            )
            .count()
        )

    def get_by_hash(self, config_hash: str, device_id: int) -> Optional[Configuration]:
        """
        Find configuration by hash (for deduplication)

        Args:
            config_hash: Configuration hash
            device_id: Device ID

        Returns:
            Configuration with matching hash or None
        """
        return (
            self.db.query(Configuration)
            .filter(
                Configuration.config_hash == config_hash,
                Configuration.device_id == device_id,
            )
            .first()
        )

    def delete_old_configs(
        self, device_id: int, keep_count: int
    ) -> int:
        """
        Delete old configurations beyond retention count

        Args:
            device_id: Device ID
            keep_count: Number of configs to keep

        Returns:
            Number of deleted configurations
        """
        # Get configurations to delete (oldest ones beyond keep_count)
        to_delete = (
            self.db.query(Configuration.id)
            .filter(Configuration.device_id == device_id)
            .order_by(Configuration.backed_up_at.desc())
            .offset(keep_count)
            .all()
        )

        if not to_delete:
            return 0

        delete_ids = [config[0] for config in to_delete]
        deleted = (
            self.db.query(Configuration)
            .filter(Configuration.id.in_(delete_ids))
            .delete(synchronize_session=False)
        )
        self.db.commit()

        return deleted
