"""
Discovery, neighbour and topology diagram endpoints
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_organization_id, require_permission
from app.core.database import get_db
from app.models.device import Device
from app.models.network import DiscoveryRun, Neighbor, TopologyDiagram
from app.models.user import User
from app.repositories.audit_log import AuditLogRepository
from app.services.discovery import DiscoveryService
from app.services.topology import build_graph, merge_layout

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class DiscoveryRequest(BaseModel):
    """Start a discovery crawl from one device"""

    seed_device_id: int = Field(..., description="Device to start from")
    max_hops: int = Field(2, ge=0, le=10, description="How far to walk")
    auto_add: bool = Field(
        False, description="Register neighbours that are not managed yet"
    )
    collect_inventory: bool = Field(
        True, description="Also collect MAC address tables and ARP"
    )
    run_async: bool = Field(
        True, description="Queue the crawl rather than waiting for it"
    )


class DiscoveryRunResponse(BaseModel):
    """A discovery crawl"""

    id: int
    status: str
    seed_device_id: Optional[int]
    max_hops: int
    devices_probed: int
    neighbors_found: int
    hosts_found: int
    devices_created: int
    started_at: datetime
    finished_at: Optional[datetime]
    duration: Optional[int]
    error_message: Optional[str]
    details: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


class NeighborResponse(BaseModel):
    """One adjacency"""

    id: int
    device_id: int
    device_hostname: Optional[str] = None
    local_interface: str
    remote_hostname: str
    remote_interface: Optional[str]
    remote_platform: Optional[str]
    remote_mgmt_ip: Optional[str]
    remote_device_id: Optional[int]
    protocol: str
    first_seen: datetime
    last_seen: datetime
    is_active: bool

    class Config:
        from_attributes = True


class DiagramCreate(BaseModel):
    """Create a saved diagram"""

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    layout: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class DiagramUpdate(BaseModel):
    """Update a saved diagram"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    layout: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None


class DiagramResponse(BaseModel):
    """A saved diagram"""

    id: int
    name: str
    description: Optional[str]
    layout: Dict[str, Any]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------------------------------
# Discovery runs
# --------------------------------------------------------------------------


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def start_discovery(
    request: DiscoveryRequest,
    current_user: User = Depends(require_permission("discovery:run")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Start a discovery crawl from a seed device

    Queued to the worker by default: a crawl of any size takes far longer than
    an HTTP request should.
    """
    device = db.execute(
        select(Device).where(
            Device.id == request.seed_device_id,
            Device.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seed device not found",
        )

    audit_repo = AuditLogRepository(db)
    audit_repo.log_action(
        user_id=current_user.id,
        action="discovery_started",
        resource_type="device",
        resource_id=device.id,
        details={
            "seed": device.hostname,
            "max_hops": request.max_hops,
            "auto_add": request.auto_add,
        },
    )

    if request.run_async:
        from app.tasks.discovery import discovery_crawl_task

        task = discovery_crawl_task.delay(
            organization_id=organization_id,
            seed_device_id=request.seed_device_id,
            max_hops=request.max_hops,
            auto_add=request.auto_add,
            collect_inventory=request.collect_inventory,
            user_id=current_user.id,
        )
        return {
            "queued": True,
            "task_id": task.id,
            "seed": device.hostname,
            "message": f"Discovery started from {device.hostname}",
        }

    service = DiscoveryService(db)
    summary = service.crawl(
        organization_id=organization_id,
        seed_device_id=request.seed_device_id,
        max_hops=request.max_hops,
        auto_add=request.auto_add,
        collect_inventory=request.collect_inventory,
        user_id=current_user.id,
    )

    return {
        "queued": False,
        "run_id": summary.run_id,
        "devices_probed": summary.devices_probed,
        "devices_failed": summary.devices_failed,
        "neighbors_found": summary.neighbors_found,
        "hosts_found": summary.hosts_found,
        "devices_created": summary.devices_created,
        "unmanaged": summary.unmanaged,
        "errors": summary.errors,
    }


@router.get("/runs", response_model=List[DiscoveryRunResponse])
def list_discovery_runs(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """List recent discovery crawls, newest first"""
    return list(
        db.execute(
            select(DiscoveryRun)
            .where(DiscoveryRun.organization_id == organization_id)
            .order_by(DiscoveryRun.started_at.desc())
            .limit(limit)
        ).scalars()
    )


@router.get("/runs/{run_id}", response_model=DiscoveryRunResponse)
def get_discovery_run(
    run_id: int,
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Get one discovery crawl"""
    run = db.execute(
        select(DiscoveryRun).where(
            DiscoveryRun.id == run_id,
            DiscoveryRun.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Discovery run not found"
        )

    return run


# --------------------------------------------------------------------------
# Neighbours
# --------------------------------------------------------------------------


@router.get("/neighbors", response_model=List[NeighborResponse])
def list_neighbors(
    device_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    protocol: Optional[str] = Query(None, pattern="^(lldp|cdp)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """List discovered adjacencies"""
    statement = (
        select(Neighbor, Device.hostname)
        .join(Device, Neighbor.device_id == Device.id)
        .where(Neighbor.organization_id == organization_id)
    )

    if device_id is not None:
        statement = statement.where(Neighbor.device_id == device_id)
    if active_only:
        statement = statement.where(Neighbor.is_active.is_(True))
    if protocol:
        statement = statement.where(Neighbor.protocol == protocol)

    rows = db.execute(
        statement.order_by(Neighbor.last_seen.desc()).offset(skip).limit(limit)
    ).all()

    responses = []
    for neighbor, hostname in rows:
        payload = NeighborResponse.model_validate(neighbor)
        responses.append(payload.model_copy(update={"device_hostname": hostname}))

    return responses


@router.delete("/neighbors/{neighbor_id}")
def delete_neighbor(
    neighbor_id: int,
    current_user: User = Depends(require_permission("discovery:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Remove an adjacency, for a link that will never come back"""
    neighbor = db.execute(
        select(Neighbor).where(
            Neighbor.id == neighbor_id,
            Neighbor.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not neighbor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Neighbor not found"
        )

    db.delete(neighbor)
    db.commit()

    return {"success": True, "message": "Adjacency removed"}


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------


@router.get("/topology")
def get_topology(
    diagram_id: Optional[int] = Query(
        None, description="Apply a saved diagram's layout"
    ),
    active_only: bool = Query(True),
    include_unmanaged: bool = Query(
        True, description="Include neighbours that are not managed devices"
    ),
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """
    Build the current topology graph

    The graph is always rebuilt from current adjacencies. A saved diagram
    contributes only the user's edits on top of it, so newly discovered
    devices appear without discarding a hand-arranged layout.
    """
    graph = build_graph(
        db,
        organization_id,
        active_only=active_only,
        include_unmanaged=include_unmanaged,
    )

    if diagram_id is not None:
        diagram = db.execute(
            select(TopologyDiagram).where(
                TopologyDiagram.id == diagram_id,
                TopologyDiagram.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not diagram:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Diagram not found"
            )

        graph = merge_layout(graph, diagram.layout or {})
        graph["diagram"] = {"id": diagram.id, "name": diagram.name}

    return graph


@router.get("/diagrams", response_model=List[DiagramResponse])
def list_diagrams(
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """List saved diagrams"""
    return list(
        db.execute(
            select(TopologyDiagram)
            .where(TopologyDiagram.organization_id == organization_id)
            .order_by(TopologyDiagram.is_default.desc(), TopologyDiagram.name)
        ).scalars()
    )


@router.post("/diagrams", response_model=DiagramResponse, status_code=status.HTTP_201_CREATED)
def create_diagram(
    payload: DiagramCreate,
    current_user: User = Depends(require_permission("discovery:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Save a diagram layout"""
    clash = db.execute(
        select(TopologyDiagram.id).where(
            TopologyDiagram.organization_id == organization_id,
            TopologyDiagram.name == payload.name,
        )
    ).scalar()

    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A diagram named '{payload.name}' already exists",
        )

    if payload.is_default:
        _clear_default(db, organization_id)

    diagram = TopologyDiagram(
        organization_id=organization_id,
        name=payload.name,
        description=payload.description,
        layout=payload.layout or {},
        is_default=payload.is_default,
        created_by=current_user.id,
    )
    db.add(diagram)
    db.commit()

    return diagram


@router.get("/diagrams/{diagram_id}", response_model=DiagramResponse)
def get_diagram(
    diagram_id: int,
    current_user: User = Depends(require_permission("discovery:read")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Get one saved diagram"""
    return _get_diagram_or_404(db, diagram_id, organization_id)


@router.put("/diagrams/{diagram_id}", response_model=DiagramResponse)
def update_diagram(
    diagram_id: int,
    payload: DiagramUpdate,
    current_user: User = Depends(require_permission("discovery:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Update a saved diagram"""
    diagram = _get_diagram_or_404(db, diagram_id, organization_id)

    if payload.name is not None and payload.name != diagram.name:
        clash = db.execute(
            select(TopologyDiagram.id).where(
                TopologyDiagram.organization_id == organization_id,
                TopologyDiagram.name == payload.name,
                TopologyDiagram.id != diagram_id,
            )
        ).scalar()
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A diagram named '{payload.name}' already exists",
            )
        diagram.name = payload.name

    if payload.description is not None:
        diagram.description = payload.description

    if payload.layout is not None:
        diagram.layout = payload.layout

    if payload.is_default is not None:
        if payload.is_default:
            _clear_default(db, organization_id)
        diagram.is_default = payload.is_default

    db.commit()
    return diagram


@router.delete("/diagrams/{diagram_id}")
def delete_diagram(
    diagram_id: int,
    current_user: User = Depends(require_permission("discovery:write")),
    organization_id: int = Depends(get_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a saved diagram"""
    diagram = _get_diagram_or_404(db, diagram_id, organization_id)
    name = diagram.name

    db.delete(diagram)
    db.commit()

    return {"success": True, "message": f"Diagram '{name}' deleted"}


def _get_diagram_or_404(
    db: Session, diagram_id: int, organization_id: int
) -> TopologyDiagram:
    """Fetch a diagram within the tenant, or raise 404"""
    diagram = db.execute(
        select(TopologyDiagram).where(
            TopologyDiagram.id == diagram_id,
            TopologyDiagram.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not diagram:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Diagram not found"
        )

    return diagram


def _clear_default(db: Session, organization_id: int) -> None:
    """Only one diagram per organization can be the default"""
    db.execute(
        TopologyDiagram.__table__.update()
        .where(
            TopologyDiagram.organization_id == organization_id,
            TopologyDiagram.is_default.is_(True),
        )
        .values(is_default=False)
    )
