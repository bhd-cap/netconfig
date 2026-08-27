"""
Network discovery models: adjacencies, host inventory, OUI vendors, diagrams
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


class Neighbor(Base):
    """
    One link-layer adjacency learned from LLDP or CDP

    A neighbour is stored per (device, local interface, remote hostname,
    remote interface): a link that keeps being seen updates last_seen instead
    of accumulating duplicate rows, which is what makes "when did this link
    disappear" answerable.
    """

    __tablename__ = "neighbors"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    local_interface = Column(String(100), nullable=False)
    remote_hostname = Column(String(255), nullable=False)
    remote_interface = Column(String(100), nullable=True)
    remote_platform = Column(Text, nullable=True)
    remote_mgmt_ip = Column(String(45), nullable=True)
    remote_chassis_id = Column(String(64), nullable=True)
    capabilities = Column(String(255), nullable=True)

    # 'lldp' or 'cdp'
    protocol = Column(String(10), nullable=False, default="lldp")

    # Set when the remote end matches a device we already manage, which is
    # what turns a list of adjacencies into a topology.
    remote_device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    first_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    device = relationship("Device", foreign_keys=[device_id])
    remote_device = relationship("Device", foreign_keys=[remote_device_id])

    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "local_interface",
            "remote_hostname",
            "remote_interface",
            name="uq_neighbor_link",
        ),
        Index("ix_neighbors_org_active", "organization_id", "is_active"),
        Index("ix_neighbors_device", "device_id"),
        Index("ix_neighbors_remote_device", "remote_device_id"),
    )

    def __repr__(self):
        return (
            f"<Neighbor({self.device_id}:{self.local_interface} -> "
            f"{self.remote_hostname}:{self.remote_interface})>"
        )


class HostInventory(Base):
    """
    A host seen on a switch port

    Keyed by (device, interface, mac, vlan) so a host that stays plugged in
    keeps one row with a moving last_seen, and a host that moves to another
    port produces a new row rather than losing its history on the old one.
    """

    __tablename__ = "host_inventory"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    interface = Column(String(100), nullable=False)
    mac_address = Column(String(17), nullable=False)
    vlan = Column(Integer, nullable=True)
    entry_type = Column(String(20), nullable=True)

    # Filled in from ARP when the switch or a router knows the address.
    ip_address = Column(String(45), nullable=True)
    hostname = Column(String(255), nullable=True)

    # Resolved from the OUI table at write time so reports and the UI do not
    # have to join on every read.
    vendor = Column(String(255), nullable=True)

    first_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    notes = Column(Text, nullable=True)

    device = relationship("Device")

    __table_args__ = (
        UniqueConstraint(
            "device_id", "interface", "mac_address", "vlan", name="uq_host_on_port"
        ),
        Index("ix_host_inventory_org_last_seen", "organization_id", "last_seen"),
        Index("ix_host_inventory_mac", "mac_address"),
        Index("ix_host_inventory_device_iface", "device_id", "interface"),
        Index("ix_host_inventory_org_vendor", "organization_id", "vendor"),
    )

    def __repr__(self):
        return (
            f"<HostInventory({self.mac_address} on {self.device_id}:{self.interface})>"
        )


class OuiVendor(Base):
    """
    IEEE OUI prefix to vendor name

    Populated from the public IEEE registry. The prefix is stored as six
    lowercase hex characters with no separators, which is what a normalised
    MAC's first three octets reduce to.
    """

    __tablename__ = "oui_vendors"

    oui = Column(String(6), primary_key=True)
    vendor_name = Column(String(255), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self):
        return f"<OuiVendor({self.oui}={self.vendor_name})>"


class TopologyDiagram(Base):
    """
    A saved, user-editable network diagram

    The generated graph is never stored: it is rebuilt from current
    adjacencies on every load. What is stored here is the user's edits on top
    of it - node positions, hidden nodes, renamed labels, manually drawn
    links - so a refresh picks up newly discovered devices without discarding
    the layout someone arranged by hand.
    """

    __tablename__ = "topology_diagrams"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # {"nodes": {"<key>": {"x": 0, "y": 0, "hidden": false, "label": "..."}},
    #  "links": [{"source": "...", "target": "...", "manual": true}],
    #  "viewport": {"zoom": 1, "x": 0, "y": 0}}
    layout = Column(JSONB, nullable=False, default=dict)

    is_default = Column(Boolean, default=False, nullable=False)

    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_diagram_name_per_org"),
        Index("ix_topology_diagrams_org", "organization_id"),
    )

    def __repr__(self):
        return f"<TopologyDiagram(id={self.id}, name='{self.name}')>"


class DiscoveryRun(Base):
    """Record of one discovery crawl, for the UI and for reports"""

    __tablename__ = "discovery_runs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    seed_device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )

    status = Column(String(20), nullable=False, default="running")
    max_hops = Column(Integer, nullable=False, default=2)

    devices_probed = Column(Integer, nullable=False, default=0)
    neighbors_found = Column(Integer, nullable=False, default=0)
    hosts_found = Column(Integer, nullable=False, default=0)
    devices_created = Column(Integer, nullable=False, default=0)

    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration = Column(Integer, nullable=True)

    error_message = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True, default=dict)

    triggered_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("ix_discovery_runs_org_started", "organization_id", "started_at"),
    )

    def __repr__(self):
        return f"<DiscoveryRun(id={self.id}, status='{self.status}')>"
