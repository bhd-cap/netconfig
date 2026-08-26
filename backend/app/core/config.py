"""
Application configuration settings
"""
from typing import List
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, field_validator


class Settings(BaseSettings):
    """Application settings"""

    # Application
    APP_NAME: str = "Network Config Backup System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: PostgresDsn

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str
    ENCRYPTION_KEY: str
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost"]

    @field_validator("CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v):
        if isinstance(v, str):
            return [i.strip() for i in v.split(",")]
        return v

    # Backup Configuration
    BACKUP_BASE_PATH: str = "/backups"
    MAX_CONCURRENT_BACKUPS: int = 10
    DEFAULT_RETENTION_DAYS: int = 90
    DEFAULT_SSH_TIMEOUT: int = 60

    # Celery
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    @field_validator("CELERY_BROKER_URL", mode="before")
    def set_celery_broker(cls, v, values):
        if v:
            return v
        return values.data.get("REDIS_URL", "redis://localhost:6379/0")

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    def set_celery_backend(cls, v, values):
        if v:
            return v
        return values.data.get("REDIS_URL", "redis://localhost:6379/0")

    @field_validator("JWT_SECRET_KEY", mode="before")
    def set_jwt_secret(cls, v, values):
        if v:
            return v
        return values.data.get("SECRET_KEY")

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Admin User (for initialization)
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "changeme"
    ADMIN_ORG_NAME: str = "Default Organization"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "allow"


# Global settings instance
settings = Settings()
