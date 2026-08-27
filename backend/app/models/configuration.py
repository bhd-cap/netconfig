"""
Configuration model for backup records
"""
from sqlalchemy import Column, Integer, String, BigInteger, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Configuration(Base):
    """Configuration backup record model"""

    __tablename__ = "configurations"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    file_size = Column(BigInteger, nullable=True)  # Size in bytes
    checksum = Column(String(64), nullable=True)  # SHA256 hash
    backed_up_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    backup_duration = Column(Float, nullable=True)  # Duration in seconds
    status = Column(String(20), nullable=False, default="success")  # success, failed, partial
    error_message = Column(Text, nullable=True)
    config_hash = Column(String(64), nullable=True, index=True)  # For deduplication

    # Relationships
    device = relationship("Device", back_populates="configurations")

    def __repr__(self):
        return f"<Configuration(id={self.id}, device_id={self.device_id}, filename='{self.filename}', status='{self.status}')>"
