# API Quick Reference

Base URL: `http://localhost:8000/api/v1`

---

## Authentication

### Login
```http
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=admin&password=secret123
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": 1, "username": "admin", ... }
}
```

### Register
```http
POST /auth/register
Content-Type: application/json

{
  "username": "newuser",
  "email": "user@example.com",
  "password": "password123",
  "organization_name": "My Company"
}
```

### Get Current User
```http
GET /auth/me
Authorization: Bearer {token}
```

### Logout
```http
POST /auth/logout
Authorization: Bearer {token}
```

---

## Devices

### List Devices
```http
GET /devices?skip=0&limit=20&device_type=cisco_ios&is_active=true&search=router
Authorization: Bearer {token}
```

**Response:**
```json
{
  "total": 50,
  "page": 1,
  "page_size": 20,
  "total_pages": 3,
  "items": [
    {
      "id": 1,
      "hostname": "router-01",
      "ip_address": "192.168.1.1",
      "device_type": "cisco_ios",
      "is_active": true,
      "last_backup_at": "2025-01-31T14:30:00Z",
      "last_backup_status": "success"
    }
  ]
}
```

### Create Device
```http
POST /devices
Authorization: Bearer {token}
Content-Type: application/json

{
  "hostname": "router-01",
  "ip_address": "192.168.1.1",
  "device_type": "cisco_ios",
  "username": "admin",
  "password": "password123",
  "port": 22,
  "enable_secret": "enable_pass",
  "transport": "ssh",
  "tags": {
    "location": "datacenter-1",
    "role": "core"
  }
}
```

`transport` is `ssh` (default), `telnet` or `snmp`. SNMP is read-only: such a
device can be discovered and inventoried but not backed up.

SNMP parameters, when the device uses or also answers SNMP:

```json
{
  "snmp_version": "2c",
  "snmp_port": 161,
  "snmp_community": "public",

  "snmp_v3_user": "monitor",
  "snmp_v3_auth_key": "…",
  "snmp_v3_priv_key": "…",
  "snmp_v3_auth_protocol": "SHA",
  "snmp_v3_priv_protocol": "AES"
}
```

The community and the v3 keys are write-only: they are encrypted on the way
in, never returned, and left alone when an update omits them.

### Get Device
```http
GET /devices/{id}
Authorization: Bearer {token}
```

### Update Device
```http
PUT /devices/{id}
Authorization: Bearer {token}
Content-Type: application/json

{
  "hostname": "router-01-updated",
  "is_active": false
}
```

### Delete Device
```http
DELETE /devices/{id}
Authorization: Bearer {token}
```

### Test Connectivity
```http
POST /devices/{id}/test
Authorization: Bearer {token}
```

**Response:**
```json
{
  "success": true,
  "message": "Connection successful",
  "hostname": "router-01",
  "device_info": {
    "device_type": "cisco_ios",
    "prompt": "router-01#"
  },
  "duration": 2.5
}
```

### Bulk Upload (CSV)
```http
POST /devices/bulk-upload
Authorization: Bearer {token}
Content-Type: multipart/form-data

file=@devices.csv
```

**CSV Format:**
```csv
hostname,ip_address,device_type,username,password,port,enable_secret,tags
router-01,192.168.1.1,cisco_ios,admin,pass123,22,enable_pass,location:dc1;role:core
```

### Download CSV Template
```http
GET /devices/bulk-upload/template
Authorization: Bearer {token}
```

---

## Backups

### Trigger Backup
```http
POST /backups/trigger
Authorization: Bearer {token}
Content-Type: application/json

{
  "device_ids": [1, 2, 3]
}
```

**Response:**
```json
{
  "task_id": "abc123-def456-...",
  "device_count": 3,
  "message": "Backup started for 3 device(s)"
}
```

### Check Task Status
```http
GET /backups/tasks/{task_id}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "task_id": "abc123...",
  "status": "SUCCESS",
  "result": {
    "total": 3,
    "successful": 2,
    "failed": 1,
    "devices": [...]
  }
}
```

### List Configurations
```http
GET /backups?skip=0&limit=20&device_id=1&status=success
Authorization: Bearer {token}
```

### Get Configuration
```http
GET /backups/{config_id}
Authorization: Bearer {token}
```

### Download Configuration
```http
GET /backups/{config_id}/download
Authorization: Bearer {token}
```

**Returns:** Configuration file (text/plain)

### Delete Configuration
```http
DELETE /backups/{config_id}
Authorization: Bearer {token}
```

---

## Backup Jobs (Scheduled)

### Create Job
```http
POST /backup-jobs
Authorization: Bearer {token}
Content-Type: application/json

{
  "name": "Nightly Backup",
  "description": "Backup all devices nightly",
  "schedule_cron": "0 2 * * *",
  "is_enabled": true,
  "device_filter": {
    "tags.location": "datacenter-1"
  }
}
```

**Cron Format:** `minute hour day_of_month month day_of_week`

Examples:
- `0 2 * * *` - Daily at 2 AM
- `0 */4 * * *` - Every 4 hours
- `0 0 * * 0` - Weekly on Sunday at midnight
- `0 0 1 * *` - Monthly on the 1st at midnight

### List Jobs
```http
GET /backup-jobs?skip=0&limit=20&is_enabled=true
Authorization: Bearer {token}
```

### Get Job
```http
GET /backup-jobs/{id}
Authorization: Bearer {token}
```

### Update Job
```http
PUT /backup-jobs/{id}
Authorization: Bearer {token}
Content-Type: application/json

{
  "schedule_cron": "0 3 * * *",
  "is_enabled": false
}
```

### Delete Job
```http
DELETE /backup-jobs/{id}
Authorization: Bearer {token}
```

### Enable Job
```http
POST /backup-jobs/{id}/enable
Authorization: Bearer {token}
```

### Disable Job
```http
POST /backup-jobs/{id}/disable
Authorization: Bearer {token}
```

### Run Job Now
```http
POST /backup-jobs/{id}/run-now
Authorization: Bearer {token}
```

---

## Configuration Comparison

### Compare Configurations
```http
POST /compare
Authorization: Bearer {token}
Content-Type: application/json

{
  "config1_id": 10,
  "config2_id": 15,
  "context_lines": 3,
  "include_html": false
}
```

**Response:**
```json
{
  "is_identical": false,
  "unified_diff": "--- Configuration 1\n+++ Configuration 2\n@@ -10,7 +10,7 @@\n...",
  "structured_diff": [
    {
      "type": "replace",
      "old_start": 15,
      "old_end": 16,
      "new_start": 15,
      "new_end": 16,
      "old_lines": ["interface GigabitEthernet0/1"],
      "new_lines": ["interface GigabitEthernet0/2"]
    }
  ],
  "statistics": {
    "added_lines": 5,
    "removed_lines": 3,
    "changed_sections": 2,
    "total_changes": 8
  }
}
```

### Compare Latest vs Previous
```http
GET /compare/device/{device_id}/latest-vs-previous?context_lines=3&include_html=false
Authorization: Bearer {token}
```

### Get Comparison Summary
```http
GET /compare/summary/{config1_id}/{config2_id}
Authorization: Bearer {token}
```

**Response:**
```json
{
  "is_identical": false,
  "has_changes": true,
  "change_count": 5,
  "similarity_ratio": 0.9234,
  "line_count_diff": 2
}
```

---

## Statistics

### Dashboard Overview
```http
GET /statistics/dashboard
Authorization: Bearer {token}
```

**Response:**
```json
{
  "devices": {
    "total": 50,
    "active": 48,
    "inactive": 2,
    "by_type": {
      "cisco_ios": 30,
      "arista_eos": 10,
      "juniper_junos": 10
    }
  },
  "backups": {
    "total": 5420,
    "successful": 5380,
    "failed": 40,
    "success_rate": 99.26,
    "last_24h": {
      "successful": 150,
      "failed": 2,
      "total": 152
    }
  },
  "jobs": {
    "total": 5,
    "enabled": 4,
    "disabled": 1
  },
  "storage": {
    "total_bytes": 524288000,
    "total_mb": 500.00,
    "total_gb": 0.49,
    "avg_backup_bytes": 96723,
    "avg_backup_mb": 0.09
  },
  "recent_activity": {
    "items": [...],
    "count": 10
  }
}
```

### Backup Trends
```http
GET /statistics/backup-trends?days=30
Authorization: Bearer {token}
```

**Response:**
```json
{
  "period": {
    "start_date": "2025-01-01",
    "end_date": "2025-01-31",
    "days": 30
  },
  "trends": [
    {
      "date": "2025-01-01",
      "total": 150,
      "successful": 148,
      "failed": 2
    }
  ],
  "summary": {
    "total_backups": 4500,
    "total_successful": 4455,
    "total_failed": 45,
    "avg_per_day": 150.0
  }
}
```

### Device Health
```http
GET /statistics/device-health
Authorization: Bearer {token}
```

**Response:**
```json
{
  "summary": {
    "total_devices": 50,
    "healthy": 45,
    "warning": 3,
    "critical": 1,
    "unknown": 1
  },
  "devices": [
    {
      "device_id": 1,
      "hostname": "router-01",
      "status": "healthy",
      "last_backup_at": "2025-01-31T14:00:00Z",
      "last_backup_status": "success"
    }
  ]
}
```

**Health Status:**
- `healthy`: Last backup within 24 hours and successful
- `warning`: Last backup 24-72 hours ago
- `critical`: Last backup >72 hours ago or last backup failed
- `unknown`: Never backed up

### Storage by Device
```http
GET /statistics/storage-by-device?limit=10
Authorization: Bearer {token}
```

**Response:**
```json
{
  "devices": [
    {
      "device_id": 1,
      "hostname": "router-01",
      "backup_count": 120,
      "total_bytes": 12582912,
      "total_mb": 12.00,
      "avg_bytes": 104857,
      "avg_mb": 0.10
    }
  ],
  "total_devices_analyzed": 10
}
```

---

## Discovery and Topology

### Start a Crawl
```http
POST /discovery/run
Authorization: Bearer {token}
Content-Type: application/json

{
  "seed_device_id": 1,
  "max_hops": 2,
  "auto_add": false,
  "collect_inventory": true,
  "run_async": true
}
```

Walks outwards from the seed device, following LLDP and CDP neighbours.
`auto_add` registers discovered neighbours as devices, inheriting the seed's
credentials and transport. Queued to a worker unless `run_async` is false.

**Response (queued):**
```json
{
  "queued": true,
  "task_id": "…",
  "seed": "core-01",
  "message": "Discovery started from core-01"
}
```

### List Crawls
```http
GET /discovery/runs?limit=20
GET /discovery/runs/{id}
```

### List Adjacencies
```http
GET /discovery/neighbors?device_id=1&protocol=lldp&active_only=true
DELETE /discovery/neighbors/{id}
```

`protocol` is `lldp` or `cdp`. Adjacencies that stop being seen are marked
inactive rather than deleted, so `active_only=false` shows links that have
gone away and when they were last seen.

### Topology Graph
```http
GET /discovery/topology?diagram_id=3&active_only=true&include_unmanaged=true
```

The graph is always rebuilt from the current adjacencies. Passing a
`diagram_id` applies that diagram's saved edits on top, so a device
discovered since it was saved still appears.

**Response:**
```json
{
  "nodes": [
    {
      "key": "device:1",
      "id": 1,
      "label": "core-01",
      "type": "device",
      "managed": true,
      "link_count": 2,
      "x": 120,
      "y": 40
    }
  ],
  "links": [
    {
      "key": "device:1|Te1/1/1::device:2|Et49",
      "source": "device:1",
      "target": "device:2",
      "source_interface": "Te1/1/1",
      "target_interface": "Et49",
      "protocol": "lldp",
      "confirmed_both_ends": true,
      "manual": false
    }
  ],
  "stats": { "nodes": 6, "managed_nodes": 5, "unmanaged_nodes": 1, "links": 5 }
}
```

A cable both ends report is one link, not two.

### Saved Diagrams
```http
GET    /discovery/diagrams
POST   /discovery/diagrams
GET    /discovery/diagrams/{id}
PUT    /discovery/diagrams/{id}
DELETE /discovery/diagrams/{id}
```

A diagram stores only the edits - node positions, renamed labels, hidden
nodes, hand-drawn links:

```json
{
  "name": "Ground floor",
  "is_default": true,
  "layout": {
    "nodes": { "device:1": { "x": 120, "y": 40, "label": "Core" } },
    "links": [
      { "source": "device:1", "target": "device:2", "label": "dark fibre" }
    ],
    "hidden_links": []
  }
}
```

---

## Host Inventory

### List Hosts
```http
GET /inventory?device_id=3&vlan=10&vendor=cisco&search=aa:bb&active_only=true&seen_within_hours=24
```

Returns a paginated list of every host seen on a switch port, with its
first-seen and last-seen times and the vendor resolved from its MAC.

### Annotate a Host
```http
PATCH /inventory/{id}
{ "hostname": "reception-printer", "notes": "rack 3" }
```

### Refresh from Devices
```http
POST /inventory/refresh
{ "device_ids": [1, 2] }
```

Re-reads MAC tables and ARP without walking the topology. Omit `device_ids`
to sweep every active device.

### OUI Vendor Data
```http
GET  /inventory/oui/status
POST /inventory/oui/import   { "source": "ieee" }
POST /inventory/oui/backfill
```

`source` is `system` (an OUI database already on the host), `bundled` (the
small starter set shipped with the app), `ieee`, `url` (with `url`) or `file`
(with `path`). `backfill` re-resolves inventory rows that have no vendor, so
existing rows benefit from a fresh import without waiting to be seen again.

---

## Reports

```http
GET /inventory/reports/summary
GET /inventory/reports/by-vendor?limit=25
GET /inventory/reports/by-port?device_id=3&min_hosts=5
GET /inventory/reports/changes?days=7
GET /inventory/reports/export        # text/csv
```

`by-port` flags a port carrying more than five MACs as `likely_uplink`.
`changes` splits hosts that appeared from hosts that stopped being seen.

---

## Users and Roles

### The Caller
```http
GET  /users/me                 # user, role and effective permissions
POST /users/me/password        { "current_password": "…", "new_password": "…" }
```

### Users
```http
GET    /users?search=&role_id=&is_active=
POST   /users
GET    /users/{id}
GET    /users/{id}/permissions
PUT    /users/{id}
POST   /users/{id}/activation      { "is_active": false }
POST   /users/{id}/reset-password  { "must_change": true }
DELETE /users/{id}
```

Creating a user without a `password`, or resetting one without a
`new_password`, generates one and returns it in that response only:

```json
{
  "user": { "id": 7, "username": "newbie", "must_change_password": true },
  "generated_password": "Xk4!pQ2m…"
}
```

The last active administrator cannot be demoted, deactivated or deleted.

### Roles
```http
GET    /users/permissions      # the permission catalogue
GET    /users/roles
POST   /users/roles            { "name": "Operator", "permissions": ["devices:*"] }
GET    /users/roles/{id}
PUT    /users/roles/{id}
DELETE /users/roles/{id}
```

Permissions are `resource:action` strings; `*` and `resource:*` wildcards are
accepted. The built-in Administrator, Operator and Viewer roles can have their
permissions changed but cannot be renamed or deleted.

---

## Application Settings

```http
GET /settings
PUT /settings
GET /settings/maintenance/status
POST /settings/test-email      { "recipient": "ops@example.com" }
```

Every field on `PUT` is optional; only what is supplied is written. Values are
validated before anything is stored, so a bad cron expression or a malformed
window leaves the settings untouched.

```json
{
  "retention_days": 30,
  "retention_max_per_device": 10,
  "clear_retention_max": false,
  "retention_enabled": true,

  "default_schedule_cron": "0 2 * * *",
  "default_schedule_enabled": false,
  "max_concurrent_backups": 10,

  "smtp_host": "smtp.example.com",
  "smtp_port": 587,
  "smtp_username": "mailer",
  "smtp_password": "…",
  "clear_smtp_password": false,
  "smtp_use_tls": true,
  "smtp_from_address": "backups@example.com",
  "notifications_enabled": true,
  "notify_recipients": ["ops@example.com"],
  "notify_on_backup_failure": true,
  "notify_on_backup_success": false,
  "notify_on_config_change": true,
  "notify_on_new_host": false,

  "maintenance_timezone": "Europe/London",
  "maintenance_windows": [
    {
      "name": "Sunday night",
      "days": [6],
      "start": "22:00",
      "end": "02:00",
      "suppress_backups": true,
      "suppress_notifications": true
    }
  ]
}
```

`days` is Monday=0 to Sunday=6, and a window may wrap midnight - 22:00-02:00
on Sunday runs into Monday morning. A scheduled job that comes due inside an
open window is held, and its next run time still advanced so held runs cannot
stack up.

The SMTP password is write-only: a read returns `smtp_password_set` only.

---

## Remote Backup Targets

```http
GET    /settings/targets?enabled_only=true
POST   /settings/targets
GET    /settings/targets/{id}
PUT    /settings/targets/{id}
DELETE /settings/targets/{id}
POST   /settings/targets/{id}/test
POST   /settings/targets/{id}/upload
```

```json
{
  "name": "Archive",
  "protocol": "sftp",
  "host": "archive.example.com",
  "port": 22,
  "username": "backup",
  "password": "…",
  "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----…",
  "remote_path": "/srv/configs",
  "use_device_subdirectories": true,
  "is_enabled": true,
  "upload_on_backup": true
}
```

`protocol` is `sftp`, `ftp` or `ftps`; a private key is SFTP only. With
`upload_on_backup`, each configuration is copied as soon as it is stored - as
a separate task, so an archive server being down never fails the backup.

`/upload` sends the latest backup of every device, or the specific
`configuration_ids` or `device_ids` given.

`/test` connects and checks the remote directory is writable, returning the
outcome rather than raising:

```json
{ "success": false, "message": "Connection refused" }
```

---

## Common Response Patterns

### Success Response
```json
{
  "success": true,
  "message": "Operation completed successfully",
  "data": { ... }
}
```

### Error Response
```json
{
  "detail": "Error message describing what went wrong"
}
```

### Paginated Response
```json
{
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5,
  "items": [...]
}
```

---

## HTTP Status Codes

- `200 OK` - Request successful
- `201 Created` - Resource created
- `400 Bad Request` - Invalid request data
- `401 Unauthorized` - Missing or invalid authentication
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Resource not found
- `409 Conflict` - Duplicate resource
- `500 Internal Server Error` - Server error

---

## Authentication Header

All authenticated endpoints require:
```http
Authorization: Bearer {access_token}
```

Get token from `/auth/login` endpoint.

---

## Pagination Parameters

All list endpoints support:
- `skip` (default: 0) - Number of records to skip
- `limit` (default: 20, max: 100) - Number of records to return

---

## Permissions

Endpoints are guarded by a `resource:action` permission rather than an admin
flag. A 403 names the permission that was missing.

| Resource | Actions |
|---|---|
| `devices` | `read`, `write`, `delete`, `test` |
| `backups` | `read`, `trigger`, `delete` |
| `jobs` | `read`, `write`, `delete` |
| `discovery` | `read`, `run`, `write` |
| `inventory` | `read`, `write` |
| `reports` | `read` |
| `users` | `read`, `write`, `delete`, `reset_password` |
| `settings` | `read`, `write` |
| `targets` | `read`, `write`, `delete` |
| `audit` | `read` |

`GET /users/permissions` returns the catalogue with descriptions, and
`GET /users/me` returns what the caller actually holds.

---

## Transports

Supported values for `transport`:
- `ssh` (default)
- `telnet`
- `snmp` - read-only, so discovery and inventory only, never a backup

---

## Device Types

Supported values for `device_type`:
- `cisco_ios`
- `cisco_ios_xe`
- `cisco_nxos`
- `arista_eos`
- `fortinet`
- `juniper_junos`
- `aruba_os`
- `hp_comware`
- `hp_procurve`

---

## Interactive Documentation

**Swagger UI:** http://localhost:8000/docs
- Try out endpoints directly
- See request/response schemas
- Copy curl commands

**ReDoc:** http://localhost:8000/redoc
- Clean, searchable documentation
- Download OpenAPI spec

---

**Version:** 1.1.0
**Last Updated:** 2026-08-27
