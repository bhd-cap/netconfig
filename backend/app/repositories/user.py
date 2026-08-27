"""
User repository
"""
from typing import Optional
from sqlalchemy.orm import Session
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User operations"""

    def __init__(self, db: Session):
        super().__init__(User, db)

    def get_by_username(self, username: str) -> Optional[User]:
        """
        Get user by username

        Args:
            username: Username

        Returns:
            User instance or None
        """
        return self.db.query(User).filter(User.username == username).first()

    def get_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email

        Args:
            email: Email address

        Returns:
            User instance or None
        """
        return self.db.query(User).filter(User.email == email).first()

    def get_by_organization(
        self, organization_id: int, skip: int = 0, limit: int = 100
    ):
        """
        Get users by organization with pagination

        Args:
            organization_id: Organization ID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of users in organization
        """
        return (
            self.db.query(User)
            .filter(User.organization_id == organization_id)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_active_by_organization(self, organization_id: int):
        """
        Get active users in an organization

        Args:
            organization_id: Organization ID

        Returns:
            List of active users
        """
        return (
            self.db.query(User)
            .filter(
                User.organization_id == organization_id,
                User.is_active == True,
            )
            .all()
        )

    def count_by_organization(self, organization_id: int) -> int:
        """
        Count users in an organization

        Args:
            organization_id: Organization ID

        Returns:
            User count
        """
        return (
            self.db.query(User)
            .filter(User.organization_id == organization_id)
            .count()
        )
