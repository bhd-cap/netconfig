"""
CSV parser for bulk device upload
"""
import csv
from io import StringIO
from typing import List, Dict, Any, Tuple
import logging

from app.schemas.device import DeviceCreate
from app.config.device_types import DEVICE_TYPE_CONFIG

logger = logging.getLogger(__name__)


class CSVParseError(Exception):
    """Exception raised for CSV parsing errors"""
    pass


def parse_device_csv(csv_content: str) -> Tuple[List[DeviceCreate], List[Dict[str, Any]]]:
    """
    Parse CSV content and create device objects

    CSV Format:
    hostname,ip_address,device_type,username,password,port,description,location,enable_secret

    Args:
        csv_content: CSV file content as string

    Returns:
        Tuple of (valid_devices, errors)
        - valid_devices: List of DeviceCreate objects
        - errors: List of error dictionaries with row number and error message

    Raises:
        CSVParseError: If CSV format is invalid
    """
    valid_devices = []
    errors = []

    # Required fields
    required_fields = {"hostname", "ip_address", "device_type", "username", "password"}

    try:
        csv_reader = csv.DictReader(StringIO(csv_content))

        # Validate headers
        if not csv_reader.fieldnames:
            raise CSVParseError("CSV file is empty or has no headers")

        headers = set(csv_reader.fieldnames)
        missing_fields = required_fields - headers

        if missing_fields:
            raise CSVParseError(
                f"Missing required columns: {', '.join(missing_fields)}"
            )

        # Process each row
        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (header is row 1)
            try:
                # Validate required fields are not empty
                for field in required_fields:
                    if not row.get(field, "").strip():
                        raise ValueError(f"Missing required field: {field}")

                # Validate device type
                device_type = row["device_type"].strip()
                if device_type not in DEVICE_TYPE_CONFIG:
                    raise ValueError(
                        f"Invalid device_type '{device_type}'. "
                        f"Valid types: {', '.join(DEVICE_TYPE_CONFIG.keys())}"
                    )

                # Parse port (default 22)
                port = 22
                if row.get("port"):
                    try:
                        port = int(row["port"])
                        if port < 1 or port > 65535:
                            raise ValueError("Port must be between 1 and 65535")
                    except ValueError as e:
                        raise ValueError(f"Invalid port: {e}")

                # Create device object
                device = DeviceCreate(
                    hostname=row["hostname"].strip(),
                    ip_address=row["ip_address"].strip(),
                    device_type=device_type,
                    username=row["username"].strip(),
                    password=row["password"].strip(),
                    port=port,
                    description=row.get("description", "").strip() or None,
                    location=row.get("location", "").strip() or None,
                    enable_secret=row.get("enable_secret", "").strip() or None,
                    ssh_key_path=row.get("ssh_key_path", "").strip() or None,
                    tags=None,  # Tags can be added later through API
                )

                valid_devices.append(device)

            except ValueError as e:
                errors.append({
                    "row": row_num,
                    "data": row,
                    "error": str(e),
                })
                logger.warning(f"Error parsing row {row_num}: {e}")

            except Exception as e:
                errors.append({
                    "row": row_num,
                    "data": row,
                    "error": f"Unexpected error: {str(e)}",
                })
                logger.error(f"Unexpected error parsing row {row_num}: {e}")

    except csv.Error as e:
        raise CSVParseError(f"CSV parsing error: {str(e)}")

    return valid_devices, errors


def generate_csv_template() -> str:
    """
    Generate a CSV template for device bulk upload

    Returns:
        str: CSV template content
    """
    template_rows = [
        # Header
        ["hostname", "ip_address", "device_type", "username", "password", "port", "description", "location", "enable_secret"],
        # Example rows
        ["router-nyc-01", "192.168.1.1", "cisco_ios", "admin", "P@ssw0rd123", "22", "NYC Core Router", "New York DC", "EnableSecret"],
        ["switch-la-01", "192.168.2.10", "arista_eos", "admin", "P@ssw0rd123", "22", "LA Access Switch", "Los Angeles DC", ""],
        ["fw-chi-01", "192.168.3.20", "fortinet", "admin", "P@ssw0rd123", "22", "Chicago Firewall", "Chicago DC", ""],
    ]

    output = StringIO()
    writer = csv.writer(output)
    writer.writerows(template_rows)

    return output.getvalue()


def validate_csv_headers(csv_content: str) -> bool:
    """
    Validate CSV has correct headers

    Args:
        csv_content: CSV file content

    Returns:
        bool: True if headers are valid

    Raises:
        CSVParseError: If headers are invalid
    """
    required_fields = {"hostname", "ip_address", "device_type", "username", "password"}

    try:
        csv_reader = csv.DictReader(StringIO(csv_content))

        if not csv_reader.fieldnames:
            raise CSVParseError("CSV file is empty or has no headers")

        headers = set(csv_reader.fieldnames)
        missing_fields = required_fields - headers

        if missing_fields:
            raise CSVParseError(
                f"Missing required columns: {', '.join(missing_fields)}"
            )

        return True

    except csv.Error as e:
        raise CSVParseError(f"CSV parsing error: {str(e)}")
