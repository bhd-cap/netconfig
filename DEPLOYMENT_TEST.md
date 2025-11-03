# Deployment Testing Guide

This guide will help you test the network configuration backup application infrastructure.

## 📋 Prerequisites

Before you begin, ensure you have:

- **Docker** 20.10+ installed
- **Docker Compose** 2.0+ installed
- At least **4GB RAM** available
- At least **10GB disk space** available
- Ports available: **80, 443, 3000, 5555, 8000**

### Check Docker Installation

```bash
docker --version
docker-compose --version
```

## 🚀 Step 1: Start the Application

### Option A: Start All Services

```bash
# Start all containers in detached mode
docker-compose up -d

# View logs from all services
docker-compose logs -f
```

### Option B: Start with Live Logs

```bash
# Start all containers and follow logs
docker-compose up
```

Expected output: You should see all services starting:
- ✓ postgres (healthy)
- ✓ redis (healthy)
- ✓ backend (starting)
- ✓ celery_worker (starting)
- ✓ celery_beat (starting)
- ✓ flower (starting)
- ✓ frontend (starting)
- ✓ nginx (starting)

## 🔍 Step 2: Verify Service Health

### Check Running Containers

```bash
docker-compose ps
```

All services should show "Up" or "Up (healthy)" status.

### Check Individual Service Logs

```bash
# Backend API logs
docker-compose logs -f backend

# Database logs
docker-compose logs -f postgres

# Celery worker logs
docker-compose logs -f celery_worker

# Frontend logs
docker-compose logs -f frontend
```

### Test Network Connectivity

```bash
# Test PostgreSQL connection
docker-compose exec postgres pg_isready -U netbackup

# Test Redis connection
docker-compose exec redis redis-cli ping
# Expected output: PONG
```

## 💾 Step 3: Initialize the Database

### Run Database Migrations

```bash
# Run Alembic migrations to create all tables
docker-compose exec backend alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial migration - create all tables
```

### Create Admin User and Default Organization

```bash
# Run the initialization script
docker-compose exec backend python init_db.py
```

Expected output:
```
============================================================
DATABASE INITIALIZATION
============================================================
Creating database tables...
✓ Database tables created
✓ Created organization: Default Organization (ID: 1)
✓ Created admin user: admin (ID: 1)
  Email: admin@example.com
  Password: changeme123
⚠️  CHANGE THE DEFAULT PASSWORD IMMEDIATELY!
============================================================
✓ Database initialization completed successfully!
============================================================

You can now login with:
  Username: admin
  Password: changeme123

Access the application at:
  Web UI:    http://localhost
  API Docs:  http://localhost:8000/docs
  Flower:    http://localhost:5555
============================================================
```

### Verify Database Tables

```bash
# Connect to PostgreSQL and check tables
docker-compose exec postgres psql -U netbackup -d netbackup -c "\dt"
```

Expected tables:
- organizations
- users
- devices
- configurations
- backup_jobs
- audit_logs
- alembic_version

### Check Created Data

```bash
# Check organizations
docker-compose exec postgres psql -U netbackup -d netbackup -c "SELECT * FROM organizations;"

# Check users
docker-compose exec postgres psql -U netbackup -d netbackup -c "SELECT id, username, email, is_admin, is_superuser FROM users;"
```

## 🌐 Step 4: Test Web Endpoints

### Test Frontend

Open in your browser:
```
http://localhost
```

You should see:
- ✓ Network Config Backup System landing page
- ✓ Backend API status showing "connected"
- ✓ API response displayed
- ✓ Links to documentation

### Test Backend API

#### Method 1: Browser

Open in your browser:
```
http://localhost:8000/
```

Expected JSON response:
```json
{
  "app": "Network Config Backup System",
  "version": "1.0.0",
  "status": "running",
  "docs": "/docs"
}
```

#### Method 2: curl

```bash
# Test root endpoint
curl http://localhost:8000/

# Test health check
curl http://localhost:8000/health

# Test API health check
curl http://localhost:8000/api/v1/health
```

### Test API Documentation

Open in your browser:
```
http://localhost:8000/docs
```

You should see the **Swagger UI** with all API endpoints.

Alternative documentation:
```
http://localhost:8000/redoc
```

### Test Celery Flower (Task Monitor)

Open in your browser:
```
http://localhost:5555
```

You should see the Flower dashboard showing:
- Active workers
- Registered tasks
- Task history (empty for now)

## 🧪 Step 5: Test Core Functionality

### Test Configuration File Storage

```bash
# Create a test backup directory structure
docker-compose exec backend python -c "
from app.services.storage import storage_service
from datetime import datetime

# Test storage service
result = storage_service.save_config(
    organization_id=1,
    hostname='test-router-01',
    config_text='! Test configuration\ninterface GigabitEthernet0/1\n description Test\n',
    timestamp=datetime.utcnow()
)
print('Storage test result:', result)
"
```

Expected output showing file path, size, and checksum.

### Test Device Connection Manager (Dry Run)

```bash
# Test device connector initialization
docker-compose exec backend python -c "
from app.services.device_connector import DeviceConnector
from app.utils.encryption import encryption_service

# Encrypt a test password
encrypted = encryption_service.encrypt('testpassword')
print('Encryption test passed')

# Create connector (won't connect, just initialize)
try:
    connector = DeviceConnector(
        hostname='test-device',
        ip_address='192.168.1.1',
        device_type='cisco_ios',
        username='admin',
        encrypted_password=encrypted,
    )
    print('Device connector initialized successfully')
    print(f'Device type: {connector.device_type}')
    print(f'Config command: {connector.config_command}')
except Exception as e:
    print(f'Error: {e}')
"
```

### Test Encryption Service

```bash
# Test credential encryption/decryption
docker-compose exec backend python -c "
from app.utils.encryption import encryption_service

# Test encryption
plain_text = 'MySecurePassword123!'
encrypted = encryption_service.encrypt(plain_text)
decrypted = encryption_service.decrypt(encrypted)

print(f'Original:  {plain_text}')
print(f'Encrypted: {encrypted[:50]}...')
print(f'Decrypted: {decrypted}')
print(f'Match: {plain_text == decrypted}')
"
```

Expected: Match should be `True`

## 📊 Step 6: Monitor Resources

### Check Container Resource Usage

```bash
docker stats
```

### Check Disk Usage

```bash
# Check Docker disk usage
docker system df

# Check backup storage
docker-compose exec backend du -sh /backups
```

### Check Database Size

```bash
docker-compose exec postgres psql -U netbackup -d netbackup -c "
SELECT pg_size_pretty(pg_database_size('netbackup')) AS database_size;
"
```

## 🛑 Step 7: Stop and Cleanup

### Stop All Services

```bash
# Stop containers (preserves data)
docker-compose down

# Stop and remove volumes (deletes all data)
docker-compose down -v
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart backend
```

### View Service Logs After Restart

```bash
# Check if services recovered properly
docker-compose logs --tail=50 backend
docker-compose logs --tail=50 celery_worker
```

## ✅ Success Criteria

Your deployment is successful if:

- [x] All 8 containers are running
- [x] PostgreSQL is healthy and accessible
- [x] Redis is healthy and responding to PING
- [x] Backend API returns JSON responses
- [x] API documentation is accessible at `/docs`
- [x] Frontend loads and shows API connection
- [x] Database tables are created
- [x] Admin user exists in database
- [x] Flower dashboard is accessible
- [x] Encryption/decryption works correctly
- [x] File storage can create directories and save files

## 🐛 Troubleshooting

### Backend Won't Start

**Issue**: Backend container exits immediately

**Solution**:
```bash
# Check logs
docker-compose logs backend

# Common causes:
# 1. Database connection failed - check DATABASE_URL in .env
# 2. Missing dependencies - rebuild image
docker-compose build backend
```

### Database Connection Failed

**Issue**: `could not connect to server`

**Solution**:
```bash
# Ensure postgres is healthy
docker-compose ps postgres

# Check postgres logs
docker-compose logs postgres

# Restart postgres
docker-compose restart postgres

# Wait for postgres to be ready
docker-compose exec postgres pg_isready -U netbackup
```

### Frontend Shows API Disconnected

**Issue**: Frontend can't connect to backend

**Solution**:
```bash
# Check if backend is running
docker-compose ps backend

# Test backend directly
curl http://localhost:8000/

# Check backend logs for errors
docker-compose logs backend

# Verify CORS settings in .env
# Should include: CORS_ORIGINS=http://localhost:3000,http://localhost
```

### Port Already in Use

**Issue**: `Bind for 0.0.0.0:XXXX failed: port is already allocated`

**Solution**:
```bash
# Find what's using the port (example for port 8000)
lsof -i :8000  # On macOS/Linux
netstat -ano | findstr :8000  # On Windows

# Either stop the conflicting service or change port in docker-compose.yml
```

### Permission Denied on /backups

**Issue**: Can't create backup directories

**Solution**:
```bash
# Fix permissions on backup volume
docker-compose exec backend chown -R root:root /backups
docker-compose exec backend chmod -R 755 /backups
```

## 📈 Next Steps

After successful deployment testing:

1. **Continue Development**: Proceed with implementing repositories and API endpoints
2. **Test with Real Devices**: Add actual network devices (in safe lab environment)
3. **Security Hardening**: Change default passwords, configure HTTPS
4. **Production Deployment**: Use production-grade secrets, separate networks
5. **Monitoring Setup**: Add Prometheus/Grafana for metrics
6. **Backup Strategy**: Implement database backup automation

## 📞 Getting Help

If you encounter issues:

1. Check logs: `docker-compose logs [service_name]`
2. Verify configuration: Review `.env` file
3. Rebuild images: `docker-compose build --no-cache`
4. Clean start: `docker-compose down -v && docker-compose up`

## ✨ What's Been Built

So far, this deployment includes:

### Infrastructure (100%)
- ✅ Docker Compose orchestration
- ✅ Multi-container networking
- ✅ PostgreSQL database
- ✅ Redis cache/message broker
- ✅ Nginx reverse proxy

### Backend Core (40%)
- ✅ FastAPI application skeleton
- ✅ Database models (multi-tenant)
- ✅ Pydantic schemas
- ✅ Alembic migrations
- ✅ Security (JWT, encryption)
- ✅ SSH device connector (Netmiko)
- ✅ Configuration storage service
- ✅ Device type configuration (9 OS types)
- ⏳ Repositories (pending)
- ⏳ API endpoints (pending)
- ⏳ Authentication system (pending)

### Task Queue (20%)
- ✅ Celery app configuration
- ✅ Celery Beat scheduler
- ✅ Flower monitoring
- ⏳ Backup tasks (pending implementation)

### Frontend (10%)
- ✅ React + TypeScript + Vite setup
- ✅ API connectivity test
- ⏳ Full UI (pending)

---

**Deployment Status**: 🟢 Foundation Ready for Testing

Continue with: Repository Pattern → FastAPI Endpoints → Authentication → Full API Implementation
