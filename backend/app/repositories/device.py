"""
Device repository with tenant scoping
"""
from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.device import Device
from app.repositories.base import BaseRepository


class DeviceRepository(BaseRepository[Device]):
    """Repository for Device operations with multi-tenant support"""

    def __init__(self, db: Session):
        super().__init__(Device, db)

    def get_by_organization(
        self,
        organization_id: int,
        skip: int = 0,
        limit: int = 100,
        device_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> List[Device]:
        """
        Get devices by organization with filtering

        Args:
            organization_id: Organization ID (tenant scope)
            skip: Number of records to skip
            limit: Maximum number of records
            device_type: Filter by device type
            is_active: Filter by active status
            search: Search in hostname or IP address

        Returns:
            List of devices
        """
        query = self.db.query(Device).filter(
            Device.organization_id == organization_id
        )

        # Apply filters
        if device_type:
            query = query.filter(Device.device_type == device_type)

        if is_active is not None:
            query = query.filter(Device.is_active == is_active)

        if search:
            query = query.filter(
                or_(
                    Device.hostname.ilike(f"%{search}%"),
                    Device.ip_address.ilike(f"%{search}%"),
                )
            )

        return query.order_by(Device.hostname).offset(skip).limit(limit).all()

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
        return (
            self.db.query(Device)
            .filter(Device.id == id, Device.organization_id == organization_id)
            .first()
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
        return (
            self.db.query(Device)
            .filter(
                Device.hostname == hostname,
                Device.organization_id == organization_id,
            )
            .first()
        )

    def get_by_ip(self, ip_address: str, organization_id: int) -> Optional[Device]:
        """
        Get device by IP address within organization

        Args:
            ip_address: Device IP address
            organization_id: Organization ID (tenant scope)

        Returns:
            Device instance or None
        """
        return (
            self.db.query(Device)
            .filter(
                Device.ip_address == ip_address,
                Device.organization_id == organization_id,
            )
            .first()
        )

    def count_by_organization(
        self,
        organization_id: int,
        device_type: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> int:
        """
        Count devices in organization with filters

        Args:
            organization_id: Organization ID (tenant scope)
            device_type: Filter by device type
            is_active: Filter by active status

        Returns:
            Device count
        """
        query = self.db.query(Device).filter(
            Device.organization_id == organization_id
        )

        if device_type:
            query = query.filter(Device.device_type == device_type)

        if is_active is not None:
            query = query.filter(Device.is_active == is_active)

        return query.count()

    def update_last_backup(
        self, device_id: int, status: str, timestamp: datetime
    ) -> Optional[Device]:
        """
        Update device last backup information

        Args:
            device_id: Device ID
            status: Backup status
            timestamp: Backup timestamp

        Returns:
            Updated device or None
        """
        device = self.get(device_id)
        if device:
            device.last_backup_at = timestamp
            device.last_backup_status = status
            self.db.commit()
            self.db.refresh(device)
        return device

    def get_active_by_organization(self, organization_id: int) -> List[Device]:
        """
        Get all active devices in organization

        Args:
            organization_id: Organization ID (tenant scope)

        Returns:
            List of active devices
        """
        return (
            self.db.query(Device)
            .filter(
                Device.organization_id == organization_id,
                Device.is_active == True,
            )
            .all()
        )
