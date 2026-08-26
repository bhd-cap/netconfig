"""
Application configuration settings
"""
import json
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

    # Connection pool. Each pooled connection costs memory in both the app
    # process and Postgres, so the pool is sized per-process and every service
    # (api, worker, beat) can set its own via the environment.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800  # seconds; drop connections before the server does
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    SECRET_KEY: str
    ENCRYPTION_KEY: str
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS.
    #
    # Held as a raw string and parsed on access. pydantic-settings JSON-decodes
    # any list-typed field straight out of the environment, before field
    # validators run, so the comma-separated form documented in .env.example
    # ("a,b") raised a SettingsError at import time and took the whole process
    # down. Both that form and a JSON array are accepted here.
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost"

    @property
    def cors_origins(self) -> List[str]:
        """CORS origins as a list"""
        raw = (self.CORS_ORIGINS or "").strip()

        if not raw:
            return []

        if raw.startswith("["):
            try:
                return [
                    str(origin).strip()
                    for origin in json.loads(raw)
                    if str(origin).strip()
                ]
            except (json.JSONDecodeError, TypeError):
                pass

        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Backup Configuration
    BACKUP_BASE_PATH: str = "/backups"
    MAX_CONCURRENT_BACKUPS: int = 10
    DEFAULT_RETENTION_DAYS: int = 90
    DEFAULT_SSH_TIMEOUT: int = 60

    # Skip writing a new backup file when the retrieved configuration is
    # byte-identical to the device's previous one. Saves disk, IO and the
    # diff work that would follow.
    BACKUP_DEDUPLICATE: bool = True

    # Comparison limits. Diffing is O(n*m) in the worst case, so refuse
    # pathological inputs rather than pinning a CPU for minutes.
    COMPARE_MAX_FILE_BYTES: int = 32 * 1024 * 1024
    COMPARE_MAX_STRUCTURED_BLOCKS: int = 500
    # difflib's autojunk heuristic ignores lines that occur in more than 1% of
    # a file, which on device configs means repeated markers like "!" - fast,
    # but it can align hunks poorly. Turning it off gives better diffs and
    # costs several times the CPU, so it stays opt-in and line-capped.
    COMPARE_ACCURATE_DIFF: bool = False
    COMPARE_ACCURATE_DIFF_MAX_LINES: int = 2000

    # Bulk CSV device upload
    MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024

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
