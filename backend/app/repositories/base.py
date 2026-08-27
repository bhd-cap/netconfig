"""
Base repository class for database operations
"""
from typing import Generic, TypeVar, Type, Optional, List, Dict, Any, Sequence
from sqlalchemy import func, select
from sqlalchemy.orm import Session
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
        # get() checks the identity map first, so repeated lookups of the same
        # row within a request cost nothing.
        return self.db.get(self.model, id)

    def get_many(self, ids: Sequence[int]) -> List[ModelType]:
        """
        Get multiple records by ID in a single query

        Args:
            ids: Record IDs

        Returns:
            List of model instances (missing IDs are simply absent)
        """
        if not ids:
            return []

        return list(
            self.db.scalars(
                select(self.model).where(self.model.id.in_(set(ids)))
            ).all()
        )

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

    def create(self, obj_in: Dict[str, Any], commit: bool = True) -> ModelType:
        """
        Create a new record

        Args:
            obj_in: Dictionary of field values
            commit: Commit immediately. Pass False to batch several writes
                into one transaction and commit once at the end.

        Returns:
            Created model instance
        """
        db_obj = self.model(**obj_in)
        self.db.add(db_obj)

        if commit:
            # Server-side defaults come back via RETURNING (eager_defaults on
            # the declarative base) and expire_on_commit is off, so no refresh
            # round trip is needed here.
            self.db.commit()
        else:
            self.db.flush()

        return db_obj

    def create_many(
        self, objs_in: Sequence[Dict[str, Any]], commit: bool = True
    ) -> List[ModelType]:
        """
        Create several records in one round trip

        Args:
            objs_in: Field values, one dict per record
            commit: Commit immediately

        Returns:
            List of created model instances
        """
        if not objs_in:
            return []

        db_objs = [self.model(**obj_in) for obj_in in objs_in]
        self.db.add_all(db_objs)

        if commit:
            self.db.commit()
        else:
            self.db.flush()

        return db_objs

    def update(
        self, db_obj: ModelType, obj_in: Dict[str, Any], commit: bool = True
    ) -> ModelType:
        """
        Update an existing record

        Every field in obj_in is written, None included. Callers decide what
        "leave this alone" means by leaving the key out - which is what
        `exclude_unset=True` on a Pydantic model produces.

        Skipping None here instead made it impossible to clear a column
        through any endpoint, silently: sending `device_filter: null` to a
        backup job, which its own endpoint documents as "clears it back to
        every device", kept the old filter and quietly held the job's narrower
        scope. Explicit nulls are the only way to unset a field over JSON, so
        discarding them makes a documented API do nothing.

        Args:
            db_obj: Existing model instance
            obj_in: Dictionary of field values to update; a None value clears
                the field rather than being ignored
            commit: Commit immediately

        Returns:
            Updated model instance
        """
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        if commit:
            self.db.commit()

        return db_obj

    def delete(self, id: int) -> bool:
        """
        Delete a record by ID

        Args:
            id: Record ID

        Returns:
            True if deleted, False if not found
        """
        obj = self.db.get(self.model, id)
        if obj:
            self.db.delete(obj)
            self.db.commit()
            return True
        return False
