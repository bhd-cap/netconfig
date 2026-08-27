"""
Neighbour discovery and host inventory collection

Two things happen here:

* Probing a device: connect once, run whichever of the LLDP, CDP, MAC and ARP
  commands its platform supports, and parse the output.
* Crawling: start from one seed device and walk outward through the
  neighbours it reports, optionally registering devices that are found but
  not yet managed.

Everything that touches the network happens off the database session, in the
same pattern the backup path uses: worker threads collect, and one thread
writes. ORM objects are not shared between threads.

Records are upserted on their natural key so a link or a host that keeps
being seen updates last_seen rather than accumulating duplicate rows. That is
what makes "when did this first appear" and "when did it last respond"
answerable, which is the point of the inventory.
"""
import ipaddress
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, or_, select, update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config.discovery_commands import get_capabilities, supports_discovery
from app.core.config import settings
from app.models.credential import Credential as CredentialModel, DeviceProbe
from app.models.device import Device
from app.models.network import DiscoveryRun, HostInventory, Neighbor
from app.services import credentials as vault, discovery_probe as probe, parsers
from app.services.device_connector import (
    DeviceConnectionError,
    DeviceCommandError,
    DeviceConnector,
    snmp_params,
)
from app.services.config_retriever import DeviceSnapshot
from app.services.oui import ensure_populated, oui_lookup
from app.utils.encryption import encryption_service

logger = logging.getLogger(__name__)


def _safe_decrypt(value: Optional[str]) -> Optional[str]:
    """
    Decrypt a stored secret, treating a failure as absent

    A credential that cannot be decrypted must not abort a crawl: the run
    should carry on with the credentials that do work and report the device as
    unauthenticated.
    """
    if not value:
        return None
    try:
        return encryption_service.decrypt(value)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not decrypt a stored secret during discovery: {e}")
        return None


@dataclass
class ProbeResult:
    """What one device reported"""

    snapshot: DeviceSnapshot
    neighbors: List[parsers.Neighbor] = field(default_factory=list)
    mac_entries: List[parsers.MacEntry] = field(default_factory=list)
    arp_entries: List[parsers.ArpEntry] = field(default_factory=list)
    sysname: Optional[str] = None
    error: Optional[str] = None
    duration: float = 0.0


@dataclass
class CrawlSummary:
    """Outcome of a discovery crawl"""

    run_id: Optional[int] = None
    devices_probed: int = 0
    devices_failed: int = 0
    neighbors_found: int = 0
    hosts_found: int = 0
    devices_created: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)
    unmanaged: List[Dict[str, str]] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Kept as a module-level alias: the mapping belongs with DeviceConnector, but
# this module and its tests have always reached for it under this name.
_snapshot_snmp = snmp_params


class DiscoveryService:
    """Collects adjacencies and host inventory from managed devices"""

    def __init__(self, db: Session):
        """
        Args:
            db: Database session, used only on the calling thread
        """
        self.db = db

    # ------------------------------------------------------------------
    # Probing (no database access - safe on a worker thread)
    # ------------------------------------------------------------------

    @staticmethod
    def probe(
        snapshot: DeviceSnapshot,
        device_type: str,
        transport: str = "ssh",
        snmp: Optional[Dict[str, object]] = None,
        capabilities: Optional[Sequence[str]] = None,
        timeout: Optional[int] = None,
    ) -> ProbeResult:
        """
        Connect to one device and collect everything it can report

        One session serves every command, because opening an SSH session per
        command is the slowest part of a crawl by a wide margin.

        Args:
            snapshot: Device connection details
            device_type: Device OS type
            transport: 'ssh', 'telnet' or 'snmp'
            snmp: SNMP parameters when transport is 'snmp'
            capabilities: Restrict to these capabilities
            timeout: Connection timeout override

        Returns:
            ProbeResult, with error set rather than raising
        """
        started = time.perf_counter()
        result = ProbeResult(snapshot=snapshot)

        wanted = list(capabilities or get_capabilities(device_type))
        if not wanted:
            result.error = f"No discovery commands defined for {device_type}"
            return result

        try:
            connector = DeviceConnector(
                hostname=snapshot.hostname,
                ip_address=snapshot.ip_address,
                device_type=device_type,
                username=snapshot.username,
                encrypted_password=snapshot.encrypted_password,
                port=snapshot.port,
                enable_secret=snapshot.enable_secret,
                ssh_key_path=snapshot.ssh_key_path,
                transport=transport,
                snmp=snmp,
                timeout=timeout,
            )
        except DeviceConnectionError as e:
            result.error = str(e)
            result.duration = time.perf_counter() - started
            return result

        try:
            with connector:
                if connector.transport == "snmp":
                    result.sysname = getattr(connector, "snmp_sysname", None)
                    DiscoveryService._probe_snmp(connector, result)
                else:
                    DiscoveryService._probe_cli(connector, result, wanted)
        except (DeviceConnectionError, DeviceCommandError) as e:
            result.error = str(e)
        except Exception as e:  # noqa: BLE001 - one device must not stop a crawl
            logger.exception(f"Unexpected error probing {snapshot.hostname}")
            result.error = f"Unexpected error: {e}"

        result.duration = time.perf_counter() - started
        return result

    @staticmethod
    def _probe_cli(
        connector: DeviceConnector, result: ProbeResult, wanted: Sequence[str]
    ) -> None:
        """Run the CLI discovery commands and parse what comes back"""
        from app.config.discovery_commands import get_discovery_command

        for capability in wanted:
            spec = get_discovery_command(connector.device_type, capability)
            if not spec:
                continue

            output = connector.get_discovery_output(capability)
            if not output:
                continue

            parsed = parsers.parse(spec["format"], output)
            result.neighbors.extend(parsed.neighbors)
            result.mac_entries.extend(parsed.mac_entries)
            result.arp_entries.extend(parsed.arp_entries)

    @staticmethod
    def _probe_snmp(connector: DeviceConnector, result: ProbeResult) -> None:
        """
        Collect neighbours and inventory over SNMP

        LLDP-MIB indexes remote entries by (timeMark, localPortNum, index), so
        the local port is recovered from the OID rather than from a column.
        """
        client = connector.connection
        if client is None:
            return

        interface_names = {}
        try:
            interface_names = client.interface_names()
        except Exception:  # noqa: BLE001 - optional enrichment
            pass

        from app.services.snmp_client import OID

        # --- LLDP -----------------------------------------------------
        sys_names = dict(client.walk(OID["lldpRemSysName"]))
        port_ids = dict(client.walk(OID["lldpRemPortId"]))
        chassis_ids = dict(client.walk(OID["lldpRemChassisId"]))

        for oid, remote_name in sys_names.items():
            suffix = oid[len(OID["lldpRemSysName"]) :].strip(".")
            parts = suffix.split(".")
            local_port = parts[1] if len(parts) >= 2 else ""

            remote_port = None
            chassis = None
            for candidate_oid, value in port_ids.items():
                if candidate_oid.endswith(suffix):
                    remote_port = value
                    break
            for candidate_oid, value in chassis_ids.items():
                if candidate_oid.endswith(suffix):
                    chassis = parsers.normalize_mac(value) or value
                    break

            result.neighbors.append(
                parsers.Neighbor(
                    local_interface=interface_names.get(local_port, local_port),
                    remote_hostname=remote_name,
                    protocol="lldp",
                    remote_interface=remote_port,
                    remote_chassis_id=chassis,
                )
            )

        # --- CDP ------------------------------------------------------
        for oid, device_id in client.walk(OID["cdpCacheDeviceId"]):
            suffix = oid[len(OID["cdpCacheDeviceId"]) :].strip(".")
            index = suffix.split(".")[0] if suffix else ""

            result.neighbors.append(
                parsers.Neighbor(
                    local_interface=interface_names.get(index, index),
                    remote_hostname=device_id,
                    protocol="cdp",
                )
            )

        # --- ARP ------------------------------------------------------
        for oid, value in client.walk(OID["ipNetToMediaPhysAddress"]):
            mac = parsers.normalize_mac(value)
            # The address is the last four labels of the OID.
            parts = oid.split(".")
            if mac and len(parts) >= 4:
                ip_address = ".".join(parts[-4:])
                try:
                    ipaddress.IPv4Address(ip_address)
                except ValueError:
                    continue
                result.arp_entries.append(
                    parsers.ArpEntry(ip_address=ip_address, mac=mac)
                )

    # ------------------------------------------------------------------
    # Persistence (single threaded)
    # ------------------------------------------------------------------

    def _hostname_of(self, device_id: Optional[int]) -> Optional[str]:
        """
        The hostname of a device, for denormalising onto a discovery row

        One small query rather than making every caller pass it, and it is
        only reached when a caller did not.
        """
        if not device_id:
            return None
        return self.db.scalar(select(Device.hostname).where(Device.id == device_id))

    def save_neighbors(
        self,
        organization_id: int,
        device_id: int,
        neighbors: Sequence[parsers.Neighbor],
        seen_at: Optional[datetime] = None,
        device_hostname: Optional[str] = None,
    ) -> int:
        """
        Upsert adjacencies for one device

        Args:
            organization_id: Tenant scope
            device_id: The device that reported them
            neighbors: Parsed adjacencies
            seen_at: Timestamp for this observation
            device_hostname: The reporting device's name, stored on the row so
                the adjacency still reads sensibly if that device is later
                deleted

        Returns:
            Number of rows written
        """
        if not neighbors:
            return 0

        seen_at = seen_at or _now()
        device_hostname = device_hostname or self._hostname_of(device_id)

        rows = []
        keys: Set[Tuple] = set()

        for neighbor in neighbors:
            if not neighbor.remote_hostname:
                continue

            key = (
                neighbor.local_interface or "",
                neighbor.remote_hostname,
                neighbor.remote_interface or "",
            )
            # A device that runs both LLDP and CDP reports the same link
            # twice; keep the first.
            if key in keys:
                continue
            keys.add(key)

            rows.append(
                {
                    "organization_id": organization_id,
                    "device_id": device_id,
                    "device_hostname": device_hostname,
                    "local_interface": key[0],
                    "remote_hostname": key[1],
                    "remote_interface": key[2],
                    "remote_platform": neighbor.remote_platform,
                    "remote_mgmt_ip": neighbor.remote_mgmt_ip,
                    "remote_chassis_id": neighbor.remote_chassis_id,
                    "capabilities": neighbor.capabilities,
                    "protocol": neighbor.protocol,
                    "first_seen": seen_at,
                    "last_seen": seen_at,
                    "is_active": True,
                }
            )

        if not rows:
            return 0

        statement = pg_insert(Neighbor).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_neighbor_link",
            set_={
                "last_seen": statement.excluded.last_seen,
                "is_active": True,
                "device_hostname": statement.excluded.device_hostname,
                "remote_platform": statement.excluded.remote_platform,
                "remote_mgmt_ip": statement.excluded.remote_mgmt_ip,
                "remote_chassis_id": statement.excluded.remote_chassis_id,
                "capabilities": statement.excluded.capabilities,
                "protocol": statement.excluded.protocol,
            },
        )
        self.db.execute(statement)

        return len(rows)

    def save_inventory(
        self,
        organization_id: int,
        device_id: int,
        mac_entries: Sequence[parsers.MacEntry],
        arp_entries: Sequence[parsers.ArpEntry] = (),
        seen_at: Optional[datetime] = None,
        neighbors: Sequence[parsers.Neighbor] = (),
        device_hostname: Optional[str] = None,
    ) -> int:
        """
        Upsert host inventory for one device

        Args:
            organization_id: Tenant scope
            device_id: The switch the hosts were seen on
            mac_entries: MAC address table rows
            arp_entries: ARP rows, used to attach IP addresses
            seen_at: Timestamp for this observation
            neighbors: Adjacencies from the same sweep. A MAC on a port where
                LLDP or CDP saw a neighbour is that neighbour, which is how a
                switch, an AP or a phone gets a name instead of just a vendor.
            device_hostname: The switch's name, stored on the row so it still
                reads sensibly if the switch is later deleted

        Returns:
            Number of rows written
        """
        if not mac_entries:
            return 0

        seen_at = seen_at or _now()
        device_hostname = device_hostname or self._hostname_of(device_id)

        # What LLDP/CDP announced on each port, so a MAC seen there can be
        # named. A port with several neighbours is an uplink or a hub, and
        # naming a host after one of many would be a guess, so those are
        # skipped.
        by_port: Dict[str, List[parsers.Neighbor]] = {}
        for neighbor in neighbors or ():
            port = parsers.canonical_interface(neighbor.local_interface or "")
            if port:
                by_port.setdefault(port, []).append(neighbor)

        announced = {
            port: found[0]
            for port, found in by_port.items()
            if len(found) == 1 and found[0].remote_hostname
        }

        # Resolve vendors from the in-process OUI cache rather than a query
        # per host.
        ensure_populated(self.db)

        ip_by_mac = {entry.mac: entry.ip_address for entry in arp_entries}

        rows = []
        keys: Set[Tuple] = set()

        for entry in mac_entries:
            if not entry.mac or not entry.interface:
                continue

            key = (entry.interface, entry.mac, entry.vlan or 0)
            if key in keys:
                continue
            keys.add(key)

            neighbor = announced.get(parsers.canonical_interface(entry.interface))

            rows.append(
                {
                    "organization_id": organization_id,
                    "device_id": device_id,
                    "device_hostname": device_hostname,
                    "interface": entry.interface,
                    "mac_address": entry.mac,
                    "vlan": entry.vlan or 0,
                    "entry_type": entry.entry_type,
                    "ip_address": ip_by_mac.get(entry.mac),
                    "vendor": oui_lookup.lookup(entry.mac, self.db),
                    "discovered_hostname": neighbor.remote_hostname if neighbor else None,
                    "discovered_via": neighbor.protocol if neighbor else None,
                    "discovered_platform": neighbor.remote_platform if neighbor else None,
                    "first_seen": seen_at,
                    "last_seen": seen_at,
                    "is_active": True,
                }
            )

        if not rows:
            return 0

        statement = pg_insert(HostInventory).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_host_on_port",
            set_={
                "last_seen": statement.excluded.last_seen,
                "is_active": True,
                "entry_type": statement.excluded.entry_type,
                # Keep a known address rather than overwriting it with NULL
                # when this particular sweep had no ARP data.
                "ip_address": func_coalesce(
                    statement.excluded.ip_address, HostInventory.ip_address
                ),
                "vendor": statement.excluded.vendor,
                "device_hostname": statement.excluded.device_hostname,
                # Same reasoning as ip_address: a sweep that happened not to
                # read LLDP must not erase a name an earlier one learned.
                "discovered_hostname": func_coalesce(
                    statement.excluded.discovered_hostname,
                    HostInventory.discovered_hostname,
                ),
                "discovered_via": func_coalesce(
                    statement.excluded.discovered_via, HostInventory.discovered_via
                ),
                "discovered_platform": func_coalesce(
                    statement.excluded.discovered_platform,
                    HostInventory.discovered_platform,
                ),
            },
        )
        self.db.execute(statement)

        return len(rows)

    def match_neighbors_to_devices(self, organization_id: int) -> int:
        """
        Link adjacencies to managed devices

        A neighbour is matched by management IP first, then by hostname, so
        two switches that report each other become one edge between two known
        nodes instead of four unconnected ones.

        Args:
            organization_id: Tenant scope

        Returns:
            Number of adjacencies newly linked
        """
        devices = self.db.execute(
            select(Device.id, Device.hostname, Device.ip_address).where(
                Device.organization_id == organization_id
            )
        ).all()

        by_ip = {row.ip_address: row.id for row in devices if row.ip_address}
        by_hostname = {row.hostname.lower(): row.id for row in devices if row.hostname}

        unmatched = self.db.execute(
            select(Neighbor.id, Neighbor.remote_hostname, Neighbor.remote_mgmt_ip).where(
                Neighbor.organization_id == organization_id,
                Neighbor.remote_device_id.is_(None),
            )
        ).all()

        matched = 0
        for row in unmatched:
            device_id = None
            if row.remote_mgmt_ip:
                device_id = by_ip.get(row.remote_mgmt_ip)
            if device_id is None and row.remote_hostname:
                device_id = by_hostname.get(row.remote_hostname.lower())

            if device_id is not None:
                self.db.execute(
                    sql_update(Neighbor)
                    .where(Neighbor.id == row.id)
                    .values(remote_device_id=device_id)
                    .execution_options(synchronize_session=False)
                )
                matched += 1

        return matched

    def mark_stale(
        self, organization_id: int, before: datetime, device_ids: Sequence[int] = ()
    ) -> Tuple[int, int]:
        """
        Mark adjacencies and hosts not seen in this sweep as inactive

        Rows are never deleted: "this host disappeared last Tuesday" is the
        useful answer, and it needs the row to still exist.

        Args:
            organization_id: Tenant scope
            before: Anything last seen before this is stale
            device_ids: Restrict to these devices (those actually probed)

        Returns:
            (neighbours marked stale, hosts marked stale)
        """
        neighbor_filter = [
            Neighbor.organization_id == organization_id,
            Neighbor.last_seen < before,
            Neighbor.is_active.is_(True),
        ]
        host_filter = [
            HostInventory.organization_id == organization_id,
            HostInventory.last_seen < before,
            HostInventory.is_active.is_(True),
        ]

        if device_ids:
            neighbor_filter.append(Neighbor.device_id.in_(list(device_ids)))
            host_filter.append(HostInventory.device_id.in_(list(device_ids)))

        neighbors = self.db.execute(
            sql_update(Neighbor)
            .where(and_(*neighbor_filter))
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        ).rowcount

        hosts = self.db.execute(
            sql_update(HostInventory)
            .where(and_(*host_filter))
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        ).rowcount

        return neighbors or 0, hosts or 0

    # ------------------------------------------------------------------
    # Crawling
    # ------------------------------------------------------------------

    def crawl(
        self,
        organization_id: int,
        seed_device_id: int,
        max_hops: int = 2,
        auto_add: bool = False,
        collect_inventory: bool = True,
        user_id: Optional[int] = None,
        max_workers: Optional[int] = None,
    ) -> CrawlSummary:
        """
        Discover the network outward from one seed device

        Each hop probes every device found at the previous hop in parallel,
        then resolves which of the neighbours they reported are devices we
        already manage (or, with auto_add, registers them) before going
        another hop out.

        Args:
            organization_id: Tenant scope
            seed_device_id: The device to start from
            max_hops: How far to walk (0 = probe only the seed)
            auto_add: Register neighbours that are not managed yet
            collect_inventory: Also collect MAC tables and ARP
            user_id: Who asked
            max_workers: Concurrent probes

        Returns:
            CrawlSummary
        """
        started_at = _now()
        summary = CrawlSummary()

        seed = self.db.get(Device, seed_device_id)
        if not seed or seed.organization_id != organization_id:
            raise ValueError(f"Device {seed_device_id} not found in this organization")

        run = DiscoveryRun(
            organization_id=organization_id,
            seed_device_id=seed_device_id,
            status="running",
            max_hops=max_hops,
            triggered_by=user_id,
        )
        self.db.add(run)
        self.db.commit()
        summary.run_id = run.id

        workers = max_workers or settings.MAX_CONCURRENT_BACKUPS
        capabilities = None if collect_inventory else ("lldp", "cdp")

        visited: Set[int] = set()
        frontier: List[int] = [seed_device_id]

        try:
            for hop in range(max_hops + 1):
                frontier = [
                    device_id for device_id in frontier if device_id not in visited
                ]
                if not frontier:
                    break

                logger.info(
                    f"Discovery run {run.id}: hop {hop}, probing {len(frontier)} device(s)"
                )

                results = self._probe_many(frontier, capabilities, workers)
                visited.update(frontier)

                for result in results:
                    self._persist_probe(organization_id, result, summary)

                self.db.commit()

                matched = self.match_neighbors_to_devices(organization_id)
                if matched:
                    self.db.commit()

                if hop >= max_hops:
                    break

                frontier = self._next_hop(
                    organization_id, results, visited, auto_add, seed, summary
                )
                self.db.commit()

            run.status = "success"
        except Exception as e:  # noqa: BLE001 - record the failure, do not lose the run
            logger.exception(f"Discovery run {run.id} failed")
            self.db.rollback()
            run = self.db.get(DiscoveryRun, summary.run_id)
            run.status = "failed"
            run.error_message = str(e)
            summary.errors.append({"device": "run", "error": str(e)})

        run.devices_probed = summary.devices_probed
        run.neighbors_found = summary.neighbors_found
        run.hosts_found = summary.hosts_found
        run.devices_created = summary.devices_created
        run.finished_at = _now()
        run.duration = int((run.finished_at - started_at).total_seconds())
        run.details = {
            "errors": summary.errors[:50],
            "unmanaged": summary.unmanaged[:100],
            "devices_failed": summary.devices_failed,
        }
        self.db.commit()

        logger.info(
            f"Discovery run {run.id} {run.status}: probed {summary.devices_probed}, "
            f"{summary.neighbors_found} adjacencies, {summary.hosts_found} hosts, "
            f"{summary.devices_created} devices added"
        )

        return summary

    def _probe_many(
        self,
        device_ids: Sequence[int],
        capabilities: Optional[Sequence[str]],
        workers: int,
    ) -> List[ProbeResult]:
        """Probe several devices in parallel, off the session"""
        devices = self.db.execute(
            select(Device).where(Device.id.in_(list(device_ids)))
        ).scalars().all()

        jobs = []
        for device in devices:
            if not supports_discovery(device.device_type):
                continue
            jobs.append(
                (
                    DeviceSnapshot.from_device(device),
                    device.device_type,
                    device.transport or "ssh",
                    _snapshot_snmp(device) if device.transport == "snmp" else None,
                )
            )

        if not jobs:
            return []

        results: List[ProbeResult] = []
        workers = max(1, min(workers, len(jobs)))

        if workers == 1:
            return [self.probe(*job, capabilities=capabilities) for job in jobs]

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="discovery"
        ) as pool:
            futures = [
                pool.submit(self.probe, *job, capabilities=capabilities) for job in jobs
            ]
            for future in as_completed(futures):
                results.append(future.result())

        return results

    def _persist_probe(
        self, organization_id: int, result: ProbeResult, summary: CrawlSummary
    ) -> None:
        """Write one probe's findings"""
        device_id = result.snapshot.id
        seen_at = _now()

        if result.error:
            summary.devices_failed += 1
            summary.errors.append(
                {"device": result.snapshot.hostname, "error": result.error}
            )
            return

        summary.devices_probed += 1

        summary.neighbors_found += self.save_neighbors(
            organization_id,
            device_id,
            result.neighbors,
            seen_at,
            device_hostname=result.snapshot.hostname,
        )
        summary.hosts_found += self.save_inventory(
            organization_id,
            device_id,
            result.mac_entries,
            result.arp_entries,
            seen_at,
            # From the same sweep, so a MAC on a port where LLDP or CDP saw a
            # neighbour gets that neighbour's name.
            neighbors=result.neighbors,
            device_hostname=result.snapshot.hostname,
        )

        self.db.execute(
            sql_update(Device)
            .where(Device.id == device_id)
            .values(last_discovered_at=seen_at)
            .execution_options(synchronize_session=False)
        )

    def _next_hop(
        self,
        organization_id: int,
        results: Sequence[ProbeResult],
        visited: Set[int],
        auto_add: bool,
        seed: Device,
        summary: CrawlSummary,
    ) -> List[int]:
        """
        Work out which devices to probe at the next hop

        Neighbours that already match a managed device are queued. Ones that
        do not are reported as unmanaged, and registered first when auto_add
        is on - inheriting the seed's credentials and transport, since a
        discovered device has none of its own.
        """
        candidates: List[int] = []

        hostnames = {
            neighbor.remote_hostname
            for result in results
            for neighbor in result.neighbors
            if neighbor.remote_hostname
        }
        if not hostnames:
            return []

        known = self.db.execute(
            select(Device.id, Device.hostname, Device.ip_address).where(
                Device.organization_id == organization_id
            )
        ).all()
        by_hostname = {row.hostname.lower(): row.id for row in known if row.hostname}
        by_ip = {row.ip_address: row.id for row in known if row.ip_address}

        for result in results:
            for neighbor in result.neighbors:
                if not neighbor.remote_hostname:
                    continue

                device_id = by_hostname.get(neighbor.remote_hostname.lower())
                if device_id is None and neighbor.remote_mgmt_ip:
                    device_id = by_ip.get(neighbor.remote_mgmt_ip)

                if device_id is not None:
                    if device_id not in visited:
                        candidates.append(device_id)
                    continue

                entry = {
                    "hostname": neighbor.remote_hostname,
                    "ip_address": neighbor.remote_mgmt_ip or "",
                    "platform": (neighbor.remote_platform or "")[:120],
                    "seen_from": result.snapshot.hostname,
                }
                if entry not in summary.unmanaged:
                    summary.unmanaged.append(entry)

                if auto_add and neighbor.remote_mgmt_ip:
                    created = self._register_discovered(
                        organization_id, neighbor, seed
                    )
                    if created is not None:
                        summary.devices_created += 1
                        by_hostname[neighbor.remote_hostname.lower()] = created
                        by_ip[neighbor.remote_mgmt_ip] = created
                        candidates.append(created)

        return list(dict.fromkeys(candidates))

    def _register_discovered(
        self, organization_id: int, neighbor: parsers.Neighbor, seed: Device
    ) -> Optional[int]:
        """
        Register a neighbour as a managed device

        The device is probed before it is registered: which transport answers,
        which vault credential authenticates, and what the device actually is.
        A device that authenticates is marked eligible for backup; one that
        does not is still registered - it belongs in the inventory and can be
        crawled - but stays inactive with the reason recorded.

        This used to inherit the seed's credentials, transport and device type
        outright, which is why a crawl from one Cisco switch registered every
        neighbour as cisco_ios over SSH and put them all on the schedule.

        Returns:
            The new device id, or None when it could not be registered
        """
        try:
            ipaddress.ip_address(neighbor.remote_mgmt_ip)
        except (ValueError, TypeError):
            return None

        existing = self.db.execute(
            select(Device.id).where(
                Device.organization_id == organization_id,
                or_(
                    Device.ip_address == neighbor.remote_mgmt_ip,
                    Device.hostname == neighbor.remote_hostname,
                ),
            )
        ).scalar()
        if existing:
            return None

        assessment = self._assess_candidate(
            organization_id, neighbor.remote_mgmt_ip, neighbor.remote_platform, seed
        )

        # Only fall back to the seed's type when nothing identified the device,
        # and say so in the description rather than leaving a silent guess.
        device_type = assessment.device_type
        guessed = device_type is None
        if guessed:
            device_type = guess_device_type(
                neighbor.remote_platform, seed.device_type
            )

        facts = assessment.facts or {}
        credentials_from = "the credential vault"

        # A device that authenticated keeps the credential that worked. One
        # that did not still needs something stored, or it cannot be retried
        # from the Devices page, so it inherits the seed's - clearly marked as
        # unverified by last_auth_status.
        username = seed.username
        encrypted_password = seed.encrypted_password
        enable_secret = seed.enable_secret

        if assessment.credential_id is not None:
            working = self.db.get(CredentialModel, assessment.credential_id)
            if working:
                username = working.username or seed.username
                encrypted_password = (
                    working.encrypted_password or seed.encrypted_password
                )
                enable_secret = (
                    working.encrypted_enable_secret or seed.enable_secret
                )
                credentials_from = f"credential '{working.name}'"

        transport = assessment.transport or seed.transport or "ssh"
        port = seed.port
        if transport == "telnet":
            port = 23
        elif transport == "ssh":
            port = 22

        device = Device(
            organization_id=organization_id,
            hostname=neighbor.remote_hostname[:255],
            ip_address=neighbor.remote_mgmt_ip,
            device_type=device_type,
            port=port,
            username=username,
            encrypted_password=encrypted_password,
            enable_secret=enable_secret,
            transport=transport,
            snmp_version=seed.snmp_version,
            snmp_community=seed.snmp_community,
            snmp_port=seed.snmp_port,
            description=(
                f"Discovered from {seed.hostname}"
                + ("" if not guessed else f"; type not identified, assumed {device_type}")
            ),
            # The whole point: eligible for backup only if a login worked.
            is_active=assessment.backup_eligible,
            last_auth_status=assessment.auth_status,
            last_auth_at=_now(),
            auth_error=assessment.auth_error,
            credential_id=assessment.credential_id,
            model=facts.get("model"),
            serial_number=facts.get("serial_number"),
            os_version=facts.get("os_version"),
            snmp_sysname=facts.get("snmp_sysname"),
            snmp_sysdescr=facts.get("snmp_sysdescr"),
            snmp_location=facts.get("snmp_location"),
            snmp_contact=facts.get("snmp_contact"),
            snmp_last_polled_at=_now() if facts.get("snmp_sysdescr") else None,
            discovered_facts=facts or None,
            discovered=True,
            discovery_source=seed.hostname,
            last_discovered_at=_now(),
            created_by=seed.created_by,
        )

        self.db.add(device)
        try:
            self.db.flush()
        except Exception as e:  # noqa: BLE001 - a race or a constraint we did not foresee
            logger.warning(
                f"Could not register discovered device {neighbor.remote_hostname}: {e}"
            )
            self.db.rollback()
            return None

        self._record_probes(organization_id, device.id, assessment)

        logger.info(
            f"Registered discovered device {device.hostname} "
            f"({device.ip_address}) as {device_type} over {transport}; "
            f"auth {assessment.auth_status} via {credentials_from}; "
            f"backup {'enabled' if assessment.backup_eligible else 'disabled'}"
        )
        return device.id

    # ------------------------------------------------------------------
    # Probing
    # ------------------------------------------------------------------

    def _credential_attempts(self, organization_id: int, seed: Device):
        """
        The credentials to try against a discovered device, CLI and SNMP

        The vault comes first. The seed's own credentials are appended as a
        last resort so a crawl still works before anyone has filled the vault
        in - that was the only behaviour before it existed.

        Args:
            organization_id: Tenant scope
            seed: The device the crawl came from

        Returns:
            (cli attempts, snmp attempts)
        """
        cli = vault.list_for_kind(self.db, organization_id, vault.CLI)
        snmp = vault.list_for_kind(self.db, organization_id, vault.SNMP)

        if seed.username and seed.encrypted_password:
            cli.append(
                vault.CredentialAttempt(
                    id=None,
                    name=f"inherited from {seed.hostname}",
                    kind=vault.CLI,
                    username=seed.username,
                    password=_safe_decrypt(seed.encrypted_password),
                    enable_secret=_safe_decrypt(seed.enable_secret),
                    ssh_key_path=seed.ssh_key_path,
                )
            )

        if seed.snmp_version and seed.snmp_community:
            snmp.append(
                vault.CredentialAttempt(
                    id=None,
                    name=f"inherited from {seed.hostname}",
                    kind=vault.SNMP,
                    snmp_version=seed.snmp_version,
                    community=_safe_decrypt(seed.snmp_community),
                )
            )

        return cli, snmp

    def _assess_candidate(
        self,
        organization_id: int,
        ip_address: str,
        platform_hint: Optional[str],
        seed: Device,
    ):
        """
        Probe a candidate device before registering it

        Never raises: a probe that blows up must not abort the crawl, so a
        failure is reported as an unreachable assessment.

        Args:
            organization_id: Tenant scope
            ip_address: Address to probe
            platform_hint: What the neighbour advertised itself as
            seed: The device the crawl came from

        Returns:
            DeviceAssessment
        """
        cli, snmp = self._credential_attempts(organization_id, seed)

        try:
            return probe.assess(
                ip_address,
                cli_attempts=cli,
                snmp_attempts=snmp,
                platform_hint=platform_hint,
                snmp_port=seed.snmp_port or 161,
            )
        except Exception as e:  # noqa: BLE001 - a crawl must survive one bad host
            logger.warning(f"Probe of {ip_address} failed: {e}")
            assessment = probe.DeviceAssessment()
            assessment.auth_status = probe.ERROR
            assessment.auth_error = str(e)[:1000]
            return assessment

    def _record_probes(
        self, organization_id: int, device_id: int, assessment
    ) -> None:
        """
        Store the latest outcome per transport for a device

        Upserted on (device, transport) so a device carries the current state
        of each rather than an ever-growing log: "SSH refused, telnet timed
        out, 4 credentials tried" is what the Devices page needs to explain an
        ineligible device.
        """
        if not assessment.probes:
            return

        rows = [
            {
                "organization_id": organization_id,
                "device_id": device_id,
                "transport": outcome.transport,
                "result": outcome.result,
                "credential_id": outcome.credential_id,
                "credential_name": outcome.credential_name,
                "attempts": outcome.attempts,
                "message": (outcome.message or "")[:2000],
                "duration": outcome.duration_ms,
                "probed_at": _now(),
            }
            for outcome in assessment.probes
        ]

        statement = pg_insert(DeviceProbe).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[DeviceProbe.device_id, DeviceProbe.transport],
            set_={
                "result": statement.excluded.result,
                "credential_id": statement.excluded.credential_id,
                "credential_name": statement.excluded.credential_name,
                "attempts": statement.excluded.attempts,
                "message": statement.excluded.message,
                "duration": statement.excluded.duration,
                "probed_at": statement.excluded.probed_at,
            },
        )
        self.db.execute(statement)

        for outcome in assessment.probes:
            if outcome.credential_id is not None:
                vault.record_outcome(
                    self.db, outcome.credential_id, outcome.ok, commit=False
                )


# Platform strings LLDP and CDP report, mapped to our device types. Matched
# case-insensitively against the platform or system description.
_PLATFORM_HINTS = (
    ("nx-os", "cisco_nxos"),
    ("nexus", "cisco_nxos"),
    ("ios-xe", "cisco_ios_xe"),
    ("ios xe", "cisco_ios_xe"),
    ("arista", "arista_eos"),
    ("eos", "arista_eos"),
    ("fortigate", "fortinet"),
    ("fortios", "fortinet"),
    ("junos", "juniper_junos"),
    ("juniper", "juniper_junos"),
    ("aruba", "aruba_os"),
    ("procurve", "hp_procurve"),
    ("comware", "hp_comware"),
    ("h3c", "hp_comware"),
    ("hewlett", "hp_procurve"),
    ("cisco", "cisco_ios"),
)


def guess_device_type(platform: Optional[str], fallback: str) -> str:
    """
    Guess a device type from the platform string a neighbour advertised

    Args:
        platform: Platform or system description from LLDP/CDP
        fallback: Type to use when nothing matches (the seed's own type)

    Returns:
        A device type identifier
    """
    if not platform:
        return fallback

    lowered = platform.lower()
    for hint, device_type in _PLATFORM_HINTS:
        if hint in lowered:
            return device_type

    return fallback


def func_coalesce(*args):
    """COALESCE helper kept local so the upsert reads cleanly"""
    from sqlalchemy import func

    return func.coalesce(*args)
