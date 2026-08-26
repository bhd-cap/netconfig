"""
Device model for network devices
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class Device(Base):
    """Network device model with multi-tenant support"""

    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    hostname = Column(String(255), nullable=False, index=True)
    ip_address = Column(String(45), nullable=False, index=True)  # Supports IPv4 and IPv6
    device_type = Column(String(50), nullable=False, index=True)  # cisco_ios, arista_eos, etc.
    port = Column(Integer, default=22, nullable=False)
    username = Column(String(100), nullable=False)
    encrypted_password = Column(Text, nullable=False)  # Fernet encrypted
    enable_secret = Column(Text, nullable=True)  # For Cisco devices (Fernet encrypted)
    ssh_key_path = Column(Text, nullable=True)  # Optional SSH key path
    description = Column(Text, nullable=True)
    location = Column(String(255), nullable=True)
    tags = Column(JSONB, nullable=True, default=dict)  # Flexible metadata
    is_active = Column(Boolean, default=True, nullable=False)
    last_backup_at = Column(DateTime(timezone=True), nullable=True)
    last_backup_status = Column(String(20), nullable=True)  # success, failed, pending
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="devices")
    created_by_user = relationship("User", back_populates="created_devices", foreign_keys=[created_by])
    configurations = relationship("Configuration", back_populates="device", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Device(id={self.id}, hostname='{self.hostname}', ip='{self.ip_address}', type='{self.device_type}')>"
