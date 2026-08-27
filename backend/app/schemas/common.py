"""
Common schemas used across the application
"""
from typing import Generic, TypeVar, Any
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Schema for pagination parameters"""
    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic schema for paginated responses"""
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")
    items: list[T] = Field(..., description="List of items")


class SuccessResponse(BaseModel):
    """Schema for success responses"""
    success: bool = True
    message: str
    data: Any | None = None


class ErrorResponse(BaseModel):
    """Schema for error responses"""
    success: bool = False
    error: str = Field(..., description="Error code or type")
    message: str = Field(..., description="Human-readable error message")
    details: Any | None = Field(None, description="Additional error details")


class HealthResponse(BaseModel):
    """Schema for health check response"""
    status: str = "healthy"
    version: str
    database: str = "connected"
    redis: str = "connected"
    celery_worker: str = "active"


class DashboardStats(BaseModel):
    """Schema for dashboard statistics"""
    total_devices: int = 0
    active_devices: int = 0
    total_backups: int = 0
    successful_backups_24h: int = 0
    failed_backups_24h: int = 0
    total_organizations: int = 0
    total_users: int = 0
    storage_used_bytes: int = 0
    last_backup_at: Any | None = None
    backup_success_rate: float = 0.0


class TaskStatusResponse(BaseModel):
    """Schema for Celery task status"""
    task_id: str
    status: str  # PENDING, STARTED, SUCCESS, FAILURE, RETRY
    result: Any | None = None
    error: str | None = None
    progress: int | None = Field(None, ge=0, le=100, description="Progress percentage")
