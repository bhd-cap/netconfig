"""
SSH Device Connection Manager using Netmiko
"""
import time
from typing import Dict, Any, Optional
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
import logging

from app.config.device_types import (
    get_device_config,
    get_netmiko_device_type,
    get_config_command,
    requires_enable,
    get_timeout,
)
from app.config.discovery_commands import (
    SUPPORTED_TRANSPORTS,
    get_discovery_command,
    get_telnet_device_type,
)
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)


def snmp_params(device) -> Dict[str, Any]:
    """
    Collect a device's SNMP columns into the shape DeviceConnector expects

    Lives here rather than beside any one caller because the backup path, the
    connectivity test and discovery all need the same mapping, and the keys
    are this class's contract.

    Args:
        device: A Device row

    Returns:
        dict of SNMP parameters, secrets still encrypted
    """
    return {
        "version": device.snmp_version,
        "community": device.snmp_community,
        "port": device.snmp_port,
        "v3_user": device.snmp_v3_user,
        "v3_auth_key": device.snmp_v3_auth_key,
        "v3_priv_key": device.snmp_v3_priv_key,
        "v3_auth_protocol": device.snmp_v3_auth_protocol,
        "v3_priv_protocol": device.snmp_v3_priv_protocol,
    }


class DeviceConnectionError(Exception):
    """Exception raised for device connection errors"""
    pass


class DeviceAuthenticationError(Exception):
    """Exception raised for device authentication errors"""
    pass


class DeviceCommandError(Exception):
    """Exception raised for command execution errors"""
    pass


class DeviceConnector:
    """Manages SSH connections to network devices"""

    def __init__(
        self,
        hostname: str,
        ip_address: str,
        device_type: str,
        username: str,
        encrypted_password: str,
        port: int = 22,
        enable_secret: Optional[str] = None,
        ssh_key_path: Optional[str] = None,
        timeout: Optional[int] = None,
        transport: str = "ssh",
        snmp: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize device connector

        Args:
            hostname: Device hostname
            ip_address: Device IP address
            device_type: Device OS type
            username: Login username
            encrypted_password: Encrypted password
            port: TCP port for the CLI transport (default: 22)
            enable_secret: Enable secret for privilege escalation (encrypted)
            ssh_key_path: Path to SSH private key
            timeout: Connection timeout in seconds
            transport: 'ssh', 'telnet' or 'snmp'
            snmp: SNMP parameters when transport is 'snmp' - version,
                community (encrypted), port, and the v3 user and keys
        """
        self.hostname = hostname
        self.ip_address = ip_address
        self.device_type = device_type
        self.username = username
        self.port = port
        self.ssh_key_path = ssh_key_path

        transport = (transport or "ssh").lower()
        if transport not in SUPPORTED_TRANSPORTS:
            raise DeviceConnectionError(
                f"Unsupported transport '{transport}'; "
                f"expected one of {', '.join(SUPPORTED_TRANSPORTS)}"
            )
        self.transport = transport
        self.snmp = snmp or {}

        # Decrypt credentials. An SNMP-only device has no login password, so
        # an empty value is not an error there.
        try:
            self.password = (
                encryption_service.decrypt(encrypted_password)
                if encrypted_password
                else ""
            )
            self.enable_secret = (
                encryption_service.decrypt(enable_secret) if enable_secret else None
            )
        except Exception as e:
            logger.error(f"Failed to decrypt credentials for {hostname}: {str(e)}")
            raise DeviceConnectionError(f"Credential decryption failed: {str(e)}")

        if self.transport == "snmp":
            self._decrypt_snmp_credentials()

        # Get device-specific configuration
        try:
            self.device_config = get_device_config(device_type)
            self.netmiko_type = get_netmiko_device_type(device_type)
            self.config_command = get_config_command(device_type)
            self.requires_enable = requires_enable(device_type)
            self.timeout = timeout or get_timeout(device_type)
        except ValueError as e:
            logger.error(f"Invalid device type {device_type}: {str(e)}")
            raise DeviceConnectionError(str(e))

        self.connection = None

    def _decrypt_snmp_credentials(self) -> None:
        """Decrypt the SNMP secrets held in self.snmp, in place"""
        for key in ("community", "v3_auth_key", "v3_priv_key"):
            value = self.snmp.get(key)
            if value:
                try:
                    self.snmp[key] = encryption_service.decrypt(value)
                except Exception as e:
                    raise DeviceConnectionError(
                        f"SNMP credential decryption failed for {self.hostname}: {e}"
                    )

    def connect(self) -> bool:
        """
        Establish a connection to the device using the configured transport

        Returns:
            bool: True if connection successful

        Raises:
            DeviceConnectionError: If connection fails
            DeviceAuthenticationError: If authentication fails
        """
        if self.transport == "snmp":
            return self._connect_snmp()

        device_params = {
            "device_type": self._cli_driver(),
            "host": self.ip_address,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "timeout": self.timeout,
            "session_timeout": self.timeout,
        }

        # Add enable secret if required
        if self.requires_enable and self.enable_secret:
            device_params["secret"] = self.enable_secret

        # Add SSH key if provided. Telnet has no key auth.
        if self.ssh_key_path and self.transport == "ssh":
            device_params["key_file"] = self.ssh_key_path

        try:
            logger.info(f"Connecting to {self.hostname} ({self.ip_address})")
            self.connection = ConnectHandler(**device_params)

            # Enter enable mode if required
            if self.requires_enable:
                self.connection.enable()

            logger.info(f"Successfully connected to {self.hostname}")
            return True

        except NetmikoAuthenticationException as e:
            error_msg = f"Authentication failed for {self.hostname}: {str(e)}"
            logger.error(error_msg)
            raise DeviceAuthenticationError(error_msg)

        except NetmikoTimeoutException as e:
            error_msg = f"Connection timeout for {self.hostname}: {str(e)}"
            logger.error(error_msg)
            raise DeviceConnectionError(error_msg)

        except Exception as e:
            error_msg = f"Connection failed for {self.hostname}: {str(e)}"
            logger.error(error_msg)
            raise DeviceConnectionError(error_msg)

    def disconnect(self):
        """Close the connection"""
        if self.connection:
            try:
                if self.transport == "snmp":
                    self.connection.close()
                else:
                    self.connection.disconnect()
                logger.info(f"Disconnected from {self.hostname}")
            except Exception as e:
                logger.warning(f"Error disconnecting from {self.hostname}: {str(e)}")
            finally:
                self.connection = None

    def get_running_config(self) -> str:
        """
        Retrieve running configuration from device

        Returns:
            str: Running configuration text

        Raises:
            DeviceConnectionError: If not connected
            DeviceCommandError: If command execution fails
        """
        if not self.connection:
            raise DeviceConnectionError("Not connected to device")

        try:
            logger.info(f"Retrieving configuration from {self.hostname}")
            start_time = time.time()

            output = self.connection.send_command(
                self.config_command,
                expect_string=r"#",
                read_timeout=self.timeout,
            )

            duration = time.time() - start_time
            logger.info(
                f"Retrieved configuration from {self.hostname} "
                f"({len(output)} bytes in {duration:.2f}s)"
            )

            return output

        except Exception as e:
            error_msg = f"Failed to retrieve config from {self.hostname}: {str(e)}"
            logger.error(error_msg)
            raise DeviceCommandError(error_msg)

    def test_connection(self) -> Dict[str, Any]:
        """
        Test device connectivity and gather basic info

        Returns:
            Dict: Connection test results with device info
        """
        result = {
            "success": False,
            "message": "",
            "response_time": None,
            "device_info": {},
        }

        start_time = time.time()

        try:
            # Attempt connection
            self.connect()
            response_time = time.time() - start_time

            # Get basic device info
            device_info = {}
            try:
                # Try to get hostname
                if self.device_type.startswith("cisco"):
                    hostname_output = self.connection.send_command("show version | include hostname")
                    device_info["hostname_check"] = hostname_output.strip()
                elif self.device_type == "juniper_junos":
                    hostname_output = self.connection.send_command("show version | match Hostname")
                    device_info["hostname_check"] = hostname_output.strip()
            except Exception:
                pass  # Hostname check is optional

            result["success"] = True
            result["message"] = f"Successfully connected to {self.hostname}"
            result["response_time"] = round(response_time, 2)
            result["device_info"] = device_info

        except DeviceAuthenticationError as e:
            result["message"] = str(e)
        except DeviceConnectionError as e:
            result["message"] = str(e)
        except Exception as e:
            result["message"] = f"Unexpected error: {str(e)}"
        finally:
            self.disconnect()

        return result

    # ----------------------------------------------------------------
    # Transport helpers
    # ----------------------------------------------------------------

    def _cli_driver(self) -> str:
        """
        The Netmiko driver to use for the configured CLI transport

        Returns:
            str: Netmiko device type
        """
        if self.transport == "telnet":
            return get_telnet_device_type(self.device_type)
        return self.netmiko_type

    def _connect_snmp(self) -> bool:
        """
        Prepare an SNMP session

        SNMP is connectionless, so this validates the parameters and confirms
        the device answers rather than holding a socket open.

        Returns:
            bool: True when the device responds

        Raises:
            DeviceConnectionError: If SNMP is unavailable or the device is silent
        """
        client = self._snmp_client()

        # sysName.0 - every agent implements it, so it doubles as a reachability
        # check and tells us the device's own idea of its hostname.
        value = client.get("1.3.6.1.2.1.1.5.0")
        if value is None:
            raise DeviceConnectionError(
                f"No SNMP response from {self.hostname} ({self.ip_address}:"
                f"{self.snmp.get('port', 161)})"
            )

        self.connection = client
        self.snmp_sysname = value
        logger.info(f"SNMP reachable: {self.hostname} reports sysName '{value}'")
        return True

    def _snmp_client(self):
        """
        Build an SNMP client from the configured parameters

        Returns:
            SnmpClient

        Raises:
            DeviceConnectionError: If pysnmp is not installed
        """
        from app.services.snmp_client import SnmpClient, SnmpUnavailable

        try:
            return SnmpClient(
                host=self.ip_address,
                port=int(self.snmp.get("port") or 161),
                version=str(self.snmp.get("version") or "2c"),
                community=self.snmp.get("community"),
                v3_user=self.snmp.get("v3_user"),
                v3_auth_key=self.snmp.get("v3_auth_key"),
                v3_priv_key=self.snmp.get("v3_priv_key"),
                v3_auth_protocol=self.snmp.get("v3_auth_protocol"),
                v3_priv_protocol=self.snmp.get("v3_priv_protocol"),
                timeout=self.timeout,
            )
        except SnmpUnavailable as e:
            raise DeviceConnectionError(str(e))

    def send_command(self, command: str, read_timeout: Optional[int] = None) -> str:
        """
        Run an arbitrary command and return its output

        Used by discovery, which issues several commands over one session.

        Args:
            command: Command to run
            read_timeout: Override the read timeout

        Returns:
            str: Command output ('' when the transport cannot run commands)

        Raises:
            DeviceConnectionError: If not connected
            DeviceCommandError: If the command fails
        """
        if not self.connection:
            raise DeviceConnectionError("Not connected to device")

        # SNMP has no command shell; callers use snmp_walk instead.
        if self.transport == "snmp":
            return ""

        try:
            return self.connection.send_command(
                command, read_timeout=read_timeout or self.timeout
            )
        except Exception as e:
            raise DeviceCommandError(
                f"Command '{command}' failed on {self.hostname}: {e}"
            )

    def supports_cli(self) -> bool:
        """Whether this transport can run CLI commands"""
        return self.transport in ("ssh", "telnet")

    def get_discovery_output(self, capability: str) -> Optional[str]:
        """
        Run the discovery command for one capability

        Args:
            capability: 'lldp', 'cdp', 'mac' or 'arp'

        Returns:
            Raw output, or None when this device type or transport cannot
            provide it
        """
        spec = get_discovery_command(self.device_type, capability)
        if not spec or not self.supports_cli():
            return None

        try:
            return self.send_command(spec["command"])
        except DeviceCommandError as e:
            # A device that does not run LLDP answers with an error; that is
            # a normal outcome, not a failure of the run.
            logger.info(f"{self.hostname}: {capability} unavailable ({e})")
            return None

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
