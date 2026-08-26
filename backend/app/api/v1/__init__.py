"""
API v1 router aggregation
"""
from fastapi import APIRouter
from app.api.v1 import auth, devices, backups, backup_jobs, compare, statistics

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
