#!/bin/bash

# Network Configuration Backup System - Deployment Script
# Run this script manually after Docker Desktop is running

set -e  # Exit on error

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║        Network Config Backup System - Deployment                     ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check Docker
echo -e "${BLUE}[1/7]${NC} Checking Docker..."
if ! docker ps &> /dev/null; then
    echo -e "${RED}✗ Docker is not accessible${NC}"
    echo ""
    echo "Please ensure Docker Desktop is running on Windows and:"
    echo "  1. Open Docker Desktop Settings"
    echo "  2. Go to Resources → WSL Integration"
    echo "  3. Enable integration for your WSL distro"
    echo "  4. Apply & Restart"
    echo ""
    exit 1
fi
echo -e "${GREEN}✓ Docker is accessible${NC}"
echo ""

# Navigate to project directory
cd "$(dirname "$0")"
echo -e "${BLUE}[2/7]${NC} Project directory: $(pwd)"
echo ""

# Create frontend .env if it doesn't exist
echo -e "${BLUE}[3/7]${NC} Creating frontend .env..."
if [ ! -f "frontend/.env" ]; then
    echo "VITE_API_URL=http://localhost:8000/api/v1" > frontend/.env
    echo -e "${GREEN}✓ Created frontend/.env${NC}"
else
    echo -e "${YELLOW}✓ frontend/.env already exists${NC}"
fi
echo ""

# Build Docker images
echo -e "${BLUE}[4/7]${NC} Building Docker images (this may take 5-10 minutes)..."
docker-compose build
echo -e "${GREEN}✓ Build complete${NC}"
echo ""

# Start services
echo -e "${BLUE}[5/7]${NC} Starting services..."
docker-compose up -d
echo -e "${GREEN}✓ Services started${NC}"
echo ""

# Wait for services to be ready
echo -e "${BLUE}[6/7]${NC} Waiting for services to be ready (60 seconds)..."
for i in {60..1}; do
    printf "\r   ⏳ ${i} seconds remaining...  "
    sleep 1
done
echo ""
echo -e "${GREEN}✓ Services should be ready${NC}"
echo ""

# Initialize database
echo -e "${BLUE}[7/7]${NC} Initializing database..."
echo "   Running migrations..."
docker-compose exec -T backend alembic upgrade head

echo "   Creating admin user..."
docker-compose exec -T backend python init_db.py
echo -e "${GREEN}✓ Database initialized${NC}"
echo ""

# Check service status
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Service Status:"
docker-compose ps
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}🌐 Access the Application:${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "   Frontend:      ${BLUE}http://localhost:3000${NC}"
echo -e "   API Docs:      ${BLUE}http://localhost:8000/docs${NC}"
echo -e "   Task Monitor:  ${BLUE}http://localhost:5555${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}🔐 Login Credentials:${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   Username: admin"
echo "   Password: admin123"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${YELLOW}📝 Useful Commands:${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "   View logs:       docker-compose logs -f"
echo "   Stop services:   docker-compose down"
echo "   Restart:         docker-compose restart"
echo "   Check status:    docker-compose ps"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "${GREEN}🎉 Ready to use! Open http://localhost:3000 in your browser${NC}"
echo ""
