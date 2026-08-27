"""
Configuration repository
"""
from typing import Optional, List, Sequence, Tuple
from datetime import datetime, timedelta
from sqlalchemy import func, select, delete as sql_delete
from sqlalchemy.orm import Session, contains_eager
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
        return list(
            self.db.scalars(
                select(Configuration)
                .where(Configuration.device_id == device_id)
                .order_by(Configuration.backed_up_at.desc())
                .offset(skip)
                .limit(limit)
            ).all()
        )

    def get_by_organization(
        self,
        organization_id: int,
        skip: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        device_id: Optional[int] = None,
        with_device: bool = False,
    ) -> List[Configuration]:
        """
        Get configurations for an organization (through device relationship)

        Args:
            organization_id: Organization ID (tenant scope)
            skip: Number of records to skip
            limit: Maximum number of records
            status: Filter by backup status
            device_id: Restrict to a single device
            with_device: Populate each configuration's `device` from the join
                that is already being performed, instead of leaving it to a
                lazy load per row

        Returns:
            List of configurations
        """
        stmt = (
            select(Configuration)
            .join(Configuration.device)
            .where(Device.organization_id == organization_id)
        )

        if status:
            stmt = stmt.where(Configuration.status == status)

        if device_id is not None:
            stmt = stmt.where(Configuration.device_id == device_id)

        if with_device:
            # The join is already in the statement; contains_eager reuses its
            # columns so the relationship costs nothing extra. Without this,
            # reading config.device.hostname for a page of 20 rows fires 20
            # additional SELECTs.
            stmt = stmt.options(contains_eager(Configuration.device))

        stmt = (
            stmt.order_by(Configuration.backed_up_at.desc()).offset(skip).limit(limit)
        )

        return list(self.db.scalars(stmt).unique().all())

    def get_with_device(
        self, config_id: int
    ) -> Optional[Tuple[Configuration, Device]]:
        """
        Get a configuration together with its device in one query

        Args:
            config_id: Configuration ID

        Returns:
            Tuple of (configuration, device), or None if not found
        """
        row = self.db.execute(
            select(Configuration, Device)
            .join(Device, Configuration.device_id == Device.id)
            .where(Configuration.id == config_id)
        ).first()

        return (row[0], row[1]) if row else None

    def get_latest_by_device(self, device_id: int) -> Optional[Configuration]:
        """
        Get the most recent configuration for a device

        Args:
            device_id: Device ID

        Returns:
            Latest configuration or None
        """
        return self.db.scalars(
            select(Configuration)
            .where(Configuration.device_id == device_id)
            .order_by(Configuration.backed_up_at.desc())
            .limit(1)
        ).first()

    def get_latest_hash(self, device_id: int) -> Optional[str]:
        """
        Get the config hash of a device's most recent successful backup

        Args:
            device_id: Device ID

        Returns:
            Hash string, or None when there is no prior successful backup
        """
        return self.db.scalar(
            select(Configuration.config_hash)
            .where(
                Configuration.device_id == device_id,
                Configuration.status == "success",
            )
            .order_by(Configuration.backed_up_at.desc())
            .limit(1)
        )

    def get_latest_hashes(self, device_ids: Sequence[int]) -> dict:
        """
        Get the latest successful config hash for many devices at once

        Args:
            device_ids: Device IDs

        Returns:
            dict mapping device_id to hash, omitting devices with no
            successful backup yet
        """
        if not device_ids:
            return {}

        # DISTINCT ON gives one row per device - the newest - in a single
        # index scan, instead of one query per device.
        rows = self.db.execute(
            select(Configuration.device_id, Configuration.config_hash)
            .where(
                Configuration.device_id.in_(set(device_ids)),
                Configuration.status == "success",
            )
            .distinct(Configuration.device_id)
            .order_by(Configuration.device_id, Configuration.backed_up_at.desc())
        ).all()

        return {row.device_id: row.config_hash for row in rows if row.config_hash}

    def count_by_device(self, device_id: int) -> int:
        """
        Count configurations for a device

        Args:
            device_id: Device ID

        Returns:
            Configuration count
        """
        return (
            self.db.scalar(
                select(func.count(Configuration.id)).where(
                    Configuration.device_id == device_id
                )
            )
            or 0
        )

    def count_by_organization(
        self,
        organization_id: int,
        status: Optional[str] = None,
        device_id: Optional[int] = None,
    ) -> int:
        """
        Count configurations for an organization

        Args:
            organization_id: Organization ID (tenant scope)
            status: Filter by backup status
            device_id: Restrict to a single device

        Returns:
            Configuration count
        """
        stmt = (
            select(func.count(Configuration.id))
            .select_from(Configuration)
            .join(Device, Configuration.device_id == Device.id)
            .where(Device.organization_id == organization_id)
        )

        if status:
            stmt = stmt.where(Configuration.status == status)

        if device_id is not None:
            stmt = stmt.where(Configuration.device_id == device_id)

        return self.db.scalar(stmt) or 0

    def get_organization_summary(self, organization_id: int) -> dict:
        """
        Get every backup counter the dashboard needs in one query

        Replaces six separate COUNT/SUM statements (total, successful, failed,
        last-24h successful, last-24h failed, storage) with a single scan.

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            dict of aggregate values
        """
        cutoff = datetime.utcnow() - timedelta(hours=24)

        row = self.db.execute(
            select(
                func.count(Configuration.id).label("total"),
                func.count(Configuration.id)
                .filter(Configuration.status == "success")
                .label("successful"),
                func.count(Configuration.id)
                .filter(Configuration.status == "failed")
                .label("failed"),
                func.count(Configuration.id)
                .filter(
                    Configuration.backed_up_at >= cutoff,
                    Configuration.status == "success",
                )
                .label("recent_successful"),
                func.count(Configuration.id)
                .filter(
                    Configuration.backed_up_at >= cutoff,
                    Configuration.status == "failed",
                )
                .label("recent_failed"),
                func.coalesce(func.sum(Configuration.file_size), 0).label(
                    "total_size"
                ),
                func.coalesce(func.avg(Configuration.file_size), 0).label("avg_size"),
            )
            .select_from(Configuration)
            .join(Device, Configuration.device_id == Device.id)
            .where(Device.organization_id == organization_id)
        ).one()

        return {
            "total": row.total,
            "successful": row.successful,
            "failed": row.failed,
            "recent_successful": row.recent_successful,
            "recent_failed": row.recent_failed,
            "total_size": int(row.total_size or 0),
            "avg_size": float(row.avg_size or 0),
        }

    def get_recent_activity(self, organization_id: int, limit: int = 10) -> List[dict]:
        """
        Get the most recent backups with their device hostname

        Selects the specific columns the dashboard renders rather than whole
        ORM entities, and reads the hostname from the join instead of a lazy
        load per row.

        Args:
            organization_id: Organization ID (tenant scope)
            limit: Maximum rows to return

        Returns:
            List of activity dicts
        """
        rows = self.db.execute(
            select(
                Configuration.id,
                Configuration.device_id,
                Device.hostname,
                Configuration.backed_up_at,
                Configuration.status,
                Configuration.file_size,
                Configuration.backup_duration,
            )
            .join(Device, Configuration.device_id == Device.id)
            .where(Device.organization_id == organization_id)
            .order_by(Configuration.backed_up_at.desc())
            .limit(limit)
        ).all()

        return [
            {
                "config_id": row.id,
                "device_id": row.device_id,
                "device_hostname": row.hostname,
                "backed_up_at": row.backed_up_at.isoformat(),
                "status": row.status,
                "file_size": row.file_size,
                "duration": row.backup_duration,
            }
            for row in rows
        ]

    def get_daily_trends(self, organization_id: int, days: int) -> List[dict]:
        """
        Get per-day backup counts for the trends chart

        Aggregation happens in the database; the API previously shipped raw
        rows to the browser and grouped them there.

        Args:
            organization_id: Organization ID (tenant scope)
            days: Number of days to look back

        Returns:
            List of {date, total, successful, failed}, oldest first
        """
        start_date = datetime.utcnow() - timedelta(days=days)
        day = func.date(Configuration.backed_up_at)

        rows = self.db.execute(
            select(
                day.label("day"),
                func.count(Configuration.id).label("total"),
                func.count(Configuration.id)
                .filter(Configuration.status == "success")
                .label("successful"),
                func.count(Configuration.id)
                .filter(Configuration.status == "failed")
                .label("failed"),
            )
            .select_from(Configuration)
            .join(Device, Configuration.device_id == Device.id)
            .where(
                Device.organization_id == organization_id,
                Configuration.backed_up_at >= start_date,
            )
            .group_by(day)
            .order_by(day)
        ).all()

        return [
            {
                "date": row.day.isoformat(),
                "total": row.total,
                "successful": row.successful,
                "failed": row.failed,
            }
            for row in rows
        ]

    def get_storage_by_device(
        self, organization_id: int, limit: int = 10
    ) -> List[dict]:
        """
        Get storage usage per device, largest first

        Args:
            organization_id: Organization ID (tenant scope)
            limit: Maximum devices to return

        Returns:
            List of per-device storage dicts
        """
        total_size = func.coalesce(func.sum(Configuration.file_size), 0)

        rows = self.db.execute(
            select(
                Device.id,
                Device.hostname,
                func.count(Configuration.id).label("backup_count"),
                total_size.label("total_size"),
                func.coalesce(func.avg(Configuration.file_size), 0).label("avg_size"),
            )
            .select_from(Device)
            .join(Configuration, Configuration.device_id == Device.id)
            .where(Device.organization_id == organization_id)
            .group_by(Device.id, Device.hostname)
            .order_by(total_size.desc())
            .limit(limit)
        ).all()

        return [
            {
                "device_id": row.id,
                "hostname": row.hostname,
                "backup_count": row.backup_count,
                "total_bytes": int(row.total_size or 0),
                "avg_bytes": int(row.avg_size or 0),
            }
            for row in rows
        ]

    def get_by_hash(self, config_hash: str, device_id: int) -> Optional[Configuration]:
        """
        Find configuration by hash (for deduplication)

        Args:
            config_hash: Configuration hash
            device_id: Device ID

        Returns:
            Configuration with matching hash or None
        """
        return self.db.scalars(
            select(Configuration)
            .where(
                Configuration.config_hash == config_hash,
                Configuration.device_id == device_id,
            )
            .limit(1)
        ).first()

    def delete_old_configs(
        self, device_id: int, keep_count: int
    ) -> List[str]:
        """
        Delete old configurations beyond retention count

        Args:
            device_id: Device ID
            keep_count: Number of configs to keep

        Returns:
            File paths of the deleted records, so the caller can remove them
            from disk without a second query
        """
        # Only the id/path of the doomed rows is fetched, not whole entities.
        doomed = self.db.execute(
            select(Configuration.id, Configuration.file_path)
            .where(Configuration.device_id == device_id)
            .order_by(Configuration.backed_up_at.desc())
            .offset(keep_count)
        ).all()

        if not doomed:
            return []

        self.db.execute(
            sql_delete(Configuration)
            .where(Configuration.id.in_([row.id for row in doomed]))
            .execution_options(synchronize_session=False)
        )
        self.db.commit()

        return [row.file_path for row in doomed]

    def get_paths_for_devices(self, device_ids: Sequence[int]) -> List[str]:
        """
        Get stored file paths for a set of devices

        Args:
            device_ids: Device IDs

        Returns:
            List of file paths
        """
        if not device_ids:
            return []

        return list(
            self.db.scalars(
                select(Configuration.file_path).where(
                    Configuration.device_id.in_(set(device_ids))
                )
            ).all()
        )
