"""
User schemas for request/response validation
"""
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Base user schema with common fields"""
    username: str = Field(..., min_length=3, max_length=50, description="Username")
    email: EmailStr = Field(..., description="Email address")


class UserCreate(UserBase):
    """Schema for creating a user"""
    password: str = Field(..., min_length=8, max_length=100, description="Password")
    organization_id: int = Field(..., description="Organization ID")
    is_admin: bool = Field(default=False, description="Admin role flag")


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8, max_length=100)
    is_active: bool | None = None
    is_admin: bool | None = None


class UserInDB(UserBase):
    """Schema for user in database"""
    id: int
    organization_id: int
    is_active: bool
    is_admin: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserResponse(UserInDB):
    """Schema for user API response (excludes sensitive data)"""
    pass


class UserLogin(BaseModel):
    """Schema for user login"""
    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Password")


class Token(BaseModel):
    """Schema for authentication token"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenPayload(BaseModel):
    """Schema for JWT token payload"""
    sub: int  # user_id
    org_id: int  # organization_id
    exp: datetime | None = None
