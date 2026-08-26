"""
BackupJob schemas for request/response validation
"""
from datetime import datetime
from typing import Dict, Any
from pydantic import BaseModel, Field, field_validator
from croniter import croniter


class BackupJobBase(BaseModel):
    """Base backup job schema"""
    name: str = Field(..., min_length=1, max_length=100, description="Job name")
    schedule_cron: str = Field(..., description="Cron expression (e.g., '0 2 * * *')")
    device_filter: Dict[str, Any] | None = Field(
        default=None,
        description="Filter criteria for selecting devices (e.g., {'tags.location': 'NYC'})"
    )

    @field_validator("schedule_cron")
    def validate_cron(cls, v):
        """Validate cron expression"""
        try:
            croniter(v)
        except Exception as e:
            raise ValueError(f"Invalid cron expression: {str(e)}")
        return v


class BackupJobCreate(BackupJobBase):
    """Schema for creating a backup job"""
    is_enabled: bool = Field(default=True, description="Enable job immediately")


class BackupJobUpdate(BaseModel):
    """Schema for updating a backup job"""
    name: str | None = Field(None, min_length=1, max_length=100)
    schedule_cron: str | None = None
    device_filter: Dict[str, Any] | None = None
    is_enabled: bool | None = None

    @field_validator("schedule_cron")
    def validate_cron(cls, v):
        """Validate cron expression if provided"""
        if v is not None:
            try:
                croniter(v)
            except Exception as e:
                raise ValueError(f"Invalid cron expression: {str(e)}")
        return v


class BackupJobInDB(BackupJobBase):
    """Schema for backup job in database"""
    id: int
    organization_id: int
    is_enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: int | None

    class Config:
        from_attributes = True


class BackupJobResponse(BackupJobInDB):
    """Schema for backup job API response"""
    pass


class BackupJobWithStats(BackupJobResponse):
    """Schema for backup job with execution statistics"""
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    last_run_status: str | None = None
    devices_matched: int = 0
