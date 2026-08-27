"""
Build a topology graph from discovered adjacencies

The graph is always derived from the current contents of the neighbors table
and never stored. A saved diagram contributes only the user's edits on top of
it - node positions, hidden nodes, renamed labels, manually drawn links - so
a device discovered after the diagram was saved still appears, and a layout
someone arranged by hand is not thrown away by a refresh.

Nodes are keyed by a stable string rather than a database id, because a node
may be a managed device (device:12) or a neighbour we only know by name
(host:switch-core-01). A key has to survive across rebuilds so saved
positions still attach to the right node.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.network import Neighbor

logger = logging.getLogger(__name__)


# Which tier a node sits in. The default view shows infrastructure only,
# because a switch with 200 MACs on it produces a diagram nobody can read -
# the hosts are inventory, not topology.
#
#   core         a managed device that links to several other managed devices
#   distribution a managed device linking to at least one other
#   access       a managed device with no onward infrastructure links
#   edge         an unmanaged neighbour that advertises router/bridge
#                capability - a switch or firewall we do not manage
#   host         an unmanaged neighbour that does not: a phone, an AP, a PC
TIER_CORE = "core"
TIER_DISTRIBUTION = "distribution"
TIER_ACCESS = "access"
TIER_EDGE = "edge"
TIER_HOST = "host"

INFRASTRUCTURE_TIERS = (TIER_CORE, TIER_DISTRIBUTION, TIER_ACCESS, TIER_EDGE)

# LLDP and CDP capability strings that mean "this is infrastructure". A
# neighbour advertising Router or Bridge forwards traffic; a Phone or a
# Station does not.
_INFRASTRUCTURE_CAPABILITIES = ("router", "bridge", "switch", "l3", "l2", "trans")
_HOST_CAPABILITIES = ("phone", "station", "host", "telephone")

# A managed device linking to at least this many other managed devices is
# treated as core. Three is deliberate: a device between two others is
# carrying transit traffic, but that describes every switch in a chain as
# much as it describes a hub. Three separate neighbours is where the middle
# of the network actually is.
CORE_LINK_THRESHOLD = 3


def device_key(device_id: int) -> str:
    """Stable node key for a managed device"""
    return f"device:{device_id}"


def unmanaged_key(hostname: str) -> str:
    """Stable node key for a neighbour that is not a managed device"""
    return f"host:{(hostname or '').strip().lower()}"


def _link_key(source: str, target: str, source_port: str, target_port: str) -> str:
    """
    Stable key for a link, independent of which end reported it

    Both switches on a link report it, so without normalising the direction
    the same cable becomes two edges.
    """
    ends = sorted([f"{source}|{source_port or ''}", f"{target}|{target_port or ''}"])
    return "::".join(ends)


def classify_unmanaged(capabilities: Optional[str], platform: Optional[str]) -> str:
    """
    Decide whether an unmanaged neighbour is infrastructure or an end host

    LLDP and CDP both advertise capabilities. A neighbour claiming Router or
    Bridge forwards traffic and belongs on the infrastructure diagram; a Phone
    or a Station is an end host and belongs behind a drill-down.

    Args:
        capabilities: The capability string the neighbour advertised
        platform: Its platform or system description

    Returns:
        TIER_EDGE or TIER_HOST
    """
    text = f"{capabilities or ''} {platform or ''}".lower()

    # An explicit host capability wins: an IP phone often advertises Bridge as
    # well, because it has a PC port on the back.
    if any(word in text for word in _HOST_CAPABILITIES):
        return TIER_HOST

    if any(word in text for word in _INFRASTRUCTURE_CAPABILITIES):
        return TIER_EDGE

    # Nothing said either way. An access point or a server announces itself
    # over LLDP without claiming to forward, so treat silence as a host: the
    # cost of being wrong is one drill-down, not an unreadable diagram.
    return TIER_HOST


def assign_tiers(
    nodes: Dict[str, Dict[str, Any]], links: Sequence[Dict[str, Any]]
) -> None:
    """
    Work out which tier each managed device sits in, in place

    Derived from how many *other managed devices* a device links to, not its
    total link count: an access switch with 40 hosts on it is still an access
    switch, and counting those would promote it above the core.

    Args:
        nodes: Nodes by key
        links: The links between them
    """
    infrastructure_degree = {key: 0 for key in nodes}

    for link in links:
        source = nodes.get(link["source"])
        target = nodes.get(link["target"])
        if not source or not target:
            continue

        # Only count a link when both ends are infrastructure.
        if source.get("managed") and target.get("tier") != TIER_HOST:
            infrastructure_degree[link["source"]] += 1
        if target.get("managed") and source.get("tier") != TIER_HOST:
            infrastructure_degree[link["target"]] += 1

    # Structure is a better signal than advertised capability, because plenty
    # of switches leave the capability field empty. Anything cabled to more
    # than one device is forwarding between them, whoever manages it.
    total_degree = {key: node.get("link_count", 0) for key, node in nodes.items()}

    for key, node in nodes.items():
        if not node.get("managed"):
            # Classified from its advertised capabilities, but promoted to
            # infrastructure if it is plainly acting as such: an unmanaged
            # switch with three links is not an end host, whatever it did or
            # did not advertise.
            if total_degree.get(key, 0) >= 2:
                node["tier"] = TIER_EDGE
            else:
                node.setdefault("tier", TIER_HOST)
            continue

        degree = infrastructure_degree.get(key, 0)
        node["infrastructure_links"] = degree

        if degree >= CORE_LINK_THRESHOLD:
            node["tier"] = TIER_CORE
        elif degree >= 1:
            node["tier"] = TIER_DISTRIBUTION
        else:
            node["tier"] = TIER_ACCESS


def build_graph(
    db: Session,
    organization_id: int,
    active_only: bool = True,
    include_unmanaged: bool = True,
    tiers: Optional[Sequence[str]] = None,
    expand: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Build the topology graph for an organization

    Args:
        db: Database session
        organization_id: Tenant scope
        active_only: Only include adjacencies still being seen
        include_unmanaged: Include neighbours that are not managed devices
        tiers: Which tiers to return. Defaults to infrastructure only - a
            switch with 200 MACs behind it makes a diagram nobody can read,
            and those hosts are inventory rather than topology.
        expand: Node keys whose end hosts should be included even when the
            host tier is filtered out. This is the drill-down: click a switch
            and see what is behind it, without unfolding the whole network.

    Returns:
        dict with 'nodes', 'links' and 'stats'
    """
    wanted_tiers = set(tiers) if tiers else set(INFRASTRUCTURE_TIERS)
    expanded = set(expand or ())
    devices = db.execute(
        select(
            Device.id,
            Device.hostname,
            Device.ip_address,
            Device.device_type,
            Device.is_active,
            Device.discovered,
            Device.last_backup_status,
        ).where(Device.organization_id == organization_id)
    ).all()

    nodes: Dict[str, Dict[str, Any]] = {}

    for device in devices:
        nodes[device_key(device.id)] = {
            "key": device_key(device.id),
            "id": device.id,
            "label": device.hostname,
            "type": "device",
            "device_type": device.device_type,
            "ip_address": device.ip_address,
            "managed": True,
            "is_active": device.is_active,
            "discovered": bool(device.discovered),
            "last_backup_status": device.last_backup_status,
            "link_count": 0,
        }

    statement = select(Neighbor).where(Neighbor.organization_id == organization_id)
    if active_only:
        statement = statement.where(Neighbor.is_active.is_(True))

    adjacencies = list(db.execute(statement).scalars())

    links: Dict[str, Dict[str, Any]] = {}

    for adjacency in adjacencies:
        source = device_key(adjacency.device_id)
        if source not in nodes:
            # The reporting device was deleted; its adjacencies are stale.
            continue

        if adjacency.remote_device_id:
            target = device_key(adjacency.remote_device_id)
            if target not in nodes:
                continue
        else:
            if not include_unmanaged:
                continue

            target = unmanaged_key(adjacency.remote_hostname)
            if target not in nodes:
                nodes[target] = {
                    "key": target,
                    "id": None,
                    "label": adjacency.remote_hostname,
                    "type": "unmanaged",
                    "device_type": None,
                    "ip_address": adjacency.remote_mgmt_ip,
                    "platform": adjacency.remote_platform,
                    "capabilities": adjacency.capabilities,
                    "managed": False,
                    "is_active": True,
                    "discovered": True,
                    "link_count": 0,
                    "tier": classify_unmanaged(
                        adjacency.capabilities, adjacency.remote_platform
                    ),
                }

        key = _link_key(
            source, target, adjacency.local_interface, adjacency.remote_interface
        )

        if key in links:
            # The other end reported the same cable; record that we saw both.
            links[key]["confirmed_both_ends"] = True
            continue

        links[key] = {
            "key": key,
            "source": source,
            "target": target,
            "source_interface": adjacency.local_interface,
            "target_interface": adjacency.remote_interface or None,
            "protocol": adjacency.protocol,
            "is_active": adjacency.is_active,
            "last_seen": adjacency.last_seen.isoformat() if adjacency.last_seen else None,
            "confirmed_both_ends": False,
            "manual": False,
        }

    for link in links.values():
        for end in (link["source"], link["target"]):
            if end in nodes:
                nodes[end]["link_count"] += 1

    assign_tiers(nodes, list(links.values()))

    # How many hosts sit behind each infrastructure node, so the UI can offer
    # a drill-down without having fetched them.
    hidden_children: Dict[str, int] = {key: 0 for key in nodes}
    for link in links.values():
        for end, other in (
            (link["source"], link["target"]),
            (link["target"], link["source"]),
        ):
            if end in nodes and nodes.get(other, {}).get("tier") == TIER_HOST:
                hidden_children[end] += 1

    for key, count in hidden_children.items():
        nodes[key]["host_count"] = count

    # Filter to the requested tiers. A node in an unwanted tier still appears
    # when it hangs off a node the caller expanded.
    def keep(node: Dict[str, Any]) -> bool:
        if node.get("tier") in wanted_tiers:
            return True
        return any(
            link["key"]
            for link in links.values()
            if (link["source"] == node["key"] and link["target"] in expanded)
            or (link["target"] == node["key"] and link["source"] in expanded)
        )

    visible = {key: node for key, node in nodes.items() if keep(node)}

    node_list = list(visible.values())
    link_list = [
        link
        for link in links.values()
        if link["source"] in visible and link["target"] in visible
    ]

    total_hosts = sum(1 for node in nodes.values() if node.get("tier") == TIER_HOST)

    return {
        "nodes": node_list,
        "links": link_list,
        "stats": {
            "nodes": len(node_list),
            "managed_nodes": sum(1 for node in node_list if node["managed"]),
            "unmanaged_nodes": sum(1 for node in node_list if not node["managed"]),
            "links": len(link_list),
            "isolated_nodes": sum(
                1 for node in node_list if node["link_count"] == 0
            ),
            # What the filter is holding back, so the UI can say "and 214
            # hosts" rather than silently omitting them.
            "hidden_hosts": total_hosts - sum(
                1 for node in node_list if node.get("tier") == TIER_HOST
            ),
            "total_hosts": total_hosts,
            "tiers": sorted(wanted_tiers),
            "by_tier": {
                tier: sum(1 for node in nodes.values() if node.get("tier") == tier)
                for tier in (TIER_CORE, TIER_DISTRIBUTION, TIER_ACCESS, TIER_EDGE, TIER_HOST)
            },
        },
    }


def merge_layout(graph: Dict[str, Any], layout: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply a saved diagram's edits to a freshly built graph

    Edits are applied to whatever the graph currently contains: a saved
    position for a node that no longer exists is simply ignored, and a node
    that appeared since the diagram was saved keeps its default placement
    rather than being dropped.

    Args:
        graph: Graph from build_graph
        layout: Saved layout

    Returns:
        The graph, with positions, labels, hidden flags and manual links
        applied
    """
    node_edits = (layout or {}).get("nodes") or {}
    manual_links = (layout or {}).get("links") or []
    hidden_links = set((layout or {}).get("hidden_links") or [])

    known_keys = set()

    for node in graph["nodes"]:
        known_keys.add(node["key"])
        edit = node_edits.get(node["key"])
        if not edit:
            continue

        if "x" in edit:
            node["x"] = edit["x"]
        if "y" in edit:
            node["y"] = edit["y"]
        if edit.get("label"):
            node["label"] = edit["label"]
        if "hidden" in edit:
            node["hidden"] = bool(edit["hidden"])
        if edit.get("icon"):
            node["icon"] = edit["icon"]
        if edit.get("group"):
            node["group"] = edit["group"]
        if edit.get("notes"):
            node["notes"] = edit["notes"]

    # Links the user drew by hand, for connections LLDP and CDP cannot see -
    # an unmanaged media converter, a dark fibre, a link through a provider.
    existing = {link["key"] for link in graph["links"]}

    for manual in manual_links:
        source = manual.get("source")
        target = manual.get("target")
        if not source or not target:
            continue
        if source not in known_keys or target not in known_keys:
            # An edit referring to a node that no longer exists.
            continue

        key = _link_key(
            source, target, manual.get("source_interface", ""),
            manual.get("target_interface", ""),
        )
        if key in existing:
            continue

        graph["links"].append(
            {
                "key": key,
                "source": source,
                "target": target,
                "source_interface": manual.get("source_interface"),
                "target_interface": manual.get("target_interface"),
                "protocol": "manual",
                "is_active": True,
                "last_seen": None,
                "confirmed_both_ends": False,
                "manual": True,
                "label": manual.get("label"),
            }
        )
        existing.add(key)

    if hidden_links:
        for link in graph["links"]:
            if link["key"] in hidden_links:
                link["hidden"] = True

    graph["stats"]["links"] = len(graph["links"])
    graph["stats"]["manual_links"] = sum(
        1 for link in graph["links"] if link.get("manual")
    )

    return graph


def extract_layout(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reduce a graph back to just the edits worth saving

    Used when the frontend sends a whole graph back: only positions, labels,
    hidden flags and manual links are kept, because everything else is
    rebuilt from the adjacencies next time.

    Args:
        graph: A graph as the client has it

    Returns:
        A layout suitable for storing on a diagram
    """
    nodes = {}

    for node in graph.get("nodes", []):
        key = node.get("key")
        if not key:
            continue

        edit = {}
        for field in ("x", "y", "hidden", "icon", "group", "notes"):
            if field in node and node[field] is not None:
                edit[field] = node[field]

        # Only keep a label when it differs from the discovered one.
        if node.get("label") and node.get("label") != node.get("discovered_label"):
            edit["label"] = node["label"]

        if edit:
            nodes[key] = edit

    manual = [
        {
            "source": link["source"],
            "target": link["target"],
            "source_interface": link.get("source_interface"),
            "target_interface": link.get("target_interface"),
            "label": link.get("label"),
        }
        for link in graph.get("links", [])
        if link.get("manual")
    ]

    hidden_links = [
        link["key"] for link in graph.get("links", []) if link.get("hidden")
    ]

    return {
        "nodes": nodes,
        "links": manual,
        "hidden_links": hidden_links,
        "viewport": graph.get("viewport") or {},
    }
