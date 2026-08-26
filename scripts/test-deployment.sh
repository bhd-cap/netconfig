#!/bin/bash

# Network Config Backup System - Deployment Test Script
# This script automates the deployment testing process

set -e  # Exit on error

echo "=========================================="
echo "Network Config Backup - Deployment Test"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is running
echo -n "Checking Docker... "
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}✗${NC}"
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi
echo -e "${GREEN}✓${NC}"

# Check if docker-compose is available
echo -n "Checking Docker Compose... "
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}✗${NC}"
    echo "Error: docker-compose not found. Please install docker-compose."
    exit 1
fi
echo -e "${GREEN}✓${NC}"

# Check if .env file exists
echo -n "Checking .env file... "
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠${NC}"
    echo "Warning: .env file not found. Using .env.example as template."
    cp .env.example .env
    echo "Please review and update .env file with your settings."
    echo -e "${YELLOW}Press Enter to continue or Ctrl+C to abort...${NC}"
    read
else
    echo -e "${GREEN}✓${NC}"
fi

echo ""
echo "=========================================="
echo "Step 1: Starting Services"
echo "=========================================="
echo ""

# Start services
echo "Starting Docker containers..."
docker-compose up -d

echo ""
echo "Waiting for services to be healthy (30 seconds)..."
sleep 30

echo ""
echo "=========================================="
echo "Step 2: Checking Service Health"
echo "=========================================="
echo ""

# Check each service
services=("postgres" "redis" "backend" "celery_worker" "celery_beat" "flower" "frontend" "nginx")

for service in "${services[@]}"; do
    echo -n "Checking $service... "
    if docker-compose ps | grep -q "$service.*Up"; then
        echo -e "${GREEN}✓${NC}"
    else
        echo -e "${RED}✗${NC}"
        echo "Service $service is not running!"
        docker-compose logs --tail=20 $service
    fi
done

echo ""
echo "=========================================="
echo "Step 3: Running Database Migrations"
echo "=========================================="
echo ""

# Run migrations
echo "Running Alembic migrations..."
docker-compose exec -T backend alembic upgrade head

echo ""
echo "=========================================="
echo "Step 4: Initializing Database"
echo "=========================================="
echo ""

# Initialize database
echo "Creating admin user and default organization..."
docker-compose exec -T backend python init_db.py

echo ""
echo "=========================================="
echo "Step 5: Testing Endpoints"
echo "=========================================="
echo ""

# Test backend
echo -n "Testing backend API... "
if curl -s http://localhost:8000/ | grep -q "Network Config Backup System"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo "Backend API is not responding correctly"
fi

# Test health endpoint
echo -n "Testing health endpoint... "
if curl -s http://localhost:8000/health | grep -q "healthy"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
fi

# Test frontend
echo -n "Testing frontend... "
if curl -s http://localhost/ | grep -q "html"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo "Frontend is not responding"
fi

echo ""
echo "=========================================="
echo "Step 6: Testing Core Services"
echo "=========================================="
echo ""

# Test PostgreSQL
echo -n "Testing PostgreSQL... "
if docker-compose exec -T postgres pg_isready -U netbackup | grep -q "accepting connections"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
fi

# Test Redis
echo -n "Testing Redis... "
if docker-compose exec -T redis redis-cli ping | grep -q "PONG"; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
fi

# Test encryption
echo -n "Testing encryption service... "
ENCRYPT_TEST=$(docker-compose exec -T backend python -c "
from app.utils.encryption import encryption_service
plain = 'test123'
encrypted = encryption_service.encrypt(plain)
decrypted = encryption_service.decrypt(encrypted)
print('OK' if plain == decrypted else 'FAIL')
" 2>/dev/null | tr -d '\r\n')

if [ "$ENCRYPT_TEST" = "OK" ]; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
fi

echo ""
echo "=========================================="
echo "✅ Deployment Test Complete!"
echo "=========================================="
echo ""
echo "Access your application at:"
echo ""
echo "  🌐 Frontend:       http://localhost"
echo "  📚 API Docs:       http://localhost:8000/docs"
echo "  🔍 API Health:     http://localhost:8000/health"
echo "  🌺 Flower (Tasks): http://localhost:5555"
echo ""
echo "Login credentials:"
echo "  Username: admin"
echo "  Password: changeme123"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT: Change the default password immediately!${NC}"
echo ""
echo "To view logs:      docker-compose logs -f [service_name]"
echo "To stop:           docker-compose down"
echo "To restart:        docker-compose restart"
echo ""
echo "For detailed testing guide, see DEPLOYMENT_TEST.md"
echo ""
