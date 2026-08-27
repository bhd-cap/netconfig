"""
Device repository with tenant scoping
"""
from typing import Optional, List, Sequence, Tuple
from datetime import datetime
from sqlalchemy import func, or_, select, update as sql_update
from sqlalchemy.orm import Session, load_only
from app.models.device import Device
from app.repositories.base import BaseRepository

# Columns needed to open an SSH session and record the result. Loading only
# these keeps large per-row text (description, tags, ssh key path) out of
# memory on the bulk backup paths.
_CONNECTION_COLUMNS = (
    Device.id,
    Device.organization_id,
    Device.hostname,
    Device.ip_address,
    Device.device_type,
    Device.port,
    Device.username,
    Device.encrypted_password,
    Device.enable_secret,
    Device.ssh_key_path,
)


# Columns the Devices page may sort on. Anything not here falls back to
# hostname rather than reaching the database.
SORTABLE_COLUMNS = {
    "hostname": Device.hostname,
    "ip_address": Device.ip_address,
    "device_type": Device.device_type,
    "location": Device.location,
    "transport": Device.transport,
    "is_active": Device.is_active,
    "last_auth_status": Device.last_auth_status,
    "last_backup_at": Device.last_backup_at,
    "last_backup_status": Device.last_backup_status,
    "last_discovered_at": Device.last_discovered_at,
    "model": Device.model,
    "os_version": Device.os_version,
    "created_at": Device.created_at,
}


class DeviceRepository(BaseRepository[Device]):
    """Repository for Device operations with multi-tenant support"""

    def __init__(self, db: Session):
        super().__init__(Device, db)

    def _scoped(self, organization_id: int):
        """Base SELECT scoped to one tenant"""
        return select(Device).where(Device.organization_id == organization_id)

    @staticmethod
    def _apply_filters(
        stmt,
        device_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ):
        """Apply the shared list/count filters to a statement"""
        if device_type:
            stmt = stmt.where(Device.device_type == device_type)

        if is_active is not None:
            stmt = stmt.where(Device.is_active.is_(is_active))

        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Device.hostname.ilike(pattern),
                    Device.ip_address.ilike(pattern),
                )
            )

        return stmt

    def get_by_organization(
        self,
        organization_id: int,
        skip: int = 0,
        limit: int = 100,
        device_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
        sort_by: str = "hostname",
        sort_dir: str = "asc",
    ) -> List[Device]:
        """
        Get devices by organization with filtering and sorting

        Args:
            organization_id: Organization ID (tenant scope)
            skip: Number of records to skip
            limit: Maximum number of records
            device_type: Filter by device type
            is_active: Filter by active status
            search: Search in hostname or IP address
            sort_by: Column to sort on; see SORTABLE_COLUMNS
            sort_dir: 'asc' or 'desc'

        Returns:
            List of devices
        """
        stmt = self._apply_filters(
            self._scoped(organization_id), device_type, is_active, search
        )
        stmt = stmt.order_by(*self._ordering(sort_by, sort_dir)).offset(skip).limit(limit)

        return list(self.db.scalars(stmt).all())

    @staticmethod
    def _ordering(sort_by: str, sort_dir: str):
        """
        The ORDER BY for a sortable column, with a stable tiebreak

        Only catalogued columns are accepted: a column name straight from a
        query string would otherwise be interpolated into SQL. Hostname is
        appended as a tiebreak so paging through equal values - every device
        with the same type, say - does not repeat or skip rows between pages.

        The tiebreak follows the sort direction. That is not cosmetic: after a
        discovery crawl a whole estate can share one value for the column being
        sorted - every device cisco_ios, over ssh, never authenticated, never
        backed up - and with a fixed ascending tiebreak the ascending and
        descending orders came back identical. Clicking the header did nothing
        anyone could see, which is indistinguishable from sorting being broken.
        """
        column = SORTABLE_COLUMNS.get(sort_by, Device.hostname)
        descending = str(sort_dir).lower() == "desc"

        # NULLs last either way: an unsorted blank is noise at the top of a
        # descending sort.
        primary = (
            column.desc().nullslast() if descending else column.asc().nullslast()
        )

        if column is Device.hostname:
            return (primary,)

        tiebreak = Device.hostname.desc() if descending else Device.hostname.asc()
        return (primary, tiebreak)

    def get_by_id_and_organization(
        self, id: int, organization_id: int
    ) -> Optional[Device]:
        """
        Get device by ID with organization scope

        Args:
            id: Device ID
            organization_id: Organization ID (tenant scope)

        Returns:
            Device instance or None
        """
        return self.db.scalars(
            select(Device).where(
                Device.id == id, Device.organization_id == organization_id
            )
        ).first()

    def get_by_ids_and_organization(
        self, ids: Sequence[int], organization_id: int
    ) -> List[Device]:
        """
        Get several devices by ID within one organization, in one query

        Args:
            ids: Device IDs
            organization_id: Organization ID (tenant scope)

        Returns:
            List of devices that exist and belong to the organization
        """
        if not ids:
            return []

        return list(
            self.db.scalars(
                select(Device).where(
                    Device.id.in_(set(ids)),
                    Device.organization_id == organization_id,
                )
            ).all()
        )

    def get_connection_details(self, ids: Sequence[int]) -> List[Device]:
        """
        Get devices with only the columns needed to connect and back up

        Args:
            ids: Device IDs

        Returns:
            List of partially loaded devices
        """
        if not ids:
            return []

        return list(
            self.db.scalars(
                select(Device)
                .options(load_only(*_CONNECTION_COLUMNS))
                .where(Device.id.in_(set(ids)))
            ).all()
        )

    def get_by_hostname(
        self, hostname: str, organization_id: int
    ) -> Optional[Device]:
        """
        Get device by hostname within organization

        Args:
            hostname: Device hostname
            organization_id: Organization ID (tenant scope)

        Returns:
            Device instance or None
        """
        return self.db.scalars(
            select(Device).where(
                Device.hostname == hostname,
                Device.organization_id == organization_id,
            )
        ).first()

    def get_by_ip(self, ip_address: str, organization_id: int) -> Optional[Device]:
        """
        Get device by IP address within organization

        Args:
            ip_address: Device IP address
            organization_id: Organization ID (tenant scope)

        Returns:
            Device instance or None
        """
        return self.db.scalars(
            select(Device).where(
                Device.ip_address == ip_address,
                Device.organization_id == organization_id,
            )
        ).first()

    def get_existing_hostnames_and_ips(
        self, organization_id: int, hostnames: Sequence[str], ips: Sequence[str]
    ) -> Tuple[set, set]:
        """
        Look up which of the given hostnames/IPs are already taken

        Used by bulk upload so that N candidate devices cost two queries
        instead of 2N.

        Args:
            organization_id: Organization ID (tenant scope)
            hostnames: Candidate hostnames
            ips: Candidate IP addresses

        Returns:
            Tuple of (taken hostnames, taken IP addresses)
        """
        taken_hostnames = set()
        taken_ips = set()

        if hostnames:
            taken_hostnames = set(
                self.db.scalars(
                    select(Device.hostname).where(
                        Device.organization_id == organization_id,
                        Device.hostname.in_(set(hostnames)),
                    )
                ).all()
            )

        if ips:
            taken_ips = set(
                self.db.scalars(
                    select(Device.ip_address).where(
                        Device.organization_id == organization_id,
                        Device.ip_address.in_(set(ips)),
                    )
                ).all()
            )

        return taken_hostnames, taken_ips

    def count_by_organization(
        self,
        organization_id: int,
        device_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> int:
        """
        Count devices in organization with filters

        Args:
            organization_id: Organization ID (tenant scope)
            device_type: Filter by device type
            is_active: Filter by active status
            search: Search in hostname or IP address

        Returns:
            Device count
        """
        # COUNT over the id column directly; Query.count() would wrap the whole
        # entity SELECT in a subquery and make the planner do more work.
        stmt = select(func.count(Device.id)).where(
            Device.organization_id == organization_id
        )
        stmt = self._apply_filters(stmt, device_type, is_active, search)

        return self.db.scalar(stmt) or 0

    def count_grouped_by_status(self, organization_id: int) -> dict:
        """
        Get device totals and per-type breakdown in a single query

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            dict with 'total', 'active' and 'by_type'
        """
        rows = self.db.execute(
            select(
                Device.device_type,
                func.count(Device.id),
                func.count(Device.id).filter(Device.is_active.is_(True)),
            )
            .where(Device.organization_id == organization_id)
            .group_by(Device.device_type)
        ).all()

        by_type = {}
        total = 0
        active = 0

        for device_type, type_count, type_active in rows:
            by_type[device_type] = type_count
            total += type_count
            active += type_active

        return {"total": total, "active": active, "by_type": by_type}

    def update_last_backup(
        self,
        device_id: int,
        status: str,
        timestamp: datetime,
        commit: bool = True,
    ) -> None:
        """
        Update device last backup information

        Issues a targeted UPDATE instead of loading the row first.

        Args:
            device_id: Device ID
            status: Backup status
            timestamp: Backup timestamp
            commit: Commit immediately
        """
        self.db.execute(
            sql_update(Device)
            .where(Device.id == device_id)
            .values(last_backup_at=timestamp, last_backup_status=status)
            .execution_options(synchronize_session=False)
        )

        if commit:
            self.db.commit()

    def get_active_by_organization(self, organization_id: int) -> List[Device]:
        """
        Get all active devices in organization

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            List of active devices
        """
        return list(
            self.db.scalars(
                select(Device).where(
                    Device.organization_id == organization_id,
                    Device.is_active.is_(True),
                )
            ).all()
        )

    def get_active_ids_by_organization(self, organization_id: int) -> List[int]:
        """
        Get IDs of all active devices in an organization

        The scheduler only needs identifiers, so this avoids materialising
        whole Device rows (including encrypted credentials) for every device.

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            List of device IDs
        """
        return list(
            self.db.scalars(
                select(Device.id).where(
                    Device.organization_id == organization_id,
                    Device.is_active.is_(True),
                )
            ).all()
        )

    def iter_all_ids(self, batch_size: int = 500):
        """
        Yield every device ID, in batches, for maintenance tasks

        Uses keyset pagination rather than a streaming cursor: callers such as
        the retention job commit between batches, which would invalidate an
        open server-side cursor, and keyset paging cannot skip or repeat rows
        the way OFFSET can when the table changes underneath it.

        Args:
            batch_size: Rows to fetch per round trip

        Yields:
            int: Device ID
        """
        last_id = 0

        while True:
            batch = list(
                self.db.scalars(
                    select(Device.id)
                    .where(Device.id > last_id)
                    .order_by(Device.id)
                    .limit(batch_size)
                ).all()
            )

            if not batch:
                return

            yield from batch
            last_id = batch[-1]
