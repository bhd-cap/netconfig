"""
SNMP client used for discovery and inventory on devices without CLI access

pysnmp is an optional dependency, imported lazily: an installation that never
uses SNMP does not pay for it, and a missing library produces a clear message
rather than an ImportError from somewhere deep inside a backup run.

pysnmp 7 is asyncio-only, while everything that calls this - the API request
handlers, the Celery tasks, the discovery worker threads - is synchronous.
Each operation therefore runs its own short-lived event loop, building the
engine and transport inside it: an SnmpEngine bound to a loop that has since
closed cannot be reused, so caching one across calls would break on the
second use.

SNMP is read-only here. It can populate topology and inventory, but it cannot
retrieve a full running configuration, so backups still need SSH or telnet.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SnmpUnavailable(RuntimeError):
    """Raised when SNMP support is requested but pysnmp is not installed"""


class SnmpError(RuntimeError):
    """Raised when an SNMP operation fails"""


# Standard MIB-2, LLDP-MIB, CDP-MIB and BRIDGE-MIB objects used for discovery.
OID = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysContact": "1.3.6.1.2.1.1.4.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
    "ifDescr": "1.3.6.1.2.1.2.2.1.2",
    "ifName": "1.3.6.1.2.1.31.1.1.1.1",
    "ifAlias": "1.3.6.1.2.1.31.1.1.1.18",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
    "lldpRemSysName": "1.0.8802.1.1.2.1.4.1.1.9",
    "lldpRemPortId": "1.0.8802.1.1.2.1.4.1.1.7",
    "lldpRemPortDesc": "1.0.8802.1.1.2.1.4.1.1.8",
    "lldpRemChassisId": "1.0.8802.1.1.2.1.4.1.1.5",
    "lldpRemSysDesc": "1.0.8802.1.1.2.1.4.1.1.10",
    "lldpLocPortId": "1.0.8802.1.1.2.1.3.7.1.3",
    "cdpCacheDeviceId": "1.3.6.1.4.1.9.9.23.1.2.1.1.6",
    "cdpCacheDevicePort": "1.3.6.1.4.1.9.9.23.1.2.1.1.7",
    "cdpCachePlatform": "1.3.6.1.4.1.9.9.23.1.2.1.1.8",
    "cdpCacheAddress": "1.3.6.1.4.1.9.9.23.1.2.1.1.4",
    "dot1dTpFdbPort": "1.3.6.1.2.1.17.4.3.1.2",
    "dot1dBasePortIfIndex": "1.3.6.1.2.1.17.1.4.1.2",
    "ipNetToMediaPhysAddress": "1.3.6.1.2.1.4.22.1.2",
}

_NO_VALUE = (
    "No Such Object currently exists at this OID",
    "No Such Instance currently exists at this OID",
    "No more variables left in this MIB View",
)


def _load_hlapi():
    """
    Import the pysnmp asyncio API, raising a clear error when it is absent

    Returns:
        The pysnmp.hlapi.v3arch.asyncio module

    Raises:
        SnmpUnavailable
    """
    try:
        from pysnmp.hlapi.v3arch import asyncio as hlapi  # type: ignore

        return hlapi
    except ImportError as e:  # pragma: no cover - depends on install
        raise SnmpUnavailable(
            "SNMP support requires the 'pysnmp' package (7.0 or newer), which "
            "is not installed. Install it with 'pip install pysnmp', or set "
            "the device transport to ssh or telnet."
        ) from e


def snmp_available() -> bool:
    """Whether SNMP support can be used in this installation"""
    try:
        _load_hlapi()
        return True
    except SnmpUnavailable:
        return False


def _run(coro):
    """
    Run a coroutine to completion from synchronous code

    Args:
        coro: Coroutine to run

    Returns:
        Its result
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Called from inside a running loop (not the normal path here): give the
    # coroutine its own loop on a separate thread rather than deadlocking.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class SnmpClient:
    """A read-only SNMP session against one device"""

    def __init__(
        self,
        host: str,
        port: int = 161,
        version: str = "2c",
        community: Optional[str] = None,
        v3_user: Optional[str] = None,
        v3_auth_key: Optional[str] = None,
        v3_priv_key: Optional[str] = None,
        v3_auth_protocol: Optional[str] = None,
        v3_priv_protocol: Optional[str] = None,
        timeout: int = 10,
        retries: int = 1,
    ):
        """
        Build an SNMP client

        Args:
            host: Device address
            port: UDP port (default 161)
            version: '1', '2c' or '3'
            community: Community string for v1/v2c
            v3_user: USM user name for v3
            v3_auth_key: v3 authentication key
            v3_priv_key: v3 privacy key
            v3_auth_protocol: 'md5', 'sha', 'sha256' ...
            v3_priv_protocol: 'des', 'aes', 'aes256' ...
            timeout: Per-request timeout in seconds
            retries: Retries per request

        Raises:
            SnmpUnavailable: If pysnmp is not installed
            SnmpError: If the parameters are incomplete
        """
        self.hlapi = _load_hlapi()
        self.host = host
        self.port = int(port)
        self.version = str(version).lower()
        self.timeout = timeout
        self.retries = retries

        self._community = community
        self._v3 = {
            "user": v3_user,
            "auth_key": v3_auth_key,
            "priv_key": v3_priv_key,
            "auth_protocol": v3_auth_protocol,
            "priv_protocol": v3_priv_protocol,
        }

        if self.version in ("1", "2c"):
            if not community:
                raise SnmpError("SNMP v1/v2c requires a community string")
        elif self.version == "3":
            if not v3_user:
                raise SnmpError("SNMP v3 requires a user name")
        else:
            raise SnmpError(f"Unsupported SNMP version '{version}'")

    # ------------------------------------------------------------------
    # pysnmp object construction (must happen inside the running loop)
    # ------------------------------------------------------------------

    def _auth_data(self):
        """Build the community or USM credentials object"""
        if self.version in ("1", "2c"):
            return self.hlapi.CommunityData(
                self._community, mpModel=0 if self.version == "1" else 1
            )

        return self.hlapi.UsmUserData(
            self._v3["user"],
            authKey=self._v3["auth_key"] or None,
            privKey=self._v3["priv_key"] or None,
            authProtocol=self._auth_protocol(self._v3["auth_protocol"]),
            privProtocol=self._priv_protocol(self._v3["priv_protocol"]),
        )

    def _auth_protocol(self, name: Optional[str]):
        """Map an authentication protocol name to the pysnmp object"""
        if not name:
            return self.hlapi.usmNoAuthProtocol

        mapping = {
            "md5": "usmHMACMD5AuthProtocol",
            "sha": "usmHMACSHAAuthProtocol",
            "sha1": "usmHMACSHAAuthProtocol",
            "sha224": "usmHMAC128SHA224AuthProtocol",
            "sha256": "usmHMAC192SHA256AuthProtocol",
            "sha384": "usmHMAC256SHA384AuthProtocol",
            "sha512": "usmHMAC384SHA512AuthProtocol",
        }
        attribute = mapping.get(name.lower(), "usmHMACSHAAuthProtocol")
        return getattr(self.hlapi, attribute, self.hlapi.usmHMACSHAAuthProtocol)

    def _priv_protocol(self, name: Optional[str]):
        """Map a privacy protocol name to the pysnmp object"""
        if not name:
            return self.hlapi.usmNoPrivProtocol

        mapping = {
            "des": "usmDESPrivProtocol",
            "3des": "usm3DESEDEPrivProtocol",
            "aes": "usmAesCfb128Protocol",
            "aes128": "usmAesCfb128Protocol",
            "aes192": "usmAesCfb192Protocol",
            "aes256": "usmAesCfb256Protocol",
        }
        attribute = mapping.get(name.lower(), "usmAesCfb128Protocol")
        return getattr(self.hlapi, attribute, self.hlapi.usmAesCfb128Protocol)

    async def _transport(self):
        """Create the UDP transport (a coroutine in pysnmp 7)"""
        return await self.hlapi.UdpTransportTarget.create(
            (self.host, self.port), timeout=self.timeout, retries=self.retries
        )

    @staticmethod
    def _usable(value: str) -> bool:
        """Whether a returned value represents a real object"""
        return bool(value) and value not in _NO_VALUE

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def get(self, oid: str) -> Optional[str]:
        """
        Fetch one scalar OID

        Args:
            oid: Object identifier

        Returns:
            The value as a string, or None when the device has no such object
        """

        async def _get():
            engine = self.hlapi.SnmpEngine()
            try:
                error_indication, error_status, _, var_binds = await self.hlapi.get_cmd(
                    engine,
                    self._auth_data(),
                    await self._transport(),
                    self.hlapi.ContextData(),
                    self.hlapi.ObjectType(self.hlapi.ObjectIdentity(oid)),
                )

                if error_indication or error_status:
                    logger.debug(
                        f"SNMP get {oid} on {self.host}: "
                        f"{error_indication or error_status}"
                    )
                    return None

                for _, value in var_binds:
                    text = str(value)
                    return text if self._usable(text) else None

                return None
            finally:
                self._close_engine(engine)

        try:
            return _run(_get())
        except Exception as e:
            logger.debug(f"SNMP get {oid} on {self.host} failed: {e}")
            return None

    def walk(self, oid: str, max_rows: int = 10000) -> List[Tuple[str, str]]:
        """
        Walk a subtree

        Args:
            oid: Root object identifier
            max_rows: Stop after this many rows, so a misbehaving agent cannot
                make a discovery run unbounded

        Returns:
            List of (oid, value) pairs
        """

        async def _walk():
            results: List[Tuple[str, str]] = []
            engine = self.hlapi.SnmpEngine()

            try:
                iterator = self.hlapi.walk_cmd(
                    engine,
                    self._auth_data(),
                    await self._transport(),
                    self.hlapi.ContextData(),
                    self.hlapi.ObjectType(self.hlapi.ObjectIdentity(oid)),
                    lexicographicMode=False,
                )

                async for error_indication, error_status, _, var_binds in iterator:
                    if error_indication or error_status:
                        logger.debug(
                            f"SNMP walk {oid} on {self.host}: "
                            f"{error_indication or error_status}"
                        )
                        break

                    for var_oid, value in var_binds:
                        text = str(value)
                        if self._usable(text):
                            results.append((str(var_oid), text))

                    if len(results) >= max_rows:
                        logger.warning(
                            f"SNMP walk of {oid} on {self.host} hit the "
                            f"{max_rows} row cap"
                        )
                        break
            finally:
                self._close_engine(engine)

            return results

        try:
            return _run(_walk())
        except Exception as e:
            logger.debug(f"SNMP walk {oid} on {self.host} failed: {e}")
            return []

    def _close_engine(self, engine) -> None:
        """Release an engine's transport dispatcher"""
        try:
            engine.close_dispatcher()
        except Exception:  # pragma: no cover - varies by pysnmp release
            try:
                engine.transportDispatcher.closeDispatcher()
            except Exception:
                pass

    def walk_named(self, name: str, max_rows: int = 10000) -> List[Tuple[str, str]]:
        """
        Walk one of the named OIDs in the OID table

        Args:
            name: Key from the OID mapping
            max_rows: Row cap

        Returns:
            List of (oid, value) pairs

        Raises:
            SnmpError: If the name is not in the OID table
        """
        oid = OID.get(name)
        if not oid:
            raise SnmpError(f"Unknown OID name '{name}'")
        return self.walk(oid, max_rows=max_rows)

    def system_info(self) -> Dict[str, Any]:
        """
        Read the standard system group

        Returns:
            dict of sysName, sysDescr, sysLocation, sysContact
        """
        return {
            "sysName": self.get(OID["sysName"]),
            "sysDescr": self.get(OID["sysDescr"]),
            "sysLocation": self.get(OID["sysLocation"]),
            "sysContact": self.get(OID["sysContact"]),
        }

    def interface_names(self) -> Dict[str, str]:
        """
        Map ifIndex to interface name

        Falls back to ifDescr where ifName is unavailable, as on older agents.

        Returns:
            dict of ifIndex (as string) to name
        """
        names: Dict[str, str] = {}

        for source in ("ifName", "ifDescr"):
            for oid, value in self.walk_named(source):
                index = oid.rsplit(".", 1)[-1]
                if value and index not in names:
                    names[index] = value
            if names:
                break

        return names

    def close(self) -> None:
        """Release resources (SNMP is connectionless; nothing is held open)"""
        return None
