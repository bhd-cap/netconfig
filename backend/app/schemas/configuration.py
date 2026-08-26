"""
Configuration schemas for request/response validation
"""
from datetime import datetime
from pydantic import BaseModel, Field


class ConfigurationBase(BaseModel):
    """Base configuration schema"""
    device_id: int = Field(..., description="Device ID")
    filename: str = Field(..., description="Configuration filename")


class ConfigurationCreate(ConfigurationBase):
    """Schema for creating a configuration record"""
    file_path: str
    file_size: int | None = None
    checksum: str | None = None
    backup_duration: float | None = None
    status: str = "success"
    error_message: str | None = None
    config_hash: str | None = None


class ConfigurationInDB(ConfigurationBase):
    """Schema for configuration in database"""
    id: int
    file_path: str
    file_size: int | None
    checksum: str | None
    backed_up_at: datetime
    backup_duration: float | None
    status: str
    error_message: str | None
    config_hash: str | None

    class Config:
        from_attributes = True


class ConfigurationResponse(ConfigurationInDB):
    """Schema for configuration API response"""
    device_hostname: str | None = None
    device_ip: str | None = None


class ConfigurationList(BaseModel):
    """Schema for paginated configuration list"""
    total: int
    items: list[ConfigurationResponse]
    page: int
    page_size: int


class ConfigurationCompareRequest(BaseModel):
    """Schema for configuration comparison request"""
    config1_id: int = Field(..., description="First configuration ID")
    config2_id: int = Field(..., description="Second configuration ID")


class ConfigurationCompareResponse(BaseModel):
    """Schema for configuration comparison response"""
    config1: ConfigurationResponse
    config2: ConfigurationResponse
    diff_unified: str = Field(..., description="Unified diff format")
    diff_html: str | None = Field(None, description="HTML formatted diff")
    changes_summary: dict = Field(..., description="Summary of changes")
    # changes_summary example: {"lines_added": 10, "lines_removed": 5, "lines_modified": 3}


class BackupTriggerRequest(BaseModel):
    """Schema for triggering backup"""
    device_ids: list[int] = Field(..., min_items=1, description="List of device IDs to backup")


class BackupTriggerResponse(BaseModel):
    """Schema for backup trigger response"""
    task_id: str = Field(..., description="Celery task ID")
    device_count: int
    message: str
