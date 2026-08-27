"""
API v1 router aggregation
"""
from fastapi import APIRouter
from app.api.v1 import (
    auth,
    devices,
    backups,
    backup_jobs,
    compare,
    statistics,
    discovery,
    inventory,
    users,
    settings,
)

api_router = APIRouter()

# Include authentication routes
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])

# Include device routes
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])

# Include backup routes
api_router.include_router(backups.router, prefix="/backups", tags=["backups"])

# Include backup job routes
api_router.include_router(backup_jobs.router, prefix="/backup-jobs", tags=["backup-jobs"])

# Include comparison routes
api_router.include_router(compare.router, prefix="/compare", tags=["comparison"])

# Include statistics routes
api_router.include_router(statistics.router, prefix="/statistics", tags=["statistics"])

# Include discovery, neighbour and topology routes
api_router.include_router(discovery.router, prefix="/discovery", tags=["discovery"])

# Include host inventory, OUI and reporting routes
api_router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])

# Include user and role administration routes
api_router.include_router(users.router, prefix="/users", tags=["users"])

# Include application settings and remote backup target routes
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
