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
  "tags": {
    "location": "datacenter-1",
    "role": "core"
  }
}
```

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

**Version:** 1.0.0
**Last Updated:** 2025-01-31
