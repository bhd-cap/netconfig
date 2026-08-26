"""
AuditLog repository
"""
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """Repository for AuditLog operations"""

    def __init__(self, db: Session):
        super().__init__(AuditLog, db)

    def get_recent(
        self,
        skip: int = 0,
        limit: int = 100,
        user_id: Optional[int] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
        days: int = 30,
    ) -> List[AuditLog]:
        """
        Get recent audit logs with filtering

        Args:
            skip: Number of records to skip
            limit: Maximum number of records
            user_id: Filter by user ID
            action: Filter by action
            resource_type: Filter by resource type
            days: Number of days to look back

        Returns:
            List of audit logs
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = self.db.query(AuditLog).filter(AuditLog.timestamp >= cutoff)

        if user_id:
            query = query.filter(AuditLog.user_id == user_id)

        if action:
            query = query.filter(AuditLog.action == action)

        if resource_type:
            query = query.filter(AuditLog.resource_type == resource_type)

        return (
            query.order_by(AuditLog.timestamp.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_by_user(
        self, user_id: int, skip: int = 0, limit: int = 100
    ) -> List[AuditLog]:
        """
        Get audit logs for a specific user

        Args:
            user_id: User ID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of audit logs
        """
        return (
            self.db.query(AuditLog)
            .filter(AuditLog.user_id == user_id)
            .order_by(AuditLog.timestamp.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_by_resource(
        self, resource_type: str, resource_id: int, skip: int = 0, limit: int = 100
    ) -> List[AuditLog]:
        """
        Get audit logs for a specific resource

        Args:
            resource_type: Resource type (e.g., 'device', 'user')
            resource_id: Resource ID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of audit logs
        """
        return (
            self.db.query(AuditLog)
            .filter(
                AuditLog.resource_type == resource_type,
                AuditLog.resource_id == resource_id,
            )
            .order_by(AuditLog.timestamp.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def count_recent(
        self,
        user_id: Optional[int] = None,
        action: Optional[str] = None,
        days: int = 30,
    ) -> int:
        """
        Count recent audit logs

        Args:
            user_id: Filter by user ID
            action: Filter by action
            days: Number of days to look back

        Returns:
            Audit log count
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = self.db.query(AuditLog).filter(AuditLog.timestamp >= cutoff)

        if user_id:
            query = query.filter(AuditLog.user_id == user_id)

        if action:
            query = query.filter(AuditLog.action == action)

        return query.count()

    def log_action(
        self,
        user_id: Optional[int],
        action: str,
        resource_type: str,
        resource_id: Optional[int] = None,
        details: Optional[dict] = None,
        ip_address: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> AuditLog:
        """
        Create an audit log entry

        Args:
            user_id: User ID performing the action
            action: Action performed
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            details: Additional details (JSON)
            ip_address: IP address of user
            status: Action status
            error_message: Error message if failed

        Returns:
            Created audit log
        """
        log_entry = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            status=status,
            error_message=error_message,
        )
        self.db.add(log_entry)
        self.db.commit()
        self.db.refresh(log_entry)
        return log_entry
