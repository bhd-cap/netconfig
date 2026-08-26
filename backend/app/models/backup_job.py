"""
BackupJob model for scheduled backup jobs
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class BackupJob(Base):
    """Scheduled backup job model"""

    __tablename__ = "backup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    schedule_cron = Column(String(100), nullable=False)  # Cron expression: "0 2 * * *"
    is_enabled = Column(Boolean, default=True, nullable=False)
    device_filter = Column(JSONB, nullable=True, default=dict)  # Filter criteria for devices
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="backup_jobs")

    def __repr__(self):
        return f"<BackupJob(id={self.id}, name='{self.name}', cron='{self.schedule_cron}', enabled={self.is_enabled})>"
