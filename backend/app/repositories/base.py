"""
Base repository class for database operations
"""
from typing import Generic, TypeVar, Type, Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Base repository with common CRUD operations"""

    def __init__(self, model: Type[ModelType], db: Session):
        """
        Initialize repository

        Args:
            model: SQLAlchemy model class
            db: Database session
        """
        self.model = model
        self.db = db

    def get(self, id: int) -> Optional[ModelType]:
        """
        Get a single record by ID

        Args:
            id: Record ID

        Returns:
            Model instance or None
        """
        return self.db.query(self.model).filter(self.model.id == id).first()

    def get_multi(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
    ) -> List[ModelType]:
        """
        Get multiple records with pagination and filtering

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            filters: Dictionary of field filters
            order_by: Field name to order by

        Returns:
            List of model instances
        """
        query = self.db.query(self.model)

        # Apply filters
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.filter(getattr(self.model, key) == value)

        # Apply ordering
        if order_by and hasattr(self.model, order_by):
            query = query.order_by(getattr(self.model, order_by).desc())

        return query.offset(skip).limit(limit).all()

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """
        Count records with optional filtering

        Args:
            filters: Dictionary of field filters

        Returns:
            Record count
        """
        query = self.db.query(func.count(self.model.id))

        # Apply filters
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.filter(getattr(self.model, key) == value)

        return query.scalar()

    def create(self, obj_in: Dict[str, Any]) -> ModelType:
        """
        Create a new record

        Args:
            obj_in: Dictionary of field values

        Returns:
            Created model instance
        """
        db_obj = self.model(**obj_in)
        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

    def update(self, db_obj: ModelType, obj_in: Dict[str, Any]) -> ModelType:
        """
        Update an existing record

        Args:
            db_obj: Existing model instance
            obj_in: Dictionary of field values to update

        Returns:
            Updated model instance
        """
        for field, value in obj_in.items():
            if hasattr(db_obj, field) and value is not None:
                setattr(db_obj, field, value)

        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

    def delete(self, id: int) -> bool:
        """
        Delete a record by ID

        Args:
            id: Record ID

        Returns:
            True if deleted, False if not found
        """
        obj = self.db.query(self.model).filter(self.model.id == id).first()
        if obj:
            self.db.delete(obj)
            self.db.commit()
            return True
        return False
