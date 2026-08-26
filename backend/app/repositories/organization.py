"""
Organization repository
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.models.organization import Organization
from app.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository[Organization]):
    """Repository for Organization operations"""

    def __init__(self, db: Session):
        super().__init__(Organization, db)

    def get_by_name(self, name: str) -> Optional[Organization]:
        """
        Get organization by name

        Args:
            name: Organization name

        Returns:
            Organization instance or None
        """
        return self.db.query(Organization).filter(Organization.name == name).first()

    def get_active(self, skip: int = 0, limit: int = 100):
        """
        Get all active organizations

        Args:
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of active organizations
        """
        return (
            self.db.query(Organization)
            .filter(Organization.is_active == True)
            .offset(skip)
            .limit(limit)
            .all()
        )
