"""
Administration models: roles, per-organization settings, remote backup targets
"""
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Role(Base):
    """
    A named set of permissions

    Permissions are a JSONB list of strings (see app.core.permissions). System
    roles are seeded per organization and cannot be deleted, so an
    organization can never end up with no way to administer itself.
    """

    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    permissions = Column(JSONB, nullable=False, default=list)

    # Seeded roles (admin/operator/viewer) - editable permissions, but they
    # cannot be removed.
    is_system = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    users = relationship("User", back_populates="role")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_role_name_per_org"),
        Index("ix_roles_org", "organization_id"),
    )

    def __repr__(self):
        return f"<Role(id={self.id}, name='{self.name}')>"


class AppSettings(Base):
    """
    Per-organization application settings

    One row per organization, created on demand with defaults.
    """

    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # --- backup retention -------------------------------------------------
    retention_days = Column(Integer, nullable=False, default=90)
    retention_max_per_device = Column(Integer, nullable=True)
    retention_enabled = Column(Boolean, default=True, nullable=False)

    # --- backup schedule defaults ----------------------------------------
    default_schedule_cron = Column(String(100), nullable=False, default="0 2 * * *")
    default_schedule_enabled = Column(Boolean, default=False, nullable=False)
    max_concurrent_backups = Column(Integer, nullable=False, default=10)

    # --- email notifications ---------------------------------------------
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, nullable=False, default=587)
    smtp_username = Column(String(255), nullable=True)
    smtp_password_encrypted = Column(Text, nullable=True)
    smtp_use_tls = Column(Boolean, default=True, nullable=False)
    smtp_from_address = Column(String(255), nullable=True)

    notifications_enabled = Column(Boolean, default=False, nullable=False)
    notify_recipients = Column(JSONB, nullable=False, default=list)
    notify_on_backup_failure = Column(Boolean, default=True, nullable=False)
    notify_on_backup_success = Column(Boolean, default=False, nullable=False)
    notify_on_config_change = Column(Boolean, default=True, nullable=False)
    notify_on_new_host = Column(Boolean, default=False, nullable=False)

    # --- maintenance windows ---------------------------------------------
    # [{"name": "...", "days": [0-6], "start": "22:00", "end": "02:00",
    #   "suppress_backups": true, "suppress_notifications": true}]
    # Stored as local wall-clock times in maintenance_timezone.
    maintenance_windows = Column(JSONB, nullable=False, default=list)
    maintenance_timezone = Column(String(64), nullable=False, default="UTC")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self):
        return f"<AppSettings(org={self.organization_id})>"


class BackupTarget(Base):
    """
    A remote server that stored configurations are copied to

    Supports SFTP (over SSH) and FTP/FTPS. Credentials are encrypted with the
    same Fernet key as device passwords.
    """

    __tablename__ = "backup_targets"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    name = Column(String(255), nullable=False)
    protocol = Column(String(10), nullable=False, default="sftp")  # sftp|ftp|ftps
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=22)

    username = Column(String(255), nullable=False)
    encrypted_password = Column(Text, nullable=True)
    private_key = Column(Text, nullable=True)  # encrypted PEM, SFTP only
    private_key_passphrase = Column(Text, nullable=True)  # encrypted

    remote_path = Column(Text, nullable=False, default="/")
    # Whether to mirror the local {org}/{hostname}/ layout on the remote side.
    use_device_subdirectories = Column(Boolean, default=True, nullable=False)

    is_enabled = Column(Boolean, default=True, nullable=False)
    # Upload each configuration as soon as it is stored.
    upload_on_backup = Column(Boolean, default=True, nullable=False)
    verify_host_key = Column(Boolean, default=False, nullable=False)
    known_host_key = Column(Text, nullable=True)

    last_status = Column(String(20), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    uploads_succeeded = Column(Integer, nullable=False, default=0)
    uploads_failed = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_backup_target_name"),
        Index("ix_backup_targets_org_enabled", "organization_id", "is_enabled"),
    )

    def __repr__(self):
        return f"<BackupTarget(id={self.id}, {self.protocol}://{self.host})>"
