"""
Device schemas for request/response validation
"""
from datetime import datetime
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field, IPvAnyAddress

# Supported device types
DeviceType = Literal[
    "cisco_ios",
    "cisco_ios_xe",
    "cisco_nxos",
    "arista_eos",
    "fortinet",
    "juniper_junos",
    "aruba_os",
    "hp_comware",
    "hp_procurve"
]


class DeviceBase(BaseModel):
    """Base device schema with common fields"""
    hostname: str = Field(..., min_length=1, max_length=255, description="Device hostname")
    ip_address: str = Field(..., description="Device IP address (IPv4 or IPv6)")
    device_type: DeviceType = Field(..., description="Device OS type")
    port: int = Field(default=22, ge=1, le=65535, description="SSH port")
    username: str = Field(..., min_length=1, max_length=100, description="SSH username")
    description: str | None = Field(None, description="Device description")
    location: str | None = Field(None, max_length=255, description="Physical location")
    tags: Dict[str, Any] | None = Field(default=None, description="Custom tags/metadata")


class DeviceCreate(DeviceBase):
    """Schema for creating a device"""
    password: str = Field(..., min_length=1, description="SSH password (will be encrypted)")
    enable_secret: str | None = Field(None, description="Enable secret for Cisco devices")
    ssh_key_path: str | None = Field(None, description="Path to SSH private key")


class DeviceUpdate(BaseModel):
    """Schema for updating a device"""
    hostname: str | None = Field(None, min_length=1, max_length=255)
    ip_address: str | None = None
    device_type: DeviceType | None = None
    port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, min_length=1, max_length=100)
    password: str | None = Field(None, min_length=1, description="New password (will be encrypted)")
    enable_secret: str | None = None
    ssh_key_path: str | None = None
    description: str | None = None
    location: str | None = None
    tags: Dict[str, Any] | None = None
    is_active: bool | None = None


class DeviceInDB(DeviceBase):
    """Schema for device in database"""
    id: int
    organization_id: int
    is_active: bool
    last_backup_at: datetime | None
    last_backup_status: str | None
    created_at: datetime
    updated_at: datetime
    created_by: int | None

    class Config:
        from_attributes = True


class DeviceResponse(DeviceInDB):
    """Schema for device API response (excludes encrypted credentials)"""
    pass


class DeviceWithBackupCount(DeviceResponse):
    """Schema for device with backup count"""
    backup_count: int = 0
    latest_config_id: int | None = None


class DeviceBulkUpload(BaseModel):
    """Schema for CSV bulk upload"""
    devices: list[DeviceCreate]


class DeviceTestConnection(BaseModel):
    """Schema for testing device connectivity"""
    success: bool
    message: str
    response_time: float | None = None
    device_info: Dict[str, Any] | None = None
