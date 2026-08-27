"""
Device model for network devices
"""
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
)
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

    # How to reach the device: ssh (default), telnet or snmp. SNMP is
    # read-only and cannot retrieve a full configuration, so it is used for
    # discovery and inventory rather than backup.
    transport = Column(String(10), nullable=False, default="ssh", server_default="ssh")
    snmp_version = Column(String(5), nullable=True)          # 1 | 2c | 3
    snmp_community = Column(Text, nullable=True)             # Fernet encrypted
    snmp_port = Column(Integer, nullable=False, default=161, server_default="161")
    snmp_v3_user = Column(String(100), nullable=True)
    snmp_v3_auth_key = Column(Text, nullable=True)           # Fernet encrypted
    snmp_v3_priv_key = Column(Text, nullable=True)           # Fernet encrypted
    snmp_v3_auth_protocol = Column(String(20), nullable=True)
    snmp_v3_priv_protocol = Column(String(20), nullable=True)

    # Set when the device was added by a discovery crawl rather than by hand,
    # so operators can tell the two apart and review what was found.
    discovered = Column(Boolean, default=False, nullable=False, server_default="false")
    discovery_source = Column(String(255), nullable=True)
    last_discovered_at = Column(DateTime(timezone=True), nullable=True)

    # Whether a CLI login has ever actually succeeded. is_active alone cannot
    # answer this: a discovered device is in the inventory whether or not
    # anyone can log into it, and only a device that authenticates is worth
    # putting on a backup schedule.
    #
    # 'never'  - not tried yet
    # 'success' - a credential worked; the device is backup-eligible
    # 'auth_failed' - reachable, but no credential was accepted
    # 'unreachable' - nothing answered on any CLI transport
    last_auth_status = Column(
        String(20), nullable=False, default="never", server_default="never"
    )
    last_auth_at = Column(DateTime(timezone=True), nullable=True)
    auth_error = Column(Text, nullable=True)

    # The vault credential that last worked, so the next run tries it first
    # instead of walking the whole list again.
    credential_id = Column(
        Integer, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )

    # --- facts learned from the device itself -----------------------------
    # Populated by SNMP where available, otherwise from CLI version output.
    # These are the fields people search and report on; anything else lands
    # in discovered_facts.
    model = Column(String(255), nullable=True)
    serial_number = Column(String(255), nullable=True)
    os_version = Column(String(255), nullable=True)
    snmp_sysname = Column(String(255), nullable=True)
    snmp_sysdescr = Column(Text, nullable=True)
    snmp_location = Column(String(255), nullable=True)
    snmp_contact = Column(String(255), nullable=True)
    snmp_uptime_seconds = Column(BigInteger, nullable=True)
    snmp_last_polled_at = Column(DateTime(timezone=True), nullable=True)
    # Everything else the probe returned - interface counts, chassis ids,
    # per-vendor OIDs. Free-form on purpose: which OIDs answer varies by
    # vendor and firmware, and a column per fact would never keep up.
    discovered_facts = Column(JSONB, nullable=True)

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
