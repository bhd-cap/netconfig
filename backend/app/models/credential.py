"""
Credential vault: the sets of logins discovery tries against a device

A network rarely has one password. Discovery walks outwards into devices
nobody has entered credentials for yet, so it needs an ordered list to try
rather than a single set inherited from the seed.

Every secret is Fernet encrypted with ENCRYPTION_KEY, exactly like a device's
own password, and no endpoint ever returns one.
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


class Credential(Base):
    """
    One credential set, either CLI (SSH/telnet) or SNMP

    `priority` is the order they are tried in, lowest first. A credential that
    has worked before is tried ahead of its priority for the same device,
    because the alternative is walking the whole list again on every run.
    """

    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    # 'cli' drives SSH and telnet through Netmiko; 'snmp' is read-only.
    kind = Column(String(10), nullable=False, default="cli")

    # Lowest first. Ties break on id, so the order is always deterministic.
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    is_enabled = Column(Boolean, default=True, nullable=False)

    # --- CLI ---------------------------------------------------------------
    username = Column(String(100), nullable=True)
    encrypted_password = Column(Text, nullable=True)
    encrypted_enable_secret = Column(Text, nullable=True)
    ssh_key_path = Column(Text, nullable=True)

    # --- SNMP --------------------------------------------------------------
    snmp_version = Column(String(5), nullable=True)          # 1 | 2c | 3
    encrypted_community = Column(Text, nullable=True)
    snmp_v3_user = Column(String(100), nullable=True)
    encrypted_v3_auth_key = Column(Text, nullable=True)
    encrypted_v3_priv_key = Column(Text, nullable=True)
    snmp_v3_auth_protocol = Column(String(20), nullable=True)
    snmp_v3_priv_protocol = Column(String(20), nullable=True)

    # --- Bookkeeping -------------------------------------------------------
    # Which of these actually work is the single most useful thing to know
    # when a discovery run comes back half empty.
    success_count = Column(Integer, nullable=False, default=0, server_default="0")
    failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_credential_name_per_org"),
        # Discovery reads "the enabled credentials of this kind, in order" on
        # every probe, which is exactly this index.
        Index(
            "ix_credentials_org_kind_priority",
            "organization_id",
            "kind",
            "priority",
        ),
    )

    def __repr__(self):
        return f"<Credential(id={self.id}, name='{self.name}', kind='{self.kind}')>"


class DeviceProbe(Base):
    """
    What happened the last time each transport was tried against a device

    Kept so the Devices page can say *why* a device is not eligible for
    backup - "SSH refused, telnet timed out, 4 credentials tried" is
    actionable; a bare inactive flag is not.
    """

    __tablename__ = "device_probes"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    # 'ssh' | 'telnet' | 'snmp'
    transport = Column(String(10), nullable=False)
    # 'success' | 'auth_failed' | 'unreachable' | 'error'
    result = Column(String(20), nullable=False)

    credential_id = Column(
        Integer, ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    credential_name = Column(String(100), nullable=True)

    # How many credentials were tried before giving up, and what went wrong.
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    message = Column(Text, nullable=True)
    duration = Column(Integer, nullable=True)  # milliseconds

    probed_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    device = relationship("Device")

    __table_args__ = (
        # One row per device per transport: the latest outcome, upserted.
        UniqueConstraint("device_id", "transport", name="uq_probe_device_transport"),
        Index("ix_device_probes_org", "organization_id"),
    )

    def __repr__(self):
        return (
            f"<DeviceProbe(device={self.device_id}, {self.transport}={self.result})>"
        )
