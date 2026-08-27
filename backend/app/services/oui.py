"""
MAC address to vendor lookup using the public IEEE OUI registry

The registry is imported into the oui_vendors table and cached in memory, so
resolving a vendor during an inventory sweep costs a dict lookup rather than
a query per host.

Three sources, in order of preference:
  1. the database table, populated by a previous import
  2. a bundled fallback file, so a fresh install with no internet still
     labels the vendors it is most likely to meet
  3. the IEEE registry over HTTP, when an administrator asks for a refresh

Randomly assigned (locally administered) addresses are recognised and
reported as such rather than looked up: modern phones and laptops rotate
them, and reporting a stale vendor for one is worse than saying nothing.
"""
import csv
import io
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.network import OuiVendor

logger = logging.getLogger(__name__)

# The IEEE MA-L registry. Only fetched when an administrator asks.
IEEE_OUI_CSV_URL = "https://standards-oui.ieee.org/oui/oui.csv"

# Shipped with the application so a brand new install is useful offline.
BUNDLED_OUI_PATH = Path(__file__).resolve().parent.parent / "data" / "oui_common.csv"

LOCALLY_ADMINISTERED = "Locally administered (randomised)"
MULTICAST = "Multicast"


class OuiLookup:
    """In-process cache over the oui_vendors table"""

    def __init__(self):
        self._vendors: Dict[str, str] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def load(self, db: Session, force: bool = False) -> int:
        """
        Load the OUI table into memory

        Args:
            db: Database session
            force: Reload even if already loaded

        Returns:
            Number of prefixes held
        """
        with self._lock:
            if self._loaded and not force:
                return len(self._vendors)

            rows = db.execute(select(OuiVendor.oui, OuiVendor.vendor_name)).all()
            self._vendors = {row.oui: row.vendor_name for row in rows}
            self._loaded = True

        logger.info(f"OUI cache loaded with {len(self._vendors)} prefixes")
        return len(self._vendors)

    def invalidate(self) -> None:
        """Drop the cache so the next lookup reloads it"""
        with self._lock:
            self._vendors = {}
            self._loaded = False

    @staticmethod
    def _prefix(mac: str) -> Optional[str]:
        """First three octets of a normalised MAC, as six hex characters"""
        if not mac:
            return None

        cleaned = "".join(character for character in mac.lower() if character in "0123456789abcdef")
        return cleaned[:6] if len(cleaned) >= 6 else None

    @staticmethod
    def is_locally_administered(mac: str) -> bool:
        """
        Whether a MAC is randomised rather than burned in

        Bit 1 of the first octet marks a locally administered address, which
        is what phones and laptops use for privacy. There is no vendor to
        look up for one.
        """
        prefix = OuiLookup._prefix(mac)
        if not prefix:
            return False

        try:
            first_octet = int(prefix[:2], 16)
        except ValueError:
            return False

        return bool(first_octet & 0b10)

    @staticmethod
    def is_multicast(mac: str) -> bool:
        """Whether a MAC is a group address (bit 0 of the first octet)"""
        prefix = OuiLookup._prefix(mac)
        if not prefix:
            return False

        try:
            return bool(int(prefix[:2], 16) & 0b1)
        except ValueError:
            return False

    def lookup(self, mac: str, db: Optional[Session] = None) -> Optional[str]:
        """
        Resolve a MAC to a vendor name

        Args:
            mac: MAC address in any notation
            db: Session used to load the cache on first use

        Returns:
            Vendor name, a marker for randomised or multicast addresses, or
            None when the prefix is unknown
        """
        prefix = self._prefix(mac)
        if not prefix:
            return None

        if self.is_multicast(mac):
            return MULTICAST
        if self.is_locally_administered(mac):
            return LOCALLY_ADMINISTERED

        if not self._loaded and db is not None:
            self.load(db)

        return self._vendors.get(prefix)

    def lookup_many(
        self, macs: Iterable[str], db: Optional[Session] = None
    ) -> Dict[str, Optional[str]]:
        """
        Resolve many MACs at once

        Args:
            macs: MAC addresses
            db: Session used to load the cache on first use

        Returns:
            dict of MAC to vendor name
        """
        if not self._loaded and db is not None:
            self.load(db)

        return {mac: self.lookup(mac) for mac in macs}

    @property
    def size(self) -> int:
        """How many prefixes are cached"""
        return len(self._vendors)


# Process-wide cache. Discovery resolves thousands of MACs per run.
oui_lookup = OuiLookup()


# --------------------------------------------------------------------------
# Importing
# --------------------------------------------------------------------------


def parse_ieee_csv(content: str) -> List[Tuple[str, str]]:
    """
    Parse the IEEE MA-L registry CSV

    Columns are Registry, Assignment, Organization Name, Organization Address.

    Args:
        content: CSV text

    Returns:
        List of (prefix, vendor name)
    """
    entries: List[Tuple[str, str]] = []

    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        assignment = (row.get("Assignment") or "").strip().lower()
        organization = (row.get("Organization Name") or "").strip()

        if len(assignment) != 6 or not organization:
            continue
        if not all(character in "0123456789abcdef" for character in assignment):
            continue

        entries.append((assignment, organization[:255]))

    return entries


def parse_simple_csv(content: str) -> List[Tuple[str, str]]:
    """
    Parse the bundled two-column 'prefix,vendor' file

    Args:
        content: CSV text

    Returns:
        List of (prefix, vendor name)
    """
    entries: List[Tuple[str, str]] = []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(",", 1)
        if len(parts) != 2:
            continue

        prefix = parts[0].strip().lower().replace(":", "").replace("-", "")
        vendor = parts[1].strip().strip('"')

        if len(prefix) == 6 and vendor:
            entries.append((prefix, vendor[:255]))

    return entries


def import_entries(db: Session, entries: List[Tuple[str, str]]) -> int:
    """
    Upsert OUI entries

    Args:
        db: Database session
        entries: (prefix, vendor name) pairs

    Returns:
        Number of rows written
    """
    if not entries:
        return 0

    # Lookups normalise a MAC to lowercase hex, so a prefix stored in any
    # other form is a row that can never match. The IEEE registry writes its
    # prefixes in uppercase, so normalise here rather than trusting every
    # caller to remember.
    entries = [
        (
            "".join(c for c in (prefix or "").lower() if c in "0123456789abcdef")[:6],
            vendor,
        )
        for prefix, vendor in entries
    ]
    entries = [(prefix, vendor) for prefix, vendor in entries if len(prefix) == 6]

    if not entries:
        return 0

    written = 0
    # Chunked so a full registry import (well over 30k rows) does not build
    # one enormous statement.
    chunk_size = 1000

    for start in range(0, len(entries), chunk_size):
        chunk = entries[start : start + chunk_size]

        # Deduplicate within the chunk: ON CONFLICT cannot update the same
        # row twice in one statement.
        deduped = {prefix: vendor for prefix, vendor in chunk}

        statement = pg_insert(OuiVendor).values(
            [
                {"oui": prefix, "vendor_name": vendor}
                for prefix, vendor in deduped.items()
            ]
        )
        statement = statement.on_conflict_do_update(
            index_elements=[OuiVendor.oui],
            set_={"vendor_name": statement.excluded.vendor_name},
        )

        db.execute(statement)
        written += len(deduped)

    db.commit()
    oui_lookup.invalidate()

    logger.info(f"Imported {written} OUI prefixes")
    return written


def import_bundled(db: Session) -> int:
    """
    Import the OUI list shipped with the application

    Args:
        db: Database session

    Returns:
        Number of rows written
    """
    if not BUNDLED_OUI_PATH.exists():
        logger.warning(f"No bundled OUI file at {BUNDLED_OUI_PATH}")
        return 0

    content = BUNDLED_OUI_PATH.read_text(encoding="utf-8")
    return import_entries(db, parse_simple_csv(content))


# Locations where a full OUI list often already exists on a Linux host, so an
# air-gapped deployment can import without reaching the internet.
SYSTEM_OUI_PATHS = (
    "/usr/share/ieee-data/oui.txt",
    "/usr/share/wireshark/manuf",
    "/usr/share/nmap/nmap-mac-prefixes",
    "/var/lib/ieee-data/oui.txt",
)


def parse_ieee_txt(content: str) -> List[Tuple[str, str]]:
    """
    Parse the IEEE oui.txt format

    Lines look like:
        00-00-0C   (hex)\t\tCisco Systems, Inc

    Args:
        content: File text

    Returns:
        List of (prefix, vendor name)
    """
    entries: List[Tuple[str, str]] = []
    pattern = re.compile(
        r"^\s*([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+?)\s*$"
    )

    for line in content.splitlines():
        match = pattern.match(line)
        if match:
            prefix = "".join(match.group(i) for i in (1, 2, 3)).lower()
            entries.append((prefix, match.group(4)[:255]))

    return entries


def parse_manuf(content: str) -> List[Tuple[str, str]]:
    """
    Parse the Wireshark 'manuf' and nmap 'nmap-mac-prefixes' formats

    manuf:              00:00:0C\tCisco\tCisco Systems, Inc
    nmap-mac-prefixes:  00000C Cisco Systems

    Args:
        content: File text

    Returns:
        List of (prefix, vendor name)
    """
    entries: List[Tuple[str, str]] = []

    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        parts = re.split(r"[\t ]+", line, maxsplit=2)
        if len(parts) < 2:
            continue

        prefix = parts[0].replace(":", "").replace("-", "").lower()
        # manuf also lists longer prefixes (28/36 bit); only MA-L fits here.
        if len(prefix) != 6 or not re.fullmatch(r"[0-9a-f]{6}", prefix):
            continue

        # Prefer the long name where the format provides one.
        vendor = (parts[2] if len(parts) > 2 else parts[1]).strip()
        if vendor:
            entries.append((prefix, vendor[:255]))

    return entries


def parse_any(content: str) -> List[Tuple[str, str]]:
    """
    Parse whichever OUI format the content is in

    Args:
        content: File text

    Returns:
        List of (prefix, vendor name)
    """
    for parser in (parse_ieee_csv, parse_ieee_txt, parse_manuf, parse_simple_csv):
        try:
            entries = parser(content)
        except Exception:  # noqa: BLE001 - try the next format
            continue
        if entries:
            return entries

    return []


def import_from_file(db: Session, path: str) -> int:
    """
    Import an OUI list from a local file, in any supported format

    Args:
        db: Database session
        path: File path

    Returns:
        Number of rows written

    Raises:
        RuntimeError: If the file is missing or has no usable entries
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise RuntimeError(f"No such OUI file: {path}")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    entries = parse_any(content)

    if not entries:
        raise RuntimeError(f"No usable OUI entries found in {path}")

    return import_entries(db, entries)


def find_system_oui_file() -> Optional[str]:
    """
    Locate an OUI list already present on this host

    Returns:
        Path to the first one found, or None
    """
    for candidate in SYSTEM_OUI_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def import_from_system(db: Session) -> int:
    """
    Import from an OUI list already installed on this host

    Args:
        db: Database session

    Returns:
        Number of rows written

    Raises:
        RuntimeError: If no system OUI file exists
    """
    path = find_system_oui_file()
    if not path:
        raise RuntimeError(
            "No system OUI database found. Install one with "
            "'apt-get install ieee-data' or 'apt-get install wireshark-common', "
            "or import from a URL or uploaded file."
        )

    logger.info(f"Importing OUI data from {path}")
    return import_from_file(db, path)


def import_from_url(db: Session, url: str, timeout: int = 120) -> int:
    """
    Download and import an OUI list from any URL

    Args:
        db: Database session
        url: Source URL
        timeout: HTTP timeout in seconds

    Returns:
        Number of rows written

    Raises:
        RuntimeError: If the download fails or has no usable entries
    """
    import urllib.request

    if not url.lower().startswith(("http://", "https://")):
        raise RuntimeError("The OUI source URL must be http or https")

    logger.info(f"Downloading OUI data from {url}")

    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "netconfig-backup/1.0"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Could not download {url}: {e}")

    entries = parse_any(content)
    if not entries:
        raise RuntimeError(f"The download from {url} contained no usable entries")

    return import_entries(db, entries)


def import_from_ieee(db: Session, timeout: int = 120) -> int:
    """
    Download and import the full IEEE registry

    Args:
        db: Database session
        timeout: HTTP timeout in seconds

    Returns:
        Number of rows written

    Raises:
        RuntimeError: If the registry cannot be fetched
    """
    import urllib.request

    logger.info(f"Downloading the IEEE OUI registry from {IEEE_OUI_CSV_URL}")

    try:
        request = urllib.request.Request(
            IEEE_OUI_CSV_URL, headers={"User-Agent": "netconfig-backup/1.0"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Could not download the IEEE OUI registry: {e}")

    entries = parse_ieee_csv(content)
    if not entries:
        raise RuntimeError("The IEEE registry download contained no usable entries")

    return import_entries(db, entries)


def ensure_populated(db: Session) -> int:
    """
    Make sure some OUI data is present, importing the bundled list if not

    Called on first use so inventory shows vendors without an administrator
    having to do anything.

    Args:
        db: Database session

    Returns:
        Number of prefixes available
    """
    count = db.scalar(select(OuiVendor.oui).limit(1))
    if count:
        return oui_lookup.load(db)

    imported = import_bundled(db)
    if imported:
        logger.info(f"Seeded the OUI table with {imported} bundled prefixes")

    return oui_lookup.load(db, force=True)


def clear(db: Session) -> None:
    """Remove every OUI entry (used by tests and by a forced re-import)"""
    db.execute(delete(OuiVendor))
    db.commit()
    oui_lookup.invalidate()
