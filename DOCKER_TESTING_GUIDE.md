# Docker Deployment Testing Guide

## Prerequisites

Before starting, ensure you have:
- Docker Desktop installed (or Docker Engine + Docker Compose)
- At least 4GB RAM available for Docker
- Ports available: 8000 (backend), 3000 (frontend), 5432 (postgres), 6379 (redis), 5555 (flower)

---

## Quick Start

### 1. Check Docker Installation

```bash
# Check Docker is installed and running
docker --version
docker-compose --version

# Check Docker daemon is running
docker ps
```

**Expected output:**
```
Docker version 24.x.x
Docker Compose version v2.x.x
```

If Docker is not installed, see [Installation Options](#docker-installation) below.

---

### 2. Create Environment Files

The `.env` file already exists with generated keys. Verify it:

```bash
# Check backend .env
cat backend/.env

# Create frontend .env if not exists
cat > frontend/.env <<EOF
VITE_API_URL=http://localhost:8000/api/v1
EOF
```

---

### 3. Build and Start Services

```bash
# From project root
cd /home/jcaparoso/projects/code/config-backup

# Build all services (first time only - takes 5-10 min)
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f
```

**Services Starting:**
1. PostgreSQL database
2. Redis (message broker)
3. Backend API (FastAPI)
4. Celery Worker (task processing)
5. Celery Beat (scheduler)
6. Flower (task monitor)
7. Frontend (React)
8. Nginx (reverse proxy)

---

### 4. Initialize Database

```bash
# Wait 30 seconds for services to start, then initialize
sleep 30

# Run database migrations
docker-compose exec backend alembic upgrade head

# Create default admin user
docker-compose exec backend python init_db.py
```

**Look for output like:**
```
✓ Organization 'Default Organization' created
✓ Admin user 'admin' created
✓ Default credentials:
  Username: admin
  Password: admin123
```

**Save these credentials!**

---

### 5. Verify Services

Check all services are healthy:

```bash
# Check service status
docker-compose ps

# Should show 8 services running
```

**Expected output:**
```
NAME                                 STATUS
config-backup-postgres-1            Up (healthy)
config-backup-redis-1               Up (healthy)
config-backup-backend-1             Up (healthy)
config-backup-celery_worker-1       Up
config-backup-celery_beat-1         Up
config-backup-flower-1              Up
config-backup-frontend-1            Up
config-backup-nginx-1               Up
```

---

### 6. Test Endpoints

#### Backend API

```bash
# Test root endpoint
curl http://localhost:8000/

# Test health check
curl http://localhost:8000/api/v1/health

# Test API documentation
open http://localhost:8000/docs  # macOS
xdg-open http://localhost:8000/docs  # Linux
start http://localhost:8000/docs  # Windows
```

**Expected response:**
```json
{
  "app": "Network Config Backup",
  "version": "1.0.0",
  "status": "running"
}
```

#### Frontend

```bash
# Open frontend in browser
open http://localhost:3000  # macOS
xdg-open http://localhost:3000  # Linux
start http://localhost:3000  # Windows
```

You should see the **Login page**.

---

## Testing the Application

### 1. User Registration & Login

#### Option A: Use Existing Admin Account

1. Go to http://localhost:3000
2. Click **"Sign up"** link
3. Or use existing credentials:
   - Username: `admin`
   - Password: `admin123`

#### Option B: Create New Account

1. Click **"Sign up"**
2. Fill in the form:
   - Username: `testuser`
   - Email: `test@example.com`
   - Organization: `Test Company`
   - Password: `test123456`
3. Click **"Create account"**
4. You'll be automatically logged in!

---

### 2. Explore the Dashboard

After login, you should see:

✅ **Dashboard Statistics:**
- Total Devices: 0
- Total Backups: 0
- Scheduled Jobs: 0
- Storage: 0 GB

✅ **Navigation Sidebar:**
- Dashboard
- Devices
- Backups
- Scheduled Jobs (if admin)
- Compare

✅ **User Profile:**
- Your username
- Email
- Logout button

---

### 3. Test API via Swagger UI

1. Open http://localhost:8000/docs
2. Click **"Authorize"** button
3. Login to get token:
   - Go to `/api/v1/auth/login`
   - Click **"Try it out"**
   - Enter credentials (admin/admin123)
   - Click **"Execute"**
   - Copy the `access_token` from response
4. Paste token in Authorization dialog
5. Now you can test all endpoints!

**Try these:**
- `GET /api/v1/devices` - List devices (empty initially)
- `GET /api/v1/backups` - List backups
- `GET /api/v1/statistics/dashboard` - Dashboard stats

---

### 4. Test Celery Task Queue

1. Open Flower (task monitor): http://localhost:5555
2. You should see:
   - 1 worker online
   - 0 active tasks
   - Task list (empty)

---

### 5. Add a Test Device (via Swagger)

1. Go to http://localhost:8000/docs
2. Find `POST /api/v1/devices`
3. Click **"Try it out"**
4. Use this test data:

```json
{
  "hostname": "test-router-01",
  "ip_address": "192.168.1.1",
  "device_type": "cisco_ios",
  "username": "admin",
  "password": "cisco123",
  "port": 22,
  "tags": {
    "location": "datacenter-1",
    "role": "core"
  }
}
```

5. Click **"Execute"**
6. You should get a 201 response with device details
7. Refresh frontend dashboard - should show "1 Device"!

---

### 6. Test Backup Trigger

**Note:** This will fail to connect (no real device), but tests the workflow:

1. In Swagger, find `POST /api/v1/backups/trigger`
2. Enter device ID from previous step:

```json
{
  "device_ids": [1]
}
```

3. Click **"Execute"**
4. Copy the `task_id` from response
5. Check task status: `GET /api/v1/backups/tasks/{task_id}`
6. View in Flower: http://localhost:5555

---

## Monitoring & Logs

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f frontend
docker-compose logs -f celery_worker

# Last 100 lines
docker-compose logs --tail=100 backend
```

### Check Service Health

```bash
# Service status
docker-compose ps

# Resource usage
docker stats

# Inspect specific service
docker-compose exec backend python -c "from app.core.database import engine; print(engine)"
```

### Database Access

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U netbackup -d netconfig_backup

# List tables
\dt

# Query users
SELECT id, username, email FROM users;

# Query devices
SELECT id, hostname, ip_address FROM devices;

# Exit
\q
```

### Redis Access

```bash
# Connect to Redis
docker-compose exec redis redis-cli

# Check keys
KEYS *

# Check Celery queue
LLEN celery

# Exit
exit
```

---

## Common Issues & Solutions

### Issue 1: Port Already in Use

**Error:** `Bind for 0.0.0.0:8000 failed: port is already allocated`

**Solution:**
```bash
# Find process using port
sudo lsof -i :8000  # Linux/Mac
netstat -ano | findstr :8000  # Windows

# Kill process or change port in docker-compose.yml
```

### Issue 2: Services Not Healthy

**Error:** `unhealthy` status for postgres/redis

**Solution:**
```bash
# Wait longer (up to 60 seconds)
docker-compose ps

# Check logs
docker-compose logs postgres
docker-compose logs redis

# Restart services
docker-compose restart postgres redis
```

### Issue 3: Frontend Shows "Failed to Load"

**Error:** Dashboard shows error loading stats

**Solution:**
```bash
# Check backend is running
curl http://localhost:8000/api/v1/health

# Check CORS settings in backend/.env
# Should include http://localhost:3000

# Restart backend
docker-compose restart backend
```

### Issue 4: Database Connection Error

**Error:** `could not connect to server: Connection refused`

**Solution:**
```bash
# Check database is running
docker-compose ps postgres

# Check logs
docker-compose logs postgres

# Recreate database
docker-compose down
docker volume rm config-backup_postgres_data
docker-compose up -d
docker-compose exec backend alembic upgrade head
docker-compose exec backend python init_db.py
```

### Issue 5: Build Errors

**Error:** `failed to solve with frontend dockerfile`

**Solution:**
```bash
# Clean build
docker-compose down
docker system prune -a

# Rebuild without cache
docker-compose build --no-cache

# Start
docker-compose up -d
```

---

## Stopping & Cleaning Up

### Stop Services

```bash
# Stop all services (keeps data)
docker-compose stop

# Stop and remove containers (keeps data)
docker-compose down

# Stop and remove everything including volumes (DELETES DATA!)
docker-compose down -v
```

### Clean Up Docker

```bash
# Remove unused images
docker image prune -a

# Remove unused volumes
docker volume prune

# Complete cleanup (WARNING: removes all Docker data)
docker system prune -a --volumes
```

---

## Docker Installation

If Docker is not installed, choose one option:

### Option 1: Docker Desktop (Recommended - Easy)

**Advantages:**
- GUI interface
- Built-in WSL2 support (Windows)
- Easy to manage

**Download:**
- **Windows:** https://docs.docker.com/desktop/install/windows-install/
- **Mac:** https://docs.docker.com/desktop/install/mac-install/
- **Linux:** https://docs.docker.com/desktop/install/linux-install/

**After Install:**
1. Start Docker Desktop
2. Wait for "Docker Desktop is running"
3. Test: `docker ps`

### Option 2: Docker Engine (Linux)

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install docker.io docker-compose-plugin

# Start Docker
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Test
docker ps
```

### Option 3: Docker in WSL2 (Windows)

**If you're using WSL2 (recommended for Windows):**

```bash
# Inside WSL2
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Start Docker
sudo service docker start

# Or use Docker Desktop with WSL2 integration
```

---

## Performance Tips

### Speed Up Builds

```bash
# Use BuildKit
export DOCKER_BUILDKIT=1

# Parallel builds
docker-compose build --parallel
```

### Reduce Resource Usage

Edit `docker-compose.yml` to add resource limits:

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

### Use Local Development (Faster)

For development, you can run services outside Docker:

```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev

# Keep only databases in Docker
docker-compose up -d postgres redis
```

---

## Success Checklist

✅ Docker is installed and running
✅ All 8 services are up and healthy
✅ Database is initialized with admin user
✅ Backend API responds at http://localhost:8000
✅ Frontend loads at http://localhost:3000
✅ Can login with admin credentials
✅ Dashboard shows statistics
✅ Can navigate between pages
✅ Swagger UI works at /docs
✅ Flower shows Celery worker

---

## Next Steps After Testing

If everything works:

1. **Continue Development:**
   - Add more devices via API
   - Test backup functionality
   - Create scheduled jobs
   - Explore all endpoints

2. **Build Remaining UI:**
   - Device management page
   - Backup management page
   - Job scheduler page
   - Configuration comparison page

3. **Production Deployment:**
   - Update environment variables
   - Set up SSL/TLS
   - Configure firewall
   - Set up backups
   - Monitor logs

---

## Useful Commands Reference

```bash
# Service Management
docker-compose up -d              # Start all services
docker-compose down               # Stop and remove containers
docker-compose restart backend    # Restart specific service
docker-compose ps                 # Show service status
docker-compose logs -f            # Follow logs

# Database
docker-compose exec postgres psql -U netbackup -d netconfig_backup
docker-compose exec backend alembic upgrade head
docker-compose exec backend python init_db.py

# Debugging
docker-compose exec backend bash  # Enter backend container
docker-compose exec frontend sh   # Enter frontend container
docker-compose logs --tail=100 backend

# Cleanup
docker-compose down -v            # Remove everything
docker system prune -a --volumes  # Clean Docker
```

---

**Ready to test? Let's start!**

Run these commands:

```bash
cd /home/jcaparoso/projects/code/config-backup
docker-compose build
docker-compose up -d
docker-compose logs -f
```

Then open http://localhost:3000 in your browser!

---

**Version:** 1.0.0
**Last Updated:** 2025-01-31
