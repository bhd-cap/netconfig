# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-tenant network device configuration backup system with web UI. Supports 9 network device OS types (Cisco IOS/IOS-XE/NX-OS, Arista EOS, Fortinet FortiOS, Juniper JunOS, Aruba ArubaOS, HPE Comware/ProCurve). Built with FastAPI backend, React frontend, Celery task queue, and PostgreSQL database.

## Development Commands

### Docker Environment (Primary)

```bash
# Build all services
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f [service_name]

# Stop services
docker-compose down

# Rebuild and restart specific service
docker-compose up -d --build backend
```

### Backend Development

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Initialize database with admin user
python init_db.py

# Run backend locally (requires postgres and redis running)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run single test file
pytest tests/test_devices.py -v

# Run specific test
pytest tests/test_devices.py::test_create_device -v

# Run with coverage
pytest --cov=app tests/
```

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Lint
npm run lint
```

### Database Access

```bash
# Connect to PostgreSQL in Docker
docker-compose exec postgres psql -U netbackup -d netconfig_backup

# Common queries
\dt                                           # List tables
SELECT * FROM users;                          # View users
SELECT id, hostname, ip_address FROM devices; # View devices
\q                                            # Exit
```

### Celery Tasks

```bash
# Start Celery worker locally
cd backend
celery -A app.celery_app worker --loglevel=info

# Start Celery beat scheduler
celery -A app.celery_app beat --loglevel=info

# Monitor tasks with Flower
celery -A app.celery_app flower

# Access Flower UI
http://localhost:5555
```

## Architecture

### Multi-Tenant Design

**Critical**: All data is scoped by `organization_id`. Every API endpoint extracts organization_id from JWT token and filters queries accordingly. Never write queries that cross organization boundaries.

**Authentication Flow**:
1. User logs in → JWT token generated with `user_id` and `organization_id`
2. Token sent in `Authorization: Bearer <token>` header
3. `get_organization_id()` dependency extracts org_id from token
4. All repository methods filter by `organization_id`

**Example**:
```python
# CORRECT - Tenant-scoped
devices = device_repo.get_by_organization(organization_id, skip, limit)

# WRONG - Cross-tenant query
devices = db.query(Device).all()  # Don't do this!
```

### Backend Architecture (FastAPI)

**Layered Architecture**:
```
API Routes (app/api/v1/*.py)
    ↓
Services (app/services/*.py) - Business logic
    ↓
Repositories (app/repositories/*.py) - Data access
    ↓
Models (app/models/*.py) - SQLAlchemy ORM
```

**Key Services**:
- `DeviceConnector` - SSH connections via Netmiko, handles 9 device types
- `ConfigurationRetriever` - Orchestrates backup workflow (connect → retrieve → save → log)
- `ConfigurationStorage` - Multi-tenant file storage at `/backups/{org_id}/{hostname}/`
- `ConfigurationComparison` - Generates diffs using Python's difflib

**Repository Pattern**:
All repositories extend `BaseRepository[ModelType]` with standard CRUD operations. Tenant-scoped methods always require `organization_id` parameter.

**Important**: When adding new endpoints, always use `get_organization_id()` dependency and pass to repository methods.

### Frontend Architecture (React)

**Authentication**: AuthContext manages JWT tokens in localStorage. All API calls auto-include token via axios interceptor.

**Routing**: React Router v6 with ProtectedRoute wrapper. Admin-only routes check `user.is_admin`.

**State Management**: TanStack Query for server state (caching, refetching). Local state with useState/useContext.

**API Client**: Configured axios instance in `lib/api.ts` with request/response interceptors.

### Celery Task Queue

**Tasks** (`app/tasks/backup.py`):
- `backup_device_task` - Single device backup with 3 retry attempts
- `bulk_backup_task` - Multiple devices sequentially
- `scheduled_backup_task` - Executed by Celery Beat for scheduled jobs
- `check_scheduled_jobs_task` - Runs every 60s to trigger due jobs
- `apply_retention_policy_task` - Cleanup old backups

**Beat Schedule** (`app/celery_app.py`):
- Checks for scheduled jobs every 60 seconds
- Cleanup task daily at 3 AM

**Important**: Tasks create their own database session (`SessionLocal()`) and must close it in `finally` block.

## Database Schema

**Core Models**:
- `Organization` - Tenant root entity
- `User` - Authentication, linked to organization
- `Device` - Network devices with encrypted credentials
- `Configuration` - Backup records with file metadata
- `BackupJob` - Scheduled backup jobs with cron syntax
- `AuditLog` - All user actions

**Relationships**:
```
Organization (1) ←→ (many) Users
Organization (1) ←→ (many) Devices
Device (1) ←→ (many) Configurations
Organization (1) ←→ (many) BackupJobs
```

**Encryption**: Device passwords stored encrypted with Fernet (symmetric encryption). Key in `ENCRYPTION_KEY` environment variable.

## Device Connection System

**Supported Device Types**: Map to Netmiko device types in `app/config/device_types.py`

**Connection Flow**:
1. Create `DeviceConnector` with device details
2. Decrypt password from database
3. Use context manager (`with connector:`) for auto-cleanup
4. Execute device-specific commands (defined per device type)
5. Handle enable mode for Cisco devices

**Commands by Vendor**:
- Cisco: `show running-config` (requires enable mode)
- Arista: `show running-config`
- Fortinet: `show full-configuration`
- Juniper: `show configuration | display set`
- HPE Comware: `display current-configuration`

**Error Handling**: Catches `DeviceConnectionError` and `DeviceCommandError`, logs failures, updates device status.

## File Storage

**Structure**: `/backups/{organization_id}/{hostname}/{hostname}_{timestamp}.cfg`

**Format**: `router-nyc-01_20250131_143022.cfg`

**Checksum**: SHA256 hash stored in database for deduplication.

**Retention**: Configurable via `DEFAULT_RETENTION_DAYS` (default 90). Applied per-device by `apply_retention_policy()`.

## API Design Patterns

**All list endpoints**:
- Pagination: `skip` (default 0), `limit` (default 20, max 100)
- Return `PaginatedResponse[T]` with total, page, page_size, total_pages, items

**Authentication**:
- Use `Depends(get_current_user)` for authenticated endpoints
- Use `Depends(get_current_admin_user)` for admin-only endpoints
- Use `Depends(get_organization_id)` to extract org from token

**Error Responses**:
- 400 Bad Request - Invalid input
- 401 Unauthorized - Missing/invalid token
- 403 Forbidden - Insufficient permissions
- 404 Not Found - Resource not found
- 409 Conflict - Duplicate resource

## Configuration Comparison

**Engine**: Python's `difflib` library generates:
1. Unified diff (text format)
2. HTML diff (styled side-by-side)
3. Structured diff (JSON with change blocks)

**Statistics**: Tracks added lines, removed lines, changed sections, similarity ratio.

**Important**: Always verify both configs belong to same device before comparing.

## Environment Variables

**Backend** (`backend/.env`):
- `DATABASE_URL` - PostgreSQL connection
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` - Redis URLs
- `SECRET_KEY` - JWT signing (generate with `openssl rand -hex 32`)
- `ENCRYPTION_KEY` - Fernet key (generate with `Fernet.generate_key()`)
- `BACKUP_BASE_PATH` - File storage location (default `/backups`)
- `DEFAULT_RETENTION_DAYS` - Backup retention (default 90)
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` - Default admin credentials

**Frontend** (`frontend/.env`):
- `VITE_API_URL` - Backend API URL (default `http://localhost:8000/api/v1`)

## Common Workflows

### Adding New API Endpoint

1. Create Pydantic schema in `app/schemas/`
2. Add repository method if needed in `app/repositories/`
3. Create route in `app/api/v1/`
4. Use `get_organization_id()` dependency for tenant scoping
5. Add audit logging for important actions
6. Update API router in `app/api/v1/__init__.py`

### Adding New Device Type

1. Add device type to `DEVICE_TYPES` in `app/config/device_types.py`
2. Map to Netmiko type
3. Specify configuration command
4. Set enable mode requirement
5. Update TypeScript types in `frontend/src/types/index.ts`

### Creating Database Migration

```bash
# Make model changes in app/models/
# Generate migration
alembic revision --autogenerate -m "add new field to device"
# Review generated migration in alembic/versions/
# Apply migration
alembic upgrade head
```

## Testing Strategy

**Backend**: API endpoint tests connect to real database and Redis (use test fixtures to create/cleanup test data). Mock Netmiko connections for device tests.

**Frontend**: Not yet implemented (placeholder pages exist for Devices, Backups, Jobs, Compare).

## Deployment

**Production checklist**:
1. Change all default passwords in `.env`
2. Use strong `SECRET_KEY` and `ENCRYPTION_KEY`
3. Set `DEBUG=false`
4. Configure SSL/TLS in nginx
5. Set up database backups
6. Configure firewall rules
7. Monitor Celery queues with Flower

**Docker services**: postgres, redis, backend, celery_worker, celery_beat, flower, frontend, nginx (8 total)

## Current Status (75% Complete)

**Complete**:
- Backend API (35 endpoints)
- Authentication system
- Device management
- Backup system (manual + scheduled)
- Configuration comparison
- Dashboard statistics API
- Frontend foundation (auth + dashboard)

**Incomplete**:
- Frontend UI pages (Devices, Backups, Jobs, Compare) - placeholders exist
- Charts on dashboard
- Integration tests
- User documentation

## Key Files to Reference

- `BACKEND_SUMMARY.md` - Complete backend feature overview
- `API_REFERENCE.md` - All 35 API endpoints with examples
- `CHECKPOINT.md` - Development resume point with setup instructions
- `DOCKER_TESTING_GUIDE.md` - Deployment and troubleshooting
