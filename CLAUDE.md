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

# Install dependencies (runtime only)
pip install -r requirements.txt

# Install with test and lint tooling
pip install -r requirements-dev.txt

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

## Performance Conventions

These exist because the obvious version of each was measurably worse. Keep
them in mind when extending the corresponding layer.

**Sessions**: `SessionLocal` sets `expire_on_commit=False` and the declarative
base sets `eager_defaults`. A `create()` is therefore one
`INSERT ... RETURNING` - do not add a `refresh()` after it, and do not rely on
attributes being reloaded from the database after a commit.

**Batched writes**: repository `create`/`create_many`/`update` and
`audit_repo.log_action` all take `commit=False`. When a request or task writes
several rows, pass `commit=False` and commit once at the end rather than
per row.

**Counting**: use `select(func.count(Model.id))`, not `Query.count()`, which
wraps the entity SELECT in a subquery.

**Relationships in lists**: a listing that reads `row.related.field` must
eager-load it (`contains_eager` when the join already exists, otherwise
`selectinload`), or select the specific columns. Reading a lazy relationship
inside a loop is one extra query per row.

**Aggregates**: prefer one query with `func.count(...).filter(...)` /
`func.sum(...)` over several counting queries. Never use `func.case(...)` -
that emits a call to a SQL function named "case", which does not exist. Import
`case` from `sqlalchemy` instead.

**Diffing**: `ConfigurationComparison` computes one `SequenceMatcher` per
comparison and derives the unified diff, the structured diff and the
statistics from its cached opcodes. Do not call `difflib.unified_diff` or
build a second matcher alongside it. `HtmlDiff` is expensive and only rendered
when `include_html=True`.

**Backups**: retrieval happens off the database session (see
`_fetch_and_store`), which is what makes concurrent bulk backups safe. Keep
network and disk work there, and all ORM work in `_persist`.

**Indexes**: hot query paths are backed by composite indexes in migration
`0002`. A new frequently-run query that filters and orders on different
columns probably needs one too.

## API Design Patterns

**All list endpoints**:
- Pagination: `skip` (default 0), `limit` (default 20, max 100)
- Return `PaginatedResponse[T]` with total, page, page_size, total_pages, items

**Sorting**: a sortable column must be in a catalogue - `SORTABLE_COLUMNS` in
`app/repositories/device.py` maps a name to a mapped column, and anything else
is a 400. A column name straight from a query string would otherwise be
interpolated into SQL. Append a stable tiebreak to the ORDER BY (hostname, for
devices) or paging through equal values repeats and skips rows.

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

## Discovery, Inventory and Topology

**Transports**: `Device.transport` is `ssh`, `telnet` or `snmp`. SSH and telnet
both drive the CLI through Netmiko (`app/config/discovery_commands.py` maps a
device type to its `*_telnet` driver). SNMP is read-only, so an SNMP device can
be discovered and inventoried but never backed up - `_retrieve_config` says so
explicitly rather than failing later inside the transport.

**pysnmp 7 is asyncio-only.** `app/services/snmp_client.py` drives it from sync
code by building a fresh event loop and `SnmpEngine` per call, because an
engine cannot outlive the loop it was created on. Do not hoist the engine into
a module-level singleton.

**Parsing**: `app/services/parsers.py` anchors on the MAC address rather than
on column positions, because every vendor's table is laid out differently and
several change layout between releases. `parse()` never raises - a device that
returns something unexpected yields an empty result, not a failed crawl.

**Upsert keys must be NOT NULL.** `neighbors.remote_interface` and
`host_inventory.vlan` default to `''` and `0` for this reason: NULLs never
compare equal, so `ON CONFLICT` would never match and every run would insert
duplicates. Any new upsert key needs the same treatment.

**Ageing, not deleting**: adjacencies and inventory rows are marked inactive
once they stop being seen. "Last seen on port 12 three weeks ago" is the useful
answer, and it needs the row to survive.

**Topology**: the graph is always rebuilt from the current adjacencies and
never stored. A saved diagram holds only the user's edits - positions, renamed
labels, hidden nodes, hand-drawn links - so a device discovered afterwards
still appears and a hand-arranged layout is not thrown away by a refresh.
`_link_key` normalises direction, so a cable both ends report is one edge.

**Tiers and drill-down**: `assign_tiers()` puts every node in `core`,
`distribution`, `access`, `edge` or `host`, and `build_graph()` returns
infrastructure only unless asked otherwise. A switch with 200 MACs behind it
makes a diagram nobody can read, and those hosts are inventory rather than
topology; `expand=<node key>` unfolds one node's hosts without unfolding the
network. Two details worth keeping:

- A managed device is tiered by how many *other infrastructure nodes* it links
  to, never by its total link count. Counting hosts would promote a busy
  access switch above the core.
- An unmanaged neighbour is classified from its advertised capabilities, but
  promoted to `edge` if it has two or more links regardless. Plenty of
  switches advertise nothing, and one cabled to two devices is forwarding
  between them whoever owns it.

**Discovered devices are probed, not assumed.** `_register_discovered()` runs
`discovery_probe.assess()` before writing the row: SNMP first (read-only and
fast, and sysDescr identifies a platform better than anything short of logging
in), then each vault CLI credential over SSH, then telnet. `is_active` is set
from `backup_eligible`, which is true only when a CLI login actually
succeeded - a device that authenticates nothing stays in the inventory, gets
crawled, and is kept off the backup schedule with the reason in `auth_error`.
`identify_platform()` returns **None** when nothing matches rather than
guessing, which is what stopped every neighbour of a Cisco seed being
registered as `cisco_ios` over SSH.

Two things a change here must preserve: the SNMP credential that *answered* is
stored on the new device (not the seed's, or a later crawl polls it with the
wrong community), and each transport's outcome is upserted into `device_probes`
on `(device_id, transport)` so the detail view can say "SSH refused, telnet
timed out, 4 credentials tried".

**OUI**: prefixes are stored as six lowercase hex characters.
`import_entries()` normalises whatever it is given, because the IEEE registry
writes them uppercase and a lookup normalises the MAC to lowercase. The bundled
`app/data/oui_common.csv` is a 12-prefix starter set, not the registry; the
real thing is imported from the system, a URL or IEEE.

## Scheduled Job Device Filters

`BackupJob.device_filter` is a JSONB document resolved by
`app/services/device_filter.py`. Every criterion present is ANDed; a list
within one criterion is ORed. `{}` or `None` means "every device that can be
backed up", so a job created before filtering existed keeps its old behaviour.

Keys: `device_ids`, `exclude_device_ids`, `device_types`, `locations`,
`hostname_pattern` (a glob, not SQL), `tags` (all pairs must match, via JSONB
`@>`), `transports`, `include_inactive`, `include_snmp`.

- **Unknown keys are rejected**, not ignored. A filter that silently matches
  everything or nothing is the worst failure mode here, so `validate()` runs
  on create and update rather than only at run time.
- **SNMP devices are excluded by default.** SNMP cannot retrieve a
  configuration, so including one guarantees a failure on every run. Naming
  `snmp` in `transports`, or setting `include_snmp`, opts back in.
- **A stored filter that no longer validates fails the run**, rather than
  falling back to everything - falling back would silently widen the job's
  scope.
- **Every early return must advance `next_run_at`.**
  `check_scheduled_jobs_task` picks up anything whose next run has passed, so
  a path that returns without advancing it re-fires every 60 seconds. That
  applies to the no-match, invalid-filter and maintenance-window paths alike.

`resolve()` selects only `Device.id`, so a candidate's encrypted credentials
are never materialised just to pick a set.

## Permissions

Permissions are `resource:action` strings from `app/core/permissions.py`.
Endpoints use `Depends(require_permission("devices:write"))` rather than the
`is_admin` flag. `*` and `resource:*` wildcards are supported, and the seeded
Administrator role holds `*` so a permission added later needs no backfill.

A user with no role falls back to the legacy `is_admin`/`is_superuser` flags,
so accounts that predate roles keep working. `is_admin` is kept in step with
the role's permissions whenever either changes.

An organization can never be left without an administrator: demoting,
deactivating or deleting the last active one, or stripping `users:write` from
the role that grants it, is refused.

The frontend reads `/users/me` to decide what to render. That is a courtesy,
not a boundary - the API checks every permission itself.

## Credential Vault

`Credential` rows are the ordered list of logins discovery tries against a
device it has just found: `kind` is `cli` (SSH and telnet) or `snmp`, and
`priority` is the order within a kind. `POST /credentials/reorder` rewrites
priority as `position * 10`, so inserting between two later needs no
renumbering.

Order is not cosmetic - every credential that fails costs a connection
timeout, and a crawl tries them against every neighbour it finds.

`CredentialAttempt` (`app/services/credentials.py`) is a plain dataclass
holding *decrypted* values, deliberately not an ORM object: the probe runs off
the session in a thread pool, and passing a `Credential` row into a worker
thread would use the session from two threads at once.

`vault.list_for_kind()` returns the enabled credentials for a kind; the seed
device's own credentials are appended as a last resort so a crawl still works
before anyone has filled the vault in.

## Secrets

Device passwords, enable secrets, SNMP communities and v3 keys, vault
credentials, the SMTP password, and backup-target passwords and private keys
are all Fernet encrypted with `ENCRYPTION_KEY`.

Every one of them is write-only over the API: a read returns whether one is
stored, never the value, and an update that omits the field leaves the stored
secret alone. Clearing one takes an explicit flag. Follow this pattern for any
new secret.

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
4. Use `get_organization_id()` for tenant scoping and
   `Depends(require_permission("<resource>:<action>"))` for authorization
5. Add audit logging for important actions
6. Update API router in `app/api/v1/__init__.py`
7. Add an API-level test under `tests/integration/test_api_*.py`

Watch route ordering: a literal path has to be declared before a matching
parameterised one, or `/users/roles` is swallowed by `/users/{user_id}`.

### Adding a New Permission

1. Add it to `PERMISSION_CATALOGUE` in `app/core/permissions.py`
2. Add it to whichever entries in `SYSTEM_ROLES` should hold it - the
   Administrator role holds `*`, so it needs no change
3. Use it in `require_permission()` on the endpoints it guards

The role editor and the permission list endpoint are both driven from the
catalogue, so nothing in the frontend needs updating.

### Adding New Device Type

1. Add device type to `DEVICE_TYPES` in `app/config/device_types.py`
2. Map to Netmiko type
3. Specify configuration command
4. Set enable mode requirement
5. Add its LLDP/CDP/MAC/ARP commands to `DISCOVERY_COMMANDS` in
   `app/config/discovery_commands.py`, and a parser in
   `app/services/parsers.py` if none of the existing formats fits
6. Add it to `TELNET_DEVICE_TYPES` if Netmiko has a `*_telnet` driver for it
7. Update TypeScript types in `frontend/src/types/index.ts`

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

**Backend**: `backend/tests/` needs a real PostgreSQL and the same environment
variables the app uses (`DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`). The
integration tests truncate their tables on setup, so point `DATABASE_URL` at a
scratch database, never a real one.

```bash
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
pytest -q
```

- `tests/unit/` - parsers and transports, no database
- `tests/integration/` - services and the API, against a real database
- `tests/integration/test_api_*.py` go through the real FastAPI stack with only
  `get_current_user` and `get_db` overridden, so route ordering, permission
  dependencies and response shapes are all covered

Mock at the connector boundary for device tests - the parsers have their own
tests against real captured output, so a service test should not re-parse.

**Frontend**: No unit test suite yet. `npm run build` runs `tsc` first, so type
errors fail the build - treat that as the type-checking gate. There is no
ESLint configuration file, so `npm run lint` does not currently run.

## Deployment

**Production checklist**:
1. Change all default passwords in `.env`
2. Use strong `SECRET_KEY` and `ENCRYPTION_KEY`
3. Set `DEBUG=false`
4. Configure SSL/TLS in nginx
5. Set up database backups
6. Configure firewall rules
7. Monitor Celery queues with Flower

**Docker services** (6 by default): postgres, redis, backend, celery_worker,
celery_beat, frontend.

Two more are behind profiles so they cost nothing unless asked for:
- `--profile monitoring` adds flower (task monitor, :5555)
- `--profile proxy` adds nginx (single-origin TLS termination, :80/:443)

The frontend image builds the bundle and serves it from nginx; it is not the
Vite dev server. The backend runs uvicorn workers, not `--reload`. Neither
mounts host source, so a container runs the code it was built with - use
`docker-compose.override.yml` for a live-reload dev loop.

**Install**: `./install.sh` (or `curl -fsSL <raw install.sh URL> | bash`)
generates `.env` with random secrets, builds, waits for `/api/v1/health`,
migrates, and creates the admin user with the temporary password `changeme`.
Re-running keeps existing secrets. `deploy.sh` now just delegates to it.

### LXC / systemd deployment (`lxc/`)

A second, Docker-free deployment installs the same code natively:
`lxc/install.sh` runs inside a Debian/Ubuntu container or host,
`lxc/proxmox-create-lxc.sh` runs on a Proxmox host and creates the container
first. See `lxc/README.md`.

Things to know before changing it:
- Config lives at `/etc/netconfig-backup/netconfig.env`, **not**
  `/etc/netconfig` - that path is a regular file on every Debian/Ubuntu system
  (`libtirpc-common`, see `netconfig(5)`), so a directory cannot be created
  there.
- That file is a systemd `EnvironmentFile`: bare `KEY=value`, no shell
  quoting. Never `source` it from a shell - `ADMIN_ORG_NAME=Default
  Organization` would execute `Organization` as a command. `run_as_service`
  parses it literally the way systemd does.
- The unit files in `lxc/systemd/` are templates; `@APP_DIR@`, `@DATA_DIR@`,
  `@CONFIG_FILE@` and `@USER@` are substituted at install time. Same for
  `@HTTP_PORT@`, `@API_PORT@` and `@LISTEN6@` in `lxc/nginx/netconfig.conf`.
- The IPv6 listener is added only when `/proc/net/if_inet6` exists; a
  hard-coded `listen [::]:80` makes nginx fail to start on IPv4-only hosts.
- `$APP_DIR` is deliberately not world-readable. nginx runs as `www-data`, so
  only traversal on `$APP_DIR` and `$APP_DIR/frontend` plus read on
  `frontend/dist` is granted (`publish_bundle`).
- The LXC build sets `VITE_API_URL=/api/v1`, so nginx serves the UI and proxies
  the API on one origin and CORS is not involved. The Docker build bakes in an
  absolute URL because the API is published on a separate port there.

## Current Status

**Complete**:
- Backend API (98 endpoints)
- Authentication, roles and per-permission authorization
- Device management over SSH, telnet and SNMP, with column sorting, bulk edit
  and delete, and a detail view of everything discovery learned
- Credential vault: an ordered list of CLI and SNMP credentials discovery
  tries, with per-credential success counts and a test-against-one-device
  endpoint
- Backup system (manual + scheduled, concurrent), held during maintenance
  windows, with per-job device filtering
- Configuration comparison
- Dashboard statistics API
- Neighbour discovery, seed crawl and the derived topology graph, tiered into
  core/distribution/access/edge/host with per-node drill-down
- Editable, saved topology diagrams, full screen or in their own tab
- Host inventory with first/last seen, the name each host announces over
  LLDP/CDP, and OUI vendor mapping
- Connected-device reports and a CSV export
- SFTP/FTP export of stored configurations
- User administration, application settings and maintenance windows
- Frontend pages for all of the above
- Backend test suite (407 tests) and browser smoke tests
- One-line installer

**Incomplete**:
- Frontend unit tests and an ESLint configuration
- User documentation

## Key Files to Reference

- `BACKEND_SUMMARY.md` - Complete backend feature overview
- `API_REFERENCE.md` - API endpoints with examples
- `CHECKPOINT.md` - Development resume point with setup instructions
- `DOCKER_TESTING_GUIDE.md` - Deployment and troubleshooting
