"""
Parsing ENTITY-MIB, ENTITY-SENSOR-MIB, CISCO-ENVMON-MIB and HOST-RESOURCES-MIB.

The fixtures are shaped like real walk output - fully qualified OIDs with the
table index on the end - because that indexing is the part that goes wrong: a
description and a serial number are two different columns, and pairing them up
by index is the whole job.
"""
import pytest

from app.services import snmp_inventory as inv
from app.services.snmp_client import OID


def rows(column: str, values: dict):
    """A walked column, as (oid, value) pairs"""
    base = OID[column]
    return [(f"{base}.{index}", value) for index, value in values.items()]


# --------------------------------------------------------------------------
# Hardware inventory
# --------------------------------------------------------------------------

CATALYST = {
    "entPhysicalDescr": rows("entPhysicalDescr", {
        "1": "Cisco Catalyst 9300-48P Chassis",
        "1000": "Uplink Module",
        "2000": "Power Supply Module",
        "2001": "Power Supply Module",
        "3000": "Fan Module",
    }),
    "entPhysicalClass": rows("entPhysicalClass", {
        "1": "3", "1000": "9", "2000": "6", "2001": "6", "3000": "7",
    }),
    "entPhysicalName": rows("entPhysicalName", {
        "1": "Switch 1",
        "1000": "Switch 1 FRU Uplink Module 1",
        "2000": "Switch 1 Power Supply A",
        "2001": "Switch 1 Power Supply B",
        "3000": "Switch 1 Fan 1",
    }),
    "entPhysicalModelName": rows("entPhysicalModelName", {
        "1": "C9300-48P", "1000": "C9300-NM-8X", "2000": "PWR-C1-715WAC",
    }),
    "entPhysicalSerialNum": rows("entPhysicalSerialNum", {
        "1": "FOC2137L0AB", "1000": "FOC2135L1XY", "2000": "ART2101B3QQ",
    }),
    "entPhysicalHardwareRev": rows("entPhysicalHardwareRev", {"1": "V02"}),
    "entPhysicalSoftwareRev": rows("entPhysicalSoftwareRev", {"1": "17.06.04"}),
    "entPhysicalContainedIn": rows("entPhysicalContainedIn", {
        "1": "0", "1000": "1", "2000": "1", "2001": "1", "3000": "1",
    }),
}


def test_components_pair_their_columns_by_index():
    """
    The point of the exercise

    A description, a model and a serial number arrive as three separate walked
    columns; getting the serial of the chassis attached to the fan would be
    worse than reporting nothing.
    """
    components = {c.index: c for c in inv.parse_components(CATALYST)}

    assert components["1"].serial_number == "FOC2137L0AB"
    assert components["1"].model_name == "C9300-48P"
    assert components["1000"].serial_number == "FOC2135L1XY"
    assert components["2000"].serial_number == "ART2101B3QQ"
    # Reported, but with no serial of its own - which is normal, and not a
    # reason to drop the row.
    assert components["2001"].serial_number is None


def test_components_are_classified():
    components = {c.index: c.component_class for c in inv.parse_components(CATALYST)}

    assert components["1"] == "chassis"
    assert components["1000"] == "module"
    assert components["2000"] == "power"
    assert components["3000"] == "fan"


def test_the_chassis_comes_first():
    """It is the thing everything else is inside"""
    components = inv.parse_components(CATALYST)

    assert components[0].component_class == "chassis"


def test_containment_is_recorded():
    components = {c.index: c for c in inv.parse_components(CATALYST)}

    # 0 means "contained in nothing", which is what a chassis is.
    assert components["1"].parent_index is None
    assert components["1000"].parent_index == "1"


def test_empty_rows_are_dropped():
    """Agents pad these tables; a row with no name, model or serial says nothing"""
    padded = {
        "entPhysicalDescr": rows("entPhysicalDescr", {"1": "Chassis", "9": ""}),
        "entPhysicalClass": rows("entPhysicalClass", {"1": "3", "9": "5"}),
    }

    components = inv.parse_components(padded)

    assert [c.index for c in components] == ["1"]


def test_a_device_with_no_entity_mib_yields_nothing_rather_than_failing():
    assert inv.parse_components({}) == []


# --------------------------------------------------------------------------
# Sensors
# --------------------------------------------------------------------------


def test_sensor_values_are_scaled_and_given_units():
    """
    Scale and precision both have to be applied

    entPhySensorValue is an integer. A 12V rail reported at milli-scale is
    12050, and without the scale it charts as twelve thousand volts.
    """
    sensors = {
        "entPhySensorType": rows("entPhySensorType", {
            "1": "8",    # celsius
            "2": "10",   # rpm
            "3": "6",    # watts
            "4": "4",    # volts DC
        }),
        "entPhySensorScale": rows("entPhySensorScale", {
            "1": "9", "2": "9", "3": "9", "4": "8",   # 8 is milli
        }),
        "entPhySensorPrecision": rows("entPhySensorPrecision", {
            "1": "0", "2": "0", "3": "0", "4": "0",
        }),
        "entPhySensorValue": rows("entPhySensorValue", {
            "1": "42", "2": "6300", "3": "118", "4": "12050",
        }),
        "entPhySensorOperStatus": rows("entPhySensorOperStatus", {
            "1": "1", "2": "1", "3": "1", "4": "1",
        }),
    }

    readings = {r.key: r for r in inv.parse_entity_sensors(sensors)}

    assert readings["entity:1"].sensor_type == "temperature"
    assert readings["entity:1"].value == 42.0
    assert readings["entity:1"].unit == "°C"

    assert readings["entity:2"].sensor_type == "fan"
    assert readings["entity:2"].value == 6300.0

    assert readings["entity:3"].sensor_type == "power"
    assert readings["entity:3"].unit == "W"

    # 12050 at milli-scale is 12.05 volts, not twelve thousand.
    assert readings["entity:4"].value == pytest.approx(12.05)
    assert readings["entity:4"].unit == "VDC"


def test_precision_is_applied_as_well_as_scale():
    sensors = {
        "entPhySensorType": rows("entPhySensorType", {"1": "8"}),
        "entPhySensorScale": rows("entPhySensorScale", {"1": "9"}),
        "entPhySensorPrecision": rows("entPhySensorPrecision", {"1": "1"}),
        "entPhySensorValue": rows("entPhySensorValue", {"1": "425"}),
        "entPhySensorOperStatus": rows("entPhySensorOperStatus", {"1": "1"}),
    }

    reading = inv.parse_entity_sensors(sensors)[0]

    assert reading.value == pytest.approx(42.5)


def test_a_sensor_is_named_after_the_part_it_is_in():
    """"Sensor 1013" is not an answer anybody can act on"""
    sensors = {
        "entPhySensorType": rows("entPhySensorType", {"1013": "8"}),
        "entPhySensorScale": rows("entPhySensorScale", {"1013": "9"}),
        "entPhySensorPrecision": rows("entPhySensorPrecision", {"1013": "0"}),
        "entPhySensorValue": rows("entPhySensorValue", {"1013": "38"}),
        "entPhySensorOperStatus": rows("entPhySensorOperStatus", {"1013": "1"}),
    }

    named = inv.parse_entity_sensors(sensors, {"1013": "Inlet temperature"})[0]
    unnamed = inv.parse_entity_sensors(sensors)[0]

    assert named.name == "Inlet temperature"
    assert unnamed.name == "Sensor 1013"


def test_unavailable_sensors_are_skipped():
    """A sensor the device says is unavailable has no reading to plot"""
    sensors = {
        "entPhySensorType": rows("entPhySensorType", {"1": "8", "2": "8"}),
        "entPhySensorScale": rows("entPhySensorScale", {"1": "9", "2": "9"}),
        "entPhySensorPrecision": rows("entPhySensorPrecision", {"1": "0", "2": "0"}),
        "entPhySensorValue": rows("entPhySensorValue", {"1": "40", "2": "0"}),
        "entPhySensorOperStatus": rows("entPhySensorOperStatus", {"1": "1", "2": "2"}),
    }

    readings = inv.parse_entity_sensors(sensors)

    assert [r.key for r in readings] == ["entity:1"]


def test_untyped_sensors_are_skipped():
    """Nothing to chart and no unit to label it with"""
    sensors = {
        "entPhySensorType": rows("entPhySensorType", {"1": "1", "2": "12"}),
        "entPhySensorScale": rows("entPhySensorScale", {"1": "9", "2": "9"}),
        "entPhySensorPrecision": rows("entPhySensorPrecision", {"1": "0", "2": "0"}),
        "entPhySensorValue": rows("entPhySensorValue", {"1": "1", "2": "1"}),
        "entPhySensorOperStatus": rows("entPhySensorOperStatus", {"1": "1", "2": "1"}),
    }

    assert inv.parse_entity_sensors(sensors) == []


# --------------------------------------------------------------------------
# The older Cisco environmental MIB
# --------------------------------------------------------------------------


def test_cisco_envmon_temperature_and_state():
    envmon = {
        "ciscoEnvMonTemperatureDescr": rows("ciscoEnvMonTemperatureDescr", {
            "1": "chassis inlet", "2": "chassis outlet",
        }),
        "ciscoEnvMonTemperatureValue": rows("ciscoEnvMonTemperatureValue", {
            "1": "31", "2": "40",
        }),
        "ciscoEnvMonTemperatureState": rows("ciscoEnvMonTemperatureState", {
            "1": "1", "2": "2",
        }),
    }

    readings = {r.name: r for r in inv.parse_cisco_envmon(envmon)}

    assert readings["chassis inlet"].value == 31
    assert readings["chassis inlet"].status == "ok"
    assert readings["chassis outlet"].status == "warning"
    assert readings["chassis outlet"].sensor_type == "temperature"


def test_a_failed_power_supply_is_recorded_without_a_value():
    """
    "Power supply 2: failed" is the reading somebody needs

    It has no number, and dropping it for that reason would lose the only
    thing worth reporting about it.
    """
    envmon = {
        "ciscoEnvMonSupplyDescr": rows("ciscoEnvMonSupplyDescr", {
            "1": "Power Supply 1", "2": "Power Supply 2",
        }),
        "ciscoEnvMonSupplyState": rows("ciscoEnvMonSupplyState", {"1": "1", "2": "3"}),
    }

    readings = {r.name: r for r in inv.parse_cisco_envmon(envmon)}

    assert readings["Power Supply 1"].status == "ok"
    assert readings["Power Supply 2"].status == "failed"
    assert readings["Power Supply 2"].value is None
    assert readings["Power Supply 2"].sensor_type == "power"


def test_hardware_that_is_not_fitted_is_skipped():
    """An empty power supply bay is not a failure"""
    envmon = {
        "ciscoEnvMonSupplyDescr": rows("ciscoEnvMonSupplyDescr", {"2": "Power Supply 2"}),
        "ciscoEnvMonSupplyState": rows("ciscoEnvMonSupplyState", {"2": "5"}),
    }

    assert inv.parse_cisco_envmon(envmon) == []


# --------------------------------------------------------------------------
# CPU and memory
# --------------------------------------------------------------------------


def test_processor_load_and_memory_pools():
    columns = {
        "hrProcessorLoad": rows("hrProcessorLoad", {"1": "12", "2": "8"}),
        "ciscoMemoryPoolName": rows("ciscoMemoryPoolName", {"1": "Processor"}),
        "ciscoMemoryPoolUsed": rows("ciscoMemoryPoolUsed", {"1": "300000000"}),
        "ciscoMemoryPoolFree": rows("ciscoMemoryPoolFree", {"1": "100000000"}),
    }

    readings = {r.key: r for r in inv.parse_utilisation(columns)}

    assert readings["cpu:hr:1"].value == 12
    assert readings["cpu:hr:1"].unit == "%"
    # 300M used of 400M total.
    assert readings["memory:cisco:1"].value == pytest.approx(75.0)
    assert readings["memory:cisco:1"].sensor_type == "memory"


def test_storage_reports_both_a_percentage_and_a_size():
    """
    A percentage alone cannot tell 90% of 512 MB from 90% of 2 TB

    Which is the difference between a switch that is fine and one about to
    stop logging.
    """
    columns = {
        "hrStorageDescr": rows("hrStorageDescr", {"1": "Physical memory"}),
        "hrStorageAllocationUnits": rows("hrStorageAllocationUnits", {"1": "1024"}),
        "hrStorageSize": rows("hrStorageSize", {"1": "4194304"}),
        "hrStorageUsed": rows("hrStorageUsed", {"1": "2097152"}),
    }

    readings = {r.key: r for r in inv.parse_utilisation(columns)}

    assert readings["storage:1"].value == pytest.approx(50.0)
    assert readings["storage:1"].unit == "%"
    # 2097152 units of 1024 bytes is 2048 MB.
    assert readings["storage:1:bytes"].value == pytest.approx(2048.0)
    assert readings["storage:1:bytes"].unit == "MB"
    # Recognised as memory rather than a disk, from its description.
    assert readings["storage:1"].sensor_type == "memory"


def test_a_disk_is_not_called_memory():
    columns = {
        "hrStorageDescr": rows("hrStorageDescr", {"1": "/var/log"}),
        "hrStorageAllocationUnits": rows("hrStorageAllocationUnits", {"1": "4096"}),
        "hrStorageSize": rows("hrStorageSize", {"1": "1000"}),
        "hrStorageUsed": rows("hrStorageUsed", {"1": "250"}),
    }

    readings = {r.key: r for r in inv.parse_utilisation(columns)}

    assert readings["storage:1"].sensor_type == "storage"


def test_a_zero_sized_volume_is_skipped():
    """Dividing by it would be an error report rather than a reading"""
    columns = {
        "hrStorageDescr": rows("hrStorageDescr", {"1": "Empty"}),
        "hrStorageSize": rows("hrStorageSize", {"1": "0"}),
        "hrStorageUsed": rows("hrStorageUsed", {"1": "0"}),
    }

    assert inv.parse_utilisation(columns) == []
