"""
Device schemas for request/response validation
"""
from datetime import datetime
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field, IPvAnyAddress, model_validator

# Supported device types
DeviceType = Literal[
    "cisco_ios",
    "cisco_ios_xe",
    "cisco_nxos",
    "arista_eos",
    "fortinet",
    "juniper_junos",
    "aruba_os",
    "hp_comware",
    "hp_procurve"
]


# How the device is reached. SSH and telnet both drive the CLI through
# Netmiko; SNMP is read-only and cannot retrieve a configuration, so it is
# only useful for discovery and inventory.
Transport = Literal["ssh", "telnet", "snmp"]

SnmpVersion = Literal["1", "2c", "3"]


class DeviceBase(BaseModel):
    """Base device schema with common fields"""
    hostname: str = Field(..., min_length=1, max_length=255, description="Device hostname")
    ip_address: str = Field(..., description="Device IP address (IPv4 or IPv6)")
    device_type: DeviceType = Field(..., description="Device OS type")
    port: int = Field(default=22, ge=1, le=65535, description="SSH or telnet port")
    username: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="Login username; omit when using a vault credential",
    )
    description: str | None = Field(None, description="Device description")
    location: str | None = Field(None, max_length=255, description="Physical location")
    tags: Dict[str, Any] | None = Field(default=None, description="Custom tags/metadata")

    transport: Transport = Field(
        default="ssh", description="How to reach the device: ssh, telnet or snmp"
    )
    snmp_version: SnmpVersion | None = Field(None, description="SNMP version")
    snmp_port: int = Field(default=161, ge=1, le=65535, description="SNMP port")
    snmp_v3_user: str | None = Field(None, max_length=100)
    snmp_v3_auth_protocol: str | None = Field(None, max_length=20)
    snmp_v3_priv_protocol: str | None = Field(None, max_length=20)

    # Vault credentials, instead of storing a login on the device itself. Two
    # references because a device commonly needs both at once: SSH to back the
    # configuration up, SNMP to poll inventory.
    credential_id: int | None = Field(
        None, description="Vault CLI credential to log in with"
    )
    snmp_credential_id: int | None = Field(
        None, description="Vault SNMP credential to poll with"
    )


class SnmpCredentials(BaseModel):
    """
    Write-only SNMP secrets

    Separate from DeviceBase so a read never returns them, the same way the
    login password is handled.
    """
    snmp_community: str | None = Field(None, description="v1/v2c community (encrypted)")
    snmp_v3_auth_key: str | None = Field(None, description="v3 auth key (encrypted)")
    snmp_v3_priv_key: str | None = Field(None, description="v3 privacy key (encrypted)")


class DeviceCreate(DeviceBase, SnmpCredentials):
    """Schema for creating a device"""
    password: str | None = Field(
        None,
        min_length=1,
        description="Login password; omit when using a vault credential",
    )
    enable_secret: str | None = Field(None, description="Enable secret for Cisco devices")
    ssh_key_path: str | None = Field(None, description="Path to SSH private key")

    @model_validator(mode="after")
    def credentials_come_from_somewhere(self) -> "DeviceCreate":
        """
        A device needs a login: its own, or one from the vault

        Refused here rather than at connection time, where the failure would
        be an authentication error against a real device and would look like a
        wrong password. An SNMP-only device is exempt: it is never logged into.
        """
        if self.credential_id or self.transport == "snmp":
            return self

        missing = [
            name
            for name, value in (("username", self.username), ("password", self.password))
            if not value
        ]
        if missing:
            raise ValueError(
                f"{' and '.join(missing)} required, or choose a vault credential"
            )

        return self


class DeviceUpdate(SnmpCredentials):
    """Schema for updating a device"""
    hostname: str | None = Field(None, min_length=1, max_length=255)
    ip_address: str | None = None
    device_type: DeviceType | None = None
    port: int | None = Field(None, ge=1, le=65535)
    username: str | None = Field(None, min_length=1, max_length=100)
    password: str | None = Field(None, min_length=1, description="New password (will be encrypted)")
    enable_secret: str | None = None
    ssh_key_path: str | None = None
    description: str | None = None
    location: str | None = None
    tags: Dict[str, Any] | None = None
    is_active: bool | None = None

    transport: Transport | None = None
    snmp_version: SnmpVersion | None = None
    snmp_port: int | None = Field(None, ge=1, le=65535)
    snmp_v3_user: str | None = Field(None, max_length=100)
    snmp_v3_auth_protocol: str | None = Field(None, max_length=20)
    snmp_v3_priv_protocol: str | None = Field(None, max_length=20)

    # Passing null clears the reference and returns the device to the
    # credentials stored on itself; omitting the field leaves it alone.
    credential_id: int | None = None
    snmp_credential_id: int | None = None


class DeviceInDB(DeviceBase):
    """Schema for device in database"""
    id: int
    organization_id: int
    is_active: bool
    last_backup_at: datetime | None
    last_backup_status: str | None
    created_at: datetime
    updated_at: datetime
    created_by: int | None

    # Set when a discovery crawl registered the device rather than a person.
    discovered: bool = False
    last_discovered_at: datetime | None = None

    class Config:
        from_attributes = True


class DeviceResponse(DeviceInDB):
    """Schema for device API response (excludes encrypted credentials)"""

    # Filled in by the endpoint so the UI can show which vault entry a device
    # uses without a second request per row.
    credential_name: str | None = None
    snmp_credential_name: str | None = None


class DeviceWithBackupCount(DeviceResponse):
    """Schema for device with backup count"""
    backup_count: int = 0
    latest_config_id: int | None = None


class DeviceBulkUpload(BaseModel):
    """Schema for CSV bulk upload"""
    devices: list[DeviceCreate]


class DeviceTestConnection(BaseModel):
    """Schema for testing device connectivity"""
    success: bool
    message: str
    response_time: float | None = None
    device_info: Dict[str, Any] | None = None
