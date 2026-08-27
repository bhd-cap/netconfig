"""
Hardware inventory and environmental readings over SNMP

What a device is made of and how it is doing: chassis, modules, power supplies,
fans and transceivers with their serial numbers, plus temperature, voltage,
current, power draw, fan speed, CPU and memory.

Four sources, because no single one is answered by everything in a real estate:

- **ENTITY-MIB** is the standard hardware inventory, and the only reliable
  place to find a serial number per part rather than one for the whole box.
- **ENTITY-SENSOR-MIB** is the standard for readings, indexed by the same
  entPhysicalIndex, so a temperature can name the module it came from without
  any vendor-specific mapping.
- **CISCO-ENVMON-MIB** is what older Cisco hardware offers instead, and there
  is a great deal of it still racked.
- **HOST-RESOURCES-MIB** and Cisco's process and memory MIBs cover CPU and
  memory, which neither entity MIB reports.

The parsing is deliberately separate from the polling. A walk returns a list of
(oid, value) pairs and nothing more; turning those into components and readings
is pure, which is what makes it testable against captured output from real
hardware instead of only against a live device.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# What the numbers in the MIBs mean
# --------------------------------------------------------------------------

# entPhysicalClass. Collapsed to the words an operator uses: the diagram
# between "backplane" and "container" is not worth carrying.
ENTITY_CLASSES = {
    "1": "other",
    "2": "unknown",
    "3": "chassis",
    "4": "backplane",
    "5": "container",
    "6": "power",
    "7": "fan",
    "8": "sensor",
    "9": "module",
    "10": "port",
    "11": "stack",
    "12": "cpu",
}

# The classes worth showing by default. A port and a container are inventory
# in the strict sense and noise in every practical sense: a 48-port switch
# reports 48 of one and a dozen of the other.
INTERESTING_CLASSES = ("chassis", "module", "power", "fan", "stack", "cpu")

# entPhySensorType, and the unit each implies.
SENSOR_TYPES = {
    "1": ("other", ""),
    "2": ("unknown", ""),
    "3": ("voltage", "VAC"),
    "4": ("voltage", "VDC"),
    "5": ("current", "A"),
    "6": ("power", "W"),
    "7": ("frequency", "Hz"),
    "8": ("temperature", "°C"),
    "9": ("humidity", "%RH"),
    "10": ("fan", "RPM"),
    "11": ("airflow", "cmm"),
    "12": ("state", ""),
    "13": ("special", ""),
    "14": ("dbm", "dBm"),
}

# entPhySensorScale: the power of ten the raw value is expressed in. 9 is
# "units", so a value at scale 7 (milli) has to be divided by a thousand.
SENSOR_SCALES = {
    "1": 1e-24, "2": 1e-21, "3": 1e-18, "4": 1e-15, "5": 1e-12,
    "6": 1e-9, "7": 1e-6, "8": 1e-3, "9": 1.0, "10": 1e3,
    "11": 1e6, "12": 1e9, "13": 1e12, "14": 1e15, "15": 1e18,
    "16": 1e21, "17": 1e24,
}

# entPhySensorOperStatus
SENSOR_STATUS = {"1": "ok", "2": "unavailable", "3": "failed"}

# CISCO-ENVMON-MIB state, shared by temperature, fan, supply and voltage.
ENVMON_STATE = {
    "1": "ok",
    "2": "warning",
    "3": "critical",
    "4": "shutdown",
    "5": "notPresent",
    "6": "failed",
}


@dataclass
class Component:
    """One physical part of a device"""

    index: str
    name: Optional[str] = None
    description: Optional[str] = None
    component_class: str = "unknown"
    model_name: Optional[str] = None
    serial_number: Optional[str] = None
    hardware_rev: Optional[str] = None
    firmware_rev: Optional[str] = None
    software_rev: Optional[str] = None
    parent_index: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "entity_index": self.index,
            "name": self.name,
            "description": self.description,
            "component_class": self.component_class,
            "model_name": self.model_name,
            "serial_number": self.serial_number,
            "hardware_rev": self.hardware_rev,
            "firmware_rev": self.firmware_rev,
            "software_rev": self.software_rev,
            "parent_index": self.parent_index,
        }


@dataclass
class Reading:
    """One environmental or utilisation reading"""

    key: str
    name: str
    sensor_type: str
    value: Optional[float] = None
    unit: str = ""
    status: str = "ok"
    source: str = "entity-sensor"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sensor_key": self.key,
            "name": self.name,
            "sensor_type": self.sensor_type,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "source": self.source,
        }


@dataclass
class PollResult:
    """Everything one poll of a device produced"""

    components: List[Component] = field(default_factory=list)
    readings: List[Reading] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.components or self.readings)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _index(oid: str, base: str) -> Optional[str]:
    """
    The table index an OID carries, relative to its column

    A walk returns fully qualified OIDs, and the tail after the column base is
    what ties a description to the serial number of the same part.
    """
    if not oid:
        return None

    oid = oid.lstrip(".")
    base = base.lstrip(".")

    if oid.startswith(base + "."):
        return oid[len(base) + 1 :]

    # Some agents return the index alone. Take it at face value rather than
    # dropping a row that is probably fine.
    return oid if oid and "." not in oid else None


def _by_index(rows: Sequence[Tuple[str, str]], base: str) -> Dict[str, str]:
    """Turn one walked column into index -> value"""
    values: Dict[str, str] = {}

    for oid, value in rows or ():
        index = _index(oid, base)
        if index is None:
            continue
        text = (value or "").strip()
        if text:
            values[index] = text

    return values


def _number(value: Optional[str]) -> Optional[float]:
    """A float from an SNMP value, or None when it is not one"""
    if value is None:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_components(columns: Dict[str, Sequence[Tuple[str, str]]]) -> List[Component]:
    """
    Build the hardware inventory from walked ENTITY-MIB columns

    Args:
        columns: name -> walked (oid, value) rows, keyed as in snmp_client.OID

    Returns:
        Components, chassis first, then by index - which is the order the
        hardware is physically nested in on every agent worth the name.
    """
    from app.services.snmp_client import OID

    def column(name: str) -> Dict[str, str]:
        return _by_index(columns.get(name, ()), OID[name])

    descriptions = column("entPhysicalDescr")
    classes = column("entPhysicalClass")
    names = column("entPhysicalName")
    models = column("entPhysicalModelName")
    serials = column("entPhysicalSerialNum")
    hardware = column("entPhysicalHardwareRev")
    firmware = column("entPhysicalFirmwareRev")
    software = column("entPhysicalSoftwareRev")
    parents = column("entPhysicalContainedIn")

    indexes = set(descriptions) | set(names) | set(classes)
    components: List[Component] = []

    for index in indexes:
        component_class = ENTITY_CLASSES.get(classes.get(index, ""), "unknown")

        # A part with no name, no model and no serial says nothing worth
        # storing - agents pad these tables with empty rows.
        name = names.get(index) or descriptions.get(index)
        serial = serials.get(index)
        model = models.get(index)
        if not (name or serial or model):
            continue

        parent = parents.get(index)
        components.append(
            Component(
                index=index,
                name=name,
                description=descriptions.get(index),
                component_class=component_class,
                model_name=model,
                serial_number=serial,
                hardware_rev=hardware.get(index),
                firmware_rev=firmware.get(index),
                software_rev=software.get(index),
                # 0 means "contained in nothing", which is a chassis.
                parent_index=parent if parent and parent != "0" else None,
            )
        )

    def order(component: Component) -> Tuple[int, int, str]:
        rank = 0 if component.component_class == "chassis" else 1
        try:
            return (rank, int(component.index), component.index)
        except ValueError:
            return (rank, 1 << 30, component.index)

    return sorted(components, key=order)


def parse_entity_sensors(
    columns: Dict[str, Sequence[Tuple[str, str]]],
    component_names: Optional[Dict[str, str]] = None,
) -> List[Reading]:
    """
    Build readings from walked ENTITY-SENSOR-MIB columns

    Args:
        columns: name -> walked rows
        component_names: entPhysicalIndex -> name, so a reading can be labelled
            with the part it came from rather than a bare number

    Returns:
        Readings, one per sensor that returned a usable value
    """
    from app.services.snmp_client import OID

    def column(name: str) -> Dict[str, str]:
        return _by_index(columns.get(name, ()), OID[name])

    types = column("entPhySensorType")
    scales = column("entPhySensorScale")
    precisions = column("entPhySensorPrecision")
    values = column("entPhySensorValue")
    statuses = column("entPhySensorOperStatus")
    names = component_names or {}

    readings: List[Reading] = []

    for index, raw in values.items():
        sensor_type, unit = SENSOR_TYPES.get(types.get(index, ""), ("unknown", ""))
        if sensor_type in ("unknown", "other", "state", "special"):
            # Nothing useful to chart, and no unit to label it with.
            continue

        number = _number(raw)
        if number is None:
            continue

        # entPhySensorValue is an integer; scale says which power of ten it is
        # in, and precision says how many digits of it are decimals. Both have
        # to be applied or a temperature reads as 39000 degrees.
        number *= SENSOR_SCALES.get(scales.get(index, "9"), 1.0)

        precision = _number(precisions.get(index))
        if precision:
            number /= 10 ** int(precision)

        status = SENSOR_STATUS.get(statuses.get(index, "1"), "unknown")
        if status == "unavailable":
            continue

        readings.append(
            Reading(
                key=f"entity:{index}",
                name=names.get(index) or f"Sensor {index}",
                sensor_type=sensor_type,
                value=round(number, 3),
                unit=unit,
                status="ok" if status == "ok" else "failed",
                source="entity-sensor",
            )
        )

    return sorted(readings, key=lambda reading: (reading.sensor_type, reading.name))


def parse_cisco_envmon(
    columns: Dict[str, Sequence[Tuple[str, str]]]
) -> List[Reading]:
    """
    Build readings from CISCO-ENVMON-MIB, for hardware with no sensor MIB

    Fans and power supplies report a state and no value, which is still worth
    having: "power supply 2: failed" is the reading somebody needs.
    """
    from app.services.snmp_client import OID

    def column(name: str) -> Dict[str, str]:
        return _by_index(columns.get(name, ()), OID[name])

    readings: List[Reading] = []

    def status_of(state: Optional[str]) -> str:
        word = ENVMON_STATE.get(state or "", "unknown")
        if word == "ok":
            return "ok"
        if word in ("warning",):
            return "warning"
        if word in ("critical", "shutdown", "failed"):
            return "failed"
        return "unknown"

    # Temperature and voltage carry a value as well as a state.
    for prefix, sensor_type, unit, descr_column, value_column, state_column in (
        ("temp", "temperature", "°C", "ciscoEnvMonTemperatureDescr",
         "ciscoEnvMonTemperatureValue", "ciscoEnvMonTemperatureState"),
        ("volt", "voltage", "mV", "ciscoEnvMonVoltageDescr",
         "ciscoEnvMonVoltageValue", "ciscoEnvMonVoltageState"),
    ):
        descriptions = column(descr_column)
        values = column(value_column)
        states = column(state_column)

        for index, description in descriptions.items():
            state = states.get(index)
            if ENVMON_STATE.get(state or "") == "notPresent":
                continue

            readings.append(
                Reading(
                    key=f"envmon:{prefix}:{index}",
                    name=description,
                    sensor_type=sensor_type,
                    value=_number(values.get(index)),
                    unit=unit,
                    status=status_of(state),
                    source="cisco-envmon",
                )
            )

    # Fans and supplies report only a state.
    for prefix, sensor_type, descr_column, state_column in (
        ("fan", "fan", "ciscoEnvMonFanDescr", "ciscoEnvMonFanState"),
        ("psu", "power", "ciscoEnvMonSupplyDescr", "ciscoEnvMonSupplyState"),
    ):
        descriptions = column(descr_column)
        states = column(state_column)

        for index, description in descriptions.items():
            state = states.get(index)
            if ENVMON_STATE.get(state or "") == "notPresent":
                continue

            readings.append(
                Reading(
                    key=f"envmon:{prefix}:{index}",
                    name=description,
                    sensor_type=sensor_type,
                    value=None,
                    unit="",
                    status=status_of(state),
                    source="cisco-envmon",
                )
            )

    return readings


def parse_utilisation(
    columns: Dict[str, Sequence[Tuple[str, str]]]
) -> List[Reading]:
    """
    CPU and memory, from HOST-RESOURCES-MIB or Cisco's own MIBs

    Neither entity MIB reports either, and they are the two numbers anybody
    looks at first.
    """
    from app.services.snmp_client import OID

    def column(name: str) -> Dict[str, str]:
        return _by_index(columns.get(name, ()), OID[name])

    readings: List[Reading] = []

    # HOST-RESOURCES processor load, one row per core.
    for index, raw in column("hrProcessorLoad").items():
        load = _number(raw)
        if load is None:
            continue
        readings.append(
            Reading(
                key=f"cpu:hr:{index}",
                name=f"CPU {index}",
                sensor_type="cpu",
                value=load,
                unit="%",
                status="ok",
                source="host-resources",
            )
        )

    # Cisco's five-minute average, for the boxes that answer no standard one.
    for index, raw in column("cpmCPUTotal5minRev").items():
        load = _number(raw)
        if load is None:
            continue
        readings.append(
            Reading(
                key=f"cpu:cisco:{index}",
                name=f"CPU {index} (5 min)",
                sensor_type="cpu",
                value=load,
                unit="%",
                status="ok",
                source="cisco-process",
            )
        )

    # HOST-RESOURCES storage, which covers RAM as well as disks.
    descriptions = column("hrStorageDescr")
    units = column("hrStorageAllocationUnits")
    sizes = column("hrStorageSize")
    used = column("hrStorageUsed")

    for index, description in descriptions.items():
        size = _number(sizes.get(index))
        consumed = _number(used.get(index))
        if not size or consumed is None or size <= 0:
            continue

        unit_bytes = _number(units.get(index)) or 1
        percent = round((consumed / size) * 100, 1)

        readings.append(
            Reading(
                key=f"storage:{index}",
                name=description,
                sensor_type="memory" if _looks_like_memory(description) else "storage",
                value=percent,
                unit="%",
                status="ok",
                source="host-resources",
            )
        )
        # The absolute figure as well: a percentage alone cannot tell 90% of
        # 512 MB from 90% of 2 TB.
        readings.append(
            Reading(
                key=f"storage:{index}:bytes",
                name=f"{description} used",
                sensor_type="memory" if _looks_like_memory(description) else "storage",
                value=round(consumed * unit_bytes / (1024 * 1024), 1),
                unit="MB",
                status="ok",
                source="host-resources",
            )
        )

    # Cisco memory pools, in bytes used and free.
    pool_names = column("ciscoMemoryPoolName")
    pool_used = column("ciscoMemoryPoolUsed")
    pool_free = column("ciscoMemoryPoolFree")

    for index, name in pool_names.items():
        consumed = _number(pool_used.get(index))
        free = _number(pool_free.get(index))
        if consumed is None or free is None:
            continue

        total = consumed + free
        if total <= 0:
            continue

        readings.append(
            Reading(
                key=f"memory:cisco:{index}",
                name=f"{name} memory",
                sensor_type="memory",
                value=round((consumed / total) * 100, 1),
                unit="%",
                status="ok",
                source="cisco-memory",
            )
        )

    return readings


def _looks_like_memory(description: str) -> bool:
    """Whether an hrStorage row is RAM rather than a disk"""
    lowered = (description or "").lower()
    return any(
        word in lowered
        for word in ("memory", "ram", "physical", "swap", "virtual")
    )


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------

# Walked in this order. The entity tables come first because their names label
# the sensor readings that follow.
_COMPONENT_COLUMNS = (
    "entPhysicalDescr",
    "entPhysicalClass",
    "entPhysicalName",
    "entPhysicalModelName",
    "entPhysicalSerialNum",
    "entPhysicalHardwareRev",
    "entPhysicalFirmwareRev",
    "entPhysicalSoftwareRev",
    "entPhysicalContainedIn",
)

_ENTITY_SENSOR_COLUMNS = (
    "entPhySensorType",
    "entPhySensorScale",
    "entPhySensorPrecision",
    "entPhySensorValue",
    "entPhySensorOperStatus",
)

_ENVMON_COLUMNS = (
    "ciscoEnvMonTemperatureDescr",
    "ciscoEnvMonTemperatureValue",
    "ciscoEnvMonTemperatureState",
    "ciscoEnvMonVoltageDescr",
    "ciscoEnvMonVoltageValue",
    "ciscoEnvMonVoltageState",
    "ciscoEnvMonFanDescr",
    "ciscoEnvMonFanState",
    "ciscoEnvMonSupplyDescr",
    "ciscoEnvMonSupplyState",
)

_UTILISATION_COLUMNS = (
    "hrProcessorLoad",
    "hrStorageDescr",
    "hrStorageAllocationUnits",
    "hrStorageSize",
    "hrStorageUsed",
    "cpmCPUTotal5minRev",
    "ciscoMemoryPoolName",
    "ciscoMemoryPoolUsed",
    "ciscoMemoryPoolFree",
)


def poll(client, want_envmon: bool = True) -> PollResult:
    """
    Walk a device for its hardware inventory and readings

    Args:
        client: An SnmpClient already built with the device's credentials
        want_envmon: Walk the Cisco environmental MIB. Skipped once the
            standard sensor MIB has answered, since the two report the same
            hardware and a walk of a table that does not exist still costs a
            round trip.

    Returns:
        PollResult, with error set rather than raising: one device that will
        not answer must not stop a sweep.
    """
    from app.services.snmp_client import OID

    result = PollResult()

    def walk_all(names: Sequence[str]) -> Dict[str, List[Tuple[str, str]]]:
        walked: Dict[str, List[Tuple[str, str]]] = {}
        for name in names:
            try:
                walked[name] = client.walk(OID[name])
            except Exception as e:  # noqa: BLE001 - a missing table is normal
                logger.debug(f"SNMP walk of {name} failed: {e}")
                walked[name] = []
        return walked

    try:
        entity = walk_all(_COMPONENT_COLUMNS)
        result.components = parse_components(entity)
        if result.components:
            result.sources.append("entity-mib")

        names = {
            component.index: component.name or component.description or component.index
            for component in result.components
        }

        sensors = walk_all(_ENTITY_SENSOR_COLUMNS)
        entity_readings = parse_entity_sensors(sensors, names)
        result.readings.extend(entity_readings)
        if entity_readings:
            result.sources.append("entity-sensor-mib")

        if want_envmon and not entity_readings:
            envmon = parse_cisco_envmon(walk_all(_ENVMON_COLUMNS))
            result.readings.extend(envmon)
            if envmon:
                result.sources.append("cisco-envmon-mib")

        utilisation = parse_utilisation(walk_all(_UTILISATION_COLUMNS))
        result.readings.extend(utilisation)
        if utilisation:
            result.sources.append("host-resources-mib")

    except Exception as e:  # noqa: BLE001 - one device must not stop a sweep
        logger.exception("SNMP inventory poll failed")
        result.error = f"{type(e).__name__}: {e}"

    return result
