"""
Main FastAPI application
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.api.v1 import api_router
import logging

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown"""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Debug mode: {settings.DEBUG}")

    host = str(settings.DATABASE_URL)
    logger.info(f"Database: {host.split('@')[-1] if '@' in host else 'configured'}")

    yield

    logger.info("Shutting down application")
    engine.dispose()


# Create FastAPI application.
#
# ORJSONResponse serialises several times faster than the stdlib json encoder,
# which matters most on the diff and list endpoints that return large payloads.
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Multi-tenant network device configuration backup system",
    docs_url="/docs",
    redoc_url="/redoc",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# Compress responses above 1 KiB. Configuration diffs and paginated lists are
# highly repetitive text, so this cuts transfer size by roughly an order of
# magnitude for a small, bounded amount of CPU.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 router
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    """Liveness check - answers without touching any dependency"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
    }


@app.get("/api/v1/health")
def api_health_check():
    """Readiness check - verifies the database and broker are reachable"""
    database = "connected"
    redis_status = "connected"

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as e:
        database = f"error: {type(e).__name__}"
        logger.warning(f"Health check: database unreachable: {e}")

    try:
        # Imported lazily so a broker problem cannot break process startup.
        from app.celery_app import celery_app

        with celery_app.connection_or_acquire() as connection:
            connection.ensure_connection(max_retries=0, timeout=2)
    except Exception as e:
        redis_status = f"error: {type(e).__name__}"
        logger.warning(f"Health check: broker unreachable: {e}")

    healthy = database == "connected" and redis_status == "connected"

    return {
        "status": "healthy" if healthy else "degraded",
        "version": settings.APP_VERSION,
        "database": database,
        "redis": redis_status,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
