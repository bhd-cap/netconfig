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
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)


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
    ):
        """
        Initialize device connector

        Args:
            hostname: Device hostname
            ip_address: Device IP address
            device_type: Device OS type
            username: SSH username
            encrypted_password: Encrypted password
            port: SSH port (default: 22)
            enable_secret: Enable secret for privilege escalation (encrypted)
            ssh_key_path: Path to SSH private key
            timeout: Connection timeout in seconds
        """
        self.hostname = hostname
        self.ip_address = ip_address
        self.device_type = device_type
        self.username = username
        self.port = port
        self.ssh_key_path = ssh_key_path

        # Decrypt credentials
        try:
            self.password = encryption_service.decrypt(encrypted_password)
            self.enable_secret = (
                encryption_service.decrypt(enable_secret) if enable_secret else None
            )
        except Exception as e:
            logger.error(f"Failed to decrypt credentials for {hostname}: {str(e)}")
            raise DeviceConnectionError(f"Credential decryption failed: {str(e)}")

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

    def connect(self) -> bool:
        """
        Establish SSH connection to the device

        Returns:
            bool: True if connection successful

        Raises:
            DeviceConnectionError: If connection fails
            DeviceAuthenticationError: If authentication fails
        """
        device_params = {
            "device_type": self.netmiko_type,
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

        # Add SSH key if provided
        if self.ssh_key_path:
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
        """Close SSH connection"""
        if self.connection:
            try:
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

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
