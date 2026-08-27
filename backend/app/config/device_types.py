"""
Device type configurations for supported network operating systems
"""
from typing import Dict, Any

# Device type configuration mapping
# Maps our device type identifiers to Netmiko device types and specific commands
DEVICE_TYPE_CONFIG: Dict[str, Dict[str, Any]] = {
    "cisco_ios": {
        "netmiko_type": "cisco_ios",
        "name": "Cisco IOS",
        "config_command": "show running-config",
        "enable_required": True,
        "timeout": 60,
        "description": "Cisco IOS (switches and routers)",
        "vendor": "Cisco",
    },
    "cisco_ios_xe": {
        "netmiko_type": "cisco_xe",
        "name": "Cisco IOS-XE",
        "config_command": "show running-config",
        "enable_required": True,
        "timeout": 60,
        "description": "Cisco IOS-XE (ASR, ISR 4000 series)",
        "vendor": "Cisco",
    },
    "cisco_nxos": {
        "netmiko_type": "cisco_nxos",
        "name": "Cisco NX-OS",
        "config_command": "show running-config",
        "enable_required": False,
        "timeout": 90,
        "description": "Cisco NX-OS (Nexus switches)",
        "vendor": "Cisco",
    },
    "arista_eos": {
        "netmiko_type": "arista_eos",
        "name": "Arista EOS",
        "config_command": "show running-config",
        "enable_required": False,
        "timeout": 60,
        "description": "Arista Networks EOS",
        "vendor": "Arista",
    },
    "fortinet": {
        "netmiko_type": "fortinet",
        "name": "Fortinet FortiOS",
        "config_command": "show full-configuration",
        "enable_required": False,
        "timeout": 90,
        "description": "Fortinet FortiGate firewalls",
        "vendor": "Fortinet",
    },
    "juniper_junos": {
        "netmiko_type": "juniper_junos",
        "name": "Juniper Junos",
        "config_command": "show configuration | display set",
        "enable_required": False,
        "timeout": 120,
        "description": "Juniper Networks Junos OS",
        "vendor": "Juniper",
    },
    "aruba_os": {
        "netmiko_type": "aruba_os",
        "name": "Aruba OS",
        "config_command": "show running-config",
        "enable_required": True,
        "timeout": 60,
        "description": "Aruba Networks ArubaOS (controllers and switches)",
        "vendor": "Aruba/HPE",
    },
    "hp_comware": {
        "netmiko_type": "hp_comware",
        "name": "HPE Comware",
        "config_command": "display current-configuration",
        "enable_required": False,
        "timeout": 60,
        "description": "HPE Comware (H3C switches)",
        "vendor": "HPE",
    },
    "hp_procurve": {
        "netmiko_type": "hp_procurve",
        "name": "HPE ProCurve",
        "config_command": "show running-config",
        "enable_required": False,
        "timeout": 60,
        "description": "HPE ProCurve switches",
        "vendor": "HPE",
    },
}


def get_device_config(device_type: str) -> Dict[str, Any]:
    """
    Get configuration for a specific device type

    Args:
        device_type: Device type identifier

    Returns:
        Dict: Device configuration

    Raises:
        ValueError: If device type is not supported
    """
    if device_type not in DEVICE_TYPE_CONFIG:
        raise ValueError(
            f"Unsupported device type: {device_type}. "
            f"Supported types: {', '.join(DEVICE_TYPE_CONFIG.keys())}"
        )

    return DEVICE_TYPE_CONFIG[device_type]


def get_netmiko_device_type(device_type: str) -> str:
    """
    Get Netmiko device type for a given device type

    Args:
        device_type: Device type identifier

    Returns:
        str: Netmiko device type
    """
    config = get_device_config(device_type)
    return config["netmiko_type"]


def get_config_command(device_type: str) -> str:
    """
    Get the configuration retrieval command for a device type

    Args:
        device_type: Device type identifier

    Returns:
        str: Configuration command
    """
    config = get_device_config(device_type)
    return config["config_command"]


def requires_enable(device_type: str) -> bool:
    """
    Check if device type requires enable mode

    Args:
        device_type: Device type identifier

    Returns:
        bool: True if enable mode is required
    """
    config = get_device_config(device_type)
    return config["enable_required"]


def get_timeout(device_type: str) -> int:
    """
    Get recommended timeout for device type

    Args:
        device_type: Device type identifier

    Returns:
        int: Timeout in seconds
    """
    config = get_device_config(device_type)
    return config["timeout"]


def list_supported_device_types() -> list[Dict[str, str]]:
    """
    List all supported device types with their metadata

    Returns:
        list: List of device type information
    """
    return [
        {
            "type": device_type,
            "name": config["name"],
            "vendor": config["vendor"],
            "description": config["description"],
        }
        for device_type, config in DEVICE_TYPE_CONFIG.items()
    ]


# Configuration noise patterns to filter out during comparison
# These are timestamp/uptime lines that change but don't represent config changes
CONFIG_NOISE_PATTERNS = {
    "cisco_ios": [
        r"^! Last configuration change.*",
        r"^! NVRAM config last updated.*",
        r"^ntp clock-period.*",
        r"^Current configuration.*",
    ],
    "cisco_ios_xe": [
        r"^! Last configuration change.*",
        r"^! NVRAM config last updated.*",
        r"^ntp clock-period.*",
        r"^Current configuration.*",
    ],
    "cisco_nxos": [
        r"^!Time.*",
        r"^!Command: show running-config.*",
    ],
    "arista_eos": [
        r"^! Last configuration change.*",
        r"^! device:.*",
    ],
    "fortinet": [
        r"^#config-version.*",
        r"^#conf_file_ver.*",
    ],
    "juniper_junos": [
        r"^## Last commit.*",
        r"^## Last changed.*",
    ],
    "aruba_os": [
        r"^; Last configuration change.*",
    ],
    "hp_comware": [
        r"^# Last modified time.*",
    ],
    "hp_procurve": [
        r"^; Last modified time.*",
    ],
}


def get_noise_patterns(device_type: str) -> list[str]:
    """
    Get regex patterns for configuration noise to filter during comparison

    Args:
        device_type: Device type identifier

    Returns:
        list: List of regex patterns
    """
    return CONFIG_NOISE_PATTERNS.get(device_type, [])
