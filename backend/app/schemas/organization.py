"""
Organization schemas for request/response validation
"""
from datetime import datetime
from pydantic import BaseModel, Field


class OrganizationBase(BaseModel):
    """Base organization schema with common fields"""
    name: str = Field(..., min_length=1, max_length=255, description="Organization name")
    description: str | None = Field(None, description="Organization description")


class OrganizationCreate(OrganizationBase):
    """Schema for creating an organization"""
    pass


class OrganizationUpdate(BaseModel):
    """Schema for updating an organization"""
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None


class OrganizationInDB(OrganizationBase):
    """Schema for organization in database"""
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OrganizationResponse(OrganizationInDB):
    """Schema for organization API response"""
    pass


class OrganizationWithStats(OrganizationResponse):
    """Schema for organization with statistics"""
    device_count: int = 0
    user_count: int = 0
    backup_count: int = 0
