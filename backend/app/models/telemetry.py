"""
Hardware inventory and environmental readings polled over SNMP

Three tables, and the split between them is deliberate:

``DeviceComponent`` is what a device is made of - chassis, modules, power
supplies, fans - with the serial number of each. It changes when somebody
swaps hardware, which is rarely, so it is aged rather than versioned: a part
that stops being reported is marked inactive and kept, because "the power
supply that used to be in slot B" is exactly the question a serial-number
record exists to answer.

``DeviceSensor`` is the current reading per sensor. One row per sensor per
device, upserted, so a table of "what is this device doing right now" is one
indexed query rather than a scan of history.

``SensorReading`` is that history, for charts. Kept separate because it is the
only table here that grows without bound, and it is the only one the retention
task has to prune.
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class DeviceComponent(Base):
    """One physical part of a device, as reported by ENTITY-MIB"""

    __tablename__ = "device_components"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    # entPhysicalIndex, as text: it is an integer on every agent seen so far,
    # but it is an opaque key and nothing here does arithmetic on it.
    entity_index = Column(String(32), nullable=False)

    name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    # chassis, module, power, fan, stack, cpu, port, sensor, container, other
    component_class = Column(String(20), nullable=False, default="unknown")

    model_name = Column(String(128), nullable=True)
    serial_number = Column(String(128), nullable=True, index=True)
    hardware_rev = Column(String(64), nullable=True)
    firmware_rev = Column(String(64), nullable=True)
    software_rev = Column(String(128), nullable=True)

    # entPhysicalContainedIn, so a module can be shown under its chassis.
    parent_index = Column(String(32), nullable=True)

    # Aged rather than deleted: a part that has been removed is still the
    # answer to "what was in that slot".
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    first_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    device = relationship("Device")

    __table_args__ = (
        UniqueConstraint("device_id", "entity_index", name="uq_component_device_index"),
        Index("ix_components_org_class", "organization_id", "component_class"),
        Index("ix_components_device_active", "device_id", "is_active"),
    )

    def __repr__(self):
        return (
            f"<DeviceComponent(device={self.device_id}, "
            f"class='{self.component_class}', serial='{self.serial_number}')>"
        )


class DeviceSensor(Base):
    """The current reading for one sensor on one device"""

    __tablename__ = "device_sensors"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    device_id = Column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    # Stable across polls, and namespaced by where it came from:
    # "entity:1013", "envmon:fan:2", "cpu:hr:1".
    sensor_key = Column(String(64), nullable=False)

    name = Column(String(255), nullable=False)
    # temperature, voltage, current, power, fan, humidity, cpu, memory, storage
    sensor_type = Column(String(20), nullable=False, index=True)
    unit = Column(String(16), nullable=False, default="")

    # Null for a sensor that reports only a state - a power supply that says
    # "failed" and nothing else is still worth recording.
    value = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="ok")
    source = Column(String(32), nullable=False, default="entity-sensor")

    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    first_seen = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_reading_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    device = relationship("Device")
    readings = relationship(
        "SensorReading", back_populates="sensor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("device_id", "sensor_key", name="uq_sensor_device_key"),
        Index("ix_sensors_org_type", "organization_id", "sensor_type"),
        Index("ix_sensors_device_active", "device_id", "is_active"),
    )

    def __repr__(self):
        return (
            f"<DeviceSensor(device={self.device_id}, type='{self.sensor_type}', "
            f"value={self.value})>"
        )


class SensorReading(Base):
    """One historical reading, for the trend charts"""

    __tablename__ = "sensor_readings"

    id = Column(Integer, primary_key=True, index=True)
    sensor_id = Column(
        Integer, ForeignKey("device_sensors.id", ondelete="CASCADE"), nullable=False
    )

    value = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="ok")
    recorded_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sensor = relationship("DeviceSensor", back_populates="readings")

    __table_args__ = (
        # Every chart asks for one sensor over a window, and the retention
        # sweep deletes by age; this index serves both.
        Index("ix_readings_sensor_time", "sensor_id", "recorded_at"),
    )

    def __repr__(self):
        return f"<SensorReading(sensor={self.sensor_id}, value={self.value})>"
