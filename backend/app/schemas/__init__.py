"""
Pydantic schemas for request/response validation
"""
from app.schemas.organization import (
    OrganizationBase,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationInDB,
    OrganizationResponse,
    OrganizationWithStats,
)
from app.schemas.user import (
    UserBase,
    UserCreate,
    UserUpdate,
    UserInDB,
    UserResponse,
    UserLogin,
    Token,
    TokenPayload,
)
from app.schemas.device import (
    DeviceType,
    DeviceBase,
    DeviceCreate,
    DeviceUpdate,
    DeviceInDB,
    DeviceResponse,
    DeviceWithBackupCount,
    DeviceBulkUpload,
    DeviceTestConnection,
)
from app.schemas.configuration import (
    ConfigurationBase,
    ConfigurationCreate,
    ConfigurationInDB,
    ConfigurationResponse,
    ConfigurationList,
    ConfigurationCompareRequest,
    ConfigurationCompareResponse,
    BackupTriggerRequest,
    BackupTriggerResponse,
)
from app.schemas.backup_job import (
    BackupJobBase,
    BackupJobCreate,
    BackupJobUpdate,
    BackupJobInDB,
    BackupJobResponse,
    BackupJobWithStats,
)
from app.schemas.audit_log import (
    AuditLogBase,
    AuditLogCreate,
    AuditLogInDB,
    AuditLogResponse,
    AuditLogList,
)
from app.schemas.common import (
    PaginationParams,
    PaginatedResponse,
    SuccessResponse,
    ErrorResponse,
    HealthResponse,
    DashboardStats,
    TaskStatusResponse,
)

__all__ = [
    # Organization
    "OrganizationBase",
    "OrganizationCreate",
    "OrganizationUpdate",
    "OrganizationInDB",
    "OrganizationResponse",
    "OrganizationWithStats",
    # User
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserInDB",
    "UserResponse",
    "UserLogin",
    "Token",
    "TokenPayload",
    # Device
    "DeviceType",
    "DeviceBase",
    "DeviceCreate",
    "DeviceUpdate",
    "DeviceInDB",
    "DeviceResponse",
    "DeviceWithBackupCount",
    "DeviceBulkUpload",
    "DeviceTestConnection",
    # Configuration
    "ConfigurationBase",
    "ConfigurationCreate",
    "ConfigurationInDB",
    "ConfigurationResponse",
    "ConfigurationList",
    "ConfigurationCompareRequest",
    "ConfigurationCompareResponse",
    "BackupTriggerRequest",
    "BackupTriggerResponse",
    # BackupJob
    "BackupJobBase",
    "BackupJobCreate",
    "BackupJobUpdate",
    "BackupJobInDB",
    "BackupJobResponse",
    "BackupJobWithStats",
    # AuditLog
    "AuditLogBase",
    "AuditLogCreate",
    "AuditLogInDB",
    "AuditLogResponse",
    "AuditLogList",
    # Common
    "PaginationParams",
    "PaginatedResponse",
    "SuccessResponse",
    "ErrorResponse",
    "HealthResponse",
    "DashboardStats",
    "TaskStatusResponse",
]
