"""
AuditLog schemas for request/response validation
"""
from datetime import datetime
from typing import Dict, Any
from pydantic import BaseModel, Field


class AuditLogBase(BaseModel):
    """Base audit log schema"""
    action: str = Field(..., max_length=50, description="Action performed")
    resource_type: str = Field(..., max_length=50, description="Resource type (device, user, etc.)")
    resource_id: int | None = Field(None, description="Resource ID")
    details: Dict[str, Any] | None = Field(default=None, description="Additional details")


class AuditLogCreate(AuditLogBase):
    """Schema for creating an audit log"""
    user_id: int | None = None
    ip_address: str | None = None
    status: str = "success"
    error_message: str | None = None


class AuditLogInDB(AuditLogBase):
    """Schema for audit log in database"""
    id: int
    user_id: int | None
    ip_address: str | None
    timestamp: datetime
    status: str
    error_message: str | None

    class Config:
        from_attributes = True


class AuditLogResponse(AuditLogInDB):
    """Schema for audit log API response"""
    username: str | None = None


class AuditLogList(BaseModel):
    """Schema for paginated audit log list"""
    total: int
    items: list[AuditLogResponse]
    page: int
    page_size: int
