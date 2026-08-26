#!/usr/bin/env bash
#
# Network Config Backup System - one line installer
#
#   curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/install.sh | bash
#
# What it does:
#   1. checks Docker and Compose v2 are usable
#   2. fetches the repository (or uses the current checkout)
#   3. writes .env with freshly generated secrets, keeping any existing one
#   4. builds the images and starts the stack
#   5. waits for the API to actually report healthy
#   6. runs migrations and creates the admin user
#
# Admin login after install:  admin / changeme
#
# Useful environment variables:
#   INSTALL_DIR=/opt/netconfig   where to install (default ./netconfig)
#   ADMIN_PASSWORD=...           admin password (default "changeme")
#   ADMIN_USERNAME=...           admin username (default "admin")
#   FRONTEND_PORT / BACKEND_PORT published ports (default 3000 / 8000)
#   WITH_MONITORING=true         also start Flower on FLOWER_PORT
#   BRANCH=main                  branch to clone
#   SKIP_BUILD=true              reuse existing images

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/bhd-cap/netconfig.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-changeme}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FLOWER_PORT="${FLOWER_PORT:-5555}"
WITH_MONITORING="${WITH_MONITORING:-false}"
SKIP_BUILD="${SKIP_BUILD:-false}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"

if [ -t 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

STEP=0
TOTAL_STEPS=6

step()  { STEP=$((STEP + 1)); printf '\n%s[%d/%d]%s %s\n' "$BLUE" "$STEP" "$TOTAL_STEPS" "$NC" "$1"; }
info()  { printf '      %s\n' "$1"; }
ok()    { printf '      %s+%s %s\n' "$GREEN" "$NC" "$1"; }
warn()  { printf '      %s!%s %s\n' "$YELLOW" "$NC" "$1"; }
die()   { printf '\n%serror:%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

on_error() {
    local line=$1
    printf '\n%sinstall failed%s (line %s)\n' "$RED" "$NC" "$line" >&2
    if [ -n "${COMPOSE_READY:-}" ]; then
        printf 'Recent service logs:\n' >&2
        $COMPOSE logs --tail 40 2>&1 | tail -60 >&2 || true
        printf '\nInspect with: cd %s && %s logs -f\n' "${INSTALL_DIR:-.}" "$COMPOSE" >&2
    fi
}
trap 'on_error $LINENO' ERR

banner() {
    printf '%s' "$BOLD"
    cat <<'ART'
  ______  _______ _______ ______  _______ _______ _     _ _____ _____
  |     | |______    |    |     | |_____| |       |____/    |     |
  |_____| |______    |    |_____| |     | |_____  |    \_ __|__ __|__

  Network Config Backup System - installer
ART
    printf '%s\n' "$NC"
}

# --------------------------------------------------------------------------
# 1. prerequisites
# --------------------------------------------------------------------------
check_prerequisites() {
    step "Checking prerequisites"

    command -v docker >/dev/null 2>&1 || die \
"Docker is not installed. Install Docker Engine or Docker Desktop first:
       https://docs.docker.com/get-docker/"

    if ! docker info >/dev/null 2>&1; then
        die \
"Docker is installed but the daemon is not reachable.
       Start Docker Desktop, or on Linux: sudo systemctl start docker
       If you are in WSL, enable integration for this distro in
       Docker Desktop -> Settings -> Resources -> WSL Integration."
    fi
    ok "Docker daemon is running"

    # Compose v2 preferred; the v1 python script is missing features this
    # stack relies on (profiles, per-service resource limits).
    if docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
        warn "Using legacy docker-compose v1; v2 is recommended"
    else
        die \
"Docker Compose is not available. Install the Compose plugin:
       https://docs.docker.com/compose/install/"
    fi
    ok "Compose available: $($COMPOSE version --short 2>/dev/null || echo present)"

    command -v git >/dev/null 2>&1 || GIT_MISSING=1

    for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
        if port_in_use "$port"; then
            warn "Port $port is already in use; set FRONTEND_PORT/BACKEND_PORT to change"
        fi
    done
}

port_in_use() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
    elif command -v lsof >/dev/null 2>&1; then
        lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    else
        return 1
    fi
}

# --------------------------------------------------------------------------
# 2. source
# --------------------------------------------------------------------------
obtain_source() {
    step "Locating application source"

    # Running from inside a checkout (git clone, or piped from a local file).
    if [ -f "docker-compose.yml" ] && [ -d "backend" ] && [ -d "frontend" ]; then
        INSTALL_DIR="$(pwd)"
        ok "Using the current directory: $INSTALL_DIR"
        return
    fi

    INSTALL_DIR="${INSTALL_DIR:-$(pwd)/netconfig}"

    if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
        ok "Existing installation found at $INSTALL_DIR"
        cd "$INSTALL_DIR"
        if [ -z "${GIT_MISSING:-}" ] && [ -d .git ]; then
            info "Updating to latest $BRANCH..."
            git fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1 || warn "Could not fetch updates; continuing with local copy"
            git checkout -q "$BRANCH" 2>/dev/null || true
            git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1 || warn "Local changes present; not updating"
        fi
        return
    fi

    [ -z "${GIT_MISSING:-}" ] || die \
"git is required to download the application. Install git, or clone the
       repository yourself and run install.sh from inside it."

    info "Cloning $REPO_URL ($BRANCH) into $INSTALL_DIR..."
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" >/dev/null 2>&1 \
        || die "Failed to clone $REPO_URL. Check the URL, the branch, and network access."
    cd "$INSTALL_DIR"
    ok "Cloned into $INSTALL_DIR"
}

# --------------------------------------------------------------------------
# 3. configuration
# --------------------------------------------------------------------------
random_hex() {
    # 32 bytes of hex, from whatever source is available.
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
    fi
}

random_fernet_key() {
    # Fernet requires exactly 32 raw bytes, urlsafe-base64 encoded.
    local raw
    if command -v openssl >/dev/null 2>&1; then
        raw=$(openssl rand -base64 32)
    elif command -v base64 >/dev/null 2>&1; then
        raw=$(head -c 32 /dev/urandom | base64)
    else
        die "Need openssl or base64 to generate an encryption key"
    fi
    # translate to the urlsafe alphabet and strip line breaks
    printf '%s' "$raw" | tr -d '\n' | tr '+/' '-_'
}

random_password() {
    random_hex | cut -c1-24
}

write_env() {
    step "Writing configuration"

    if [ -f .env ]; then
        ok ".env already exists; keeping existing secrets"

        # Make sure the values the installer needs are present even in an
        # older .env, without touching anything already set.
        ensure_env ADMIN_USERNAME "$ADMIN_USERNAME"
        ensure_env ADMIN_PASSWORD "$ADMIN_PASSWORD"
        ensure_env ADMIN_EMAIL "$ADMIN_EMAIL"
        ensure_env FRONTEND_PORT "$FRONTEND_PORT"
        ensure_env BACKEND_PORT "$BACKEND_PORT"
        return
    fi

    local secret_key encryption_key db_password
    secret_key=$(random_hex)
    encryption_key=$(random_fernet_key)
    db_password=$(random_password)

    umask 077
    cat > .env <<EOF
# Generated by install.sh on $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# Secrets below are unique to this installation. Keep this file out of
# version control and back up ENCRYPTION_KEY: it decrypts stored device
# credentials, and losing it makes every saved password unrecoverable.

# Database
POSTGRES_DB=netbackup
POSTGRES_USER=netbackup
POSTGRES_PASSWORD=${db_password}
DATABASE_URL=postgresql://netbackup:${db_password}@postgres:5432/netbackup

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Security
SECRET_KEY=${secret_key}
JWT_SECRET_KEY=${secret_key}
ENCRYPTION_KEY=${encryption_key}
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Application
DEBUG=false
LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
CORS_ORIGINS=http://localhost:${FRONTEND_PORT},http://localhost
VITE_API_URL=http://localhost:${BACKEND_PORT}/api/v1

# Backups
BACKUP_BASE_PATH=/backups
MAX_CONCURRENT_BACKUPS=10
DEFAULT_RETENTION_DAYS=90
DEFAULT_SSH_TIMEOUT=60
BACKUP_DEDUPLICATE=true

# Ports
FRONTEND_PORT=${FRONTEND_PORT}
BACKEND_PORT=${BACKEND_PORT}
FLOWER_PORT=${FLOWER_PORT}

# Process counts
WEB_CONCURRENCY=2
CELERY_CONCURRENCY=2

# Admin user created during initialization
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ADMIN_ORG_NAME=Default Organization
EOF
    umask 022

    ok "Generated .env with a random database password, JWT secret and Fernet key"
}

ensure_env() {
    local key=$1 value=$2
    grep -qE "^${key}=" .env || printf '%s=%s\n' "$key" "$value" >> .env
}

# --------------------------------------------------------------------------
# 4. build and start
# --------------------------------------------------------------------------
start_stack() {
    step "Building images"

    # A plain string rather than an array: expanding an empty array under
    # `set -u` is an error in bash 3.2, which macOS still ships as /bin/bash.
    local profiles=""
    if [ "$WITH_MONITORING" = "true" ]; then
        profiles="--profile monitoring"
    fi

    if [ "$SKIP_BUILD" = "true" ]; then
        ok "SKIP_BUILD set; using existing images"
    else
        info "This takes a few minutes on a first run..."
        # shellcheck disable=SC2086 # deliberate word splitting of both vars
        $COMPOSE $profiles build || die "Image build failed (see output above)"
        ok "Images built"
    fi

    COMPOSE_READY=1
    step "Starting services"
    # shellcheck disable=SC2086
    $COMPOSE $profiles up -d || die "Failed to start services"
    ok "Containers started"
}

# --------------------------------------------------------------------------
# 5. wait for readiness
# --------------------------------------------------------------------------
wait_for_api() {
    step "Waiting for the API to become healthy"

    # Poll the readiness endpoint rather than sleeping for a fixed period: the
    # backend runs database migrations at startup, which takes an unknown
    # amount of time.
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    local url="http://localhost:${BACKEND_PORT}/api/v1/health"
    local body=""

    while [ $SECONDS -lt $deadline ]; do
        if body=$(fetch "$url"); then
            case "$body" in
                *'"status":"healthy"'*)
                    ok "API reports healthy"
                    return 0
                    ;;
                *'"status":"degraded"'*)
                    info "API is up, dependencies still starting..."
                    ;;
            esac
        fi
        sleep 3
        printf '.'
    done

    printf '\n'
    warn "API did not report healthy within ${HEALTH_TIMEOUT}s"
    [ -n "$body" ] && info "last response: $body"
    warn "Continuing anyway; check '$COMPOSE logs backend' if the UI does not load"
    return 0
}

fetch() {
    local url=$1
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 5 "$url" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- --timeout=5 "$url" 2>/dev/null
    else
        # Fall back to asking the backend container itself.
        $COMPOSE exec -T backend python -c \
            "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=5).read().decode())" \
            2>/dev/null
    fi
}

# --------------------------------------------------------------------------
# 6. initialize
# --------------------------------------------------------------------------
initialize_database() {
    step "Initializing database and admin user"

    # The backend container applies migrations on startup; run it again here so
    # a SKIP_BUILD/restart path is still correct, and because it is a no-op
    # when the schema is current.
    if ! $COMPOSE exec -T backend alembic upgrade head >/tmp/netconfig-migrate.log 2>&1; then
        cat /tmp/netconfig-migrate.log >&2
        die "Database migration failed"
    fi
    ok "Schema is up to date"

    if ! $COMPOSE exec -T backend python init_db.py --reset-admin-password; then
        die "Failed to create the admin user"
    fi
}

# --------------------------------------------------------------------------
summary() {
    local host="localhost"
    printf '\n%s' "$GREEN"
    printf '=======================================================================\n'
    printf '  Installation complete\n'
    printf '=======================================================================%s\n\n' "$NC"

    printf '  %sWeb UI%s        http://%s:%s\n' "$BOLD" "$NC" "$host" "$FRONTEND_PORT"
    printf '  %sAPI docs%s      http://%s:%s/docs\n' "$BOLD" "$NC" "$host" "$BACKEND_PORT"
    if [ "$WITH_MONITORING" = "true" ]; then
        printf '  %sTask monitor%s  http://%s:%s\n' "$BOLD" "$NC" "$host" "$FLOWER_PORT"
    fi

    printf '\n  %sSign in with%s\n' "$BOLD" "$NC"
    printf '    username  %s\n' "$ADMIN_USERNAME"
    printf '    password  %s\n' "$ADMIN_PASSWORD"
    printf '\n  %s! This is a temporary password. Change it after first login.%s\n' "$YELLOW" "$NC"
    printf '  %s! Back up %s/.env - ENCRYPTION_KEY decrypts stored device%s\n' "$YELLOW" "$INSTALL_DIR" "$NC"
    printf '  %s  credentials and cannot be recovered if lost.%s\n' "$YELLOW" "$NC"

    printf '\n  %sManaging the stack%s (from %s)\n' "$BOLD" "$NC" "$INSTALL_DIR"
    printf '    %s ps                 service status\n' "$COMPOSE"
    printf '    %s logs -f            follow logs\n' "$COMPOSE"
    printf '    %s restart backend    restart a service\n' "$COMPOSE"
    printf '    %s down               stop everything\n' "$COMPOSE"
    printf '\n'
}

main() {
    banner
    check_prerequisites
    obtain_source
    write_env

    # Re-read the admin values from .env so the summary matches what was
    # actually configured (an existing .env wins over the defaults here).
    if [ -f .env ]; then
        ADMIN_USERNAME=$(env_value ADMIN_USERNAME "$ADMIN_USERNAME")
        ADMIN_PASSWORD=$(env_value ADMIN_PASSWORD "$ADMIN_PASSWORD")
        FRONTEND_PORT=$(env_value FRONTEND_PORT "$FRONTEND_PORT")
        BACKEND_PORT=$(env_value BACKEND_PORT "$BACKEND_PORT")
        FLOWER_PORT=$(env_value FLOWER_PORT "$FLOWER_PORT")
    fi

    start_stack
    wait_for_api
    initialize_database
    summary
}

env_value() {
    local key=$1 fallback=$2 line
    line=$(grep -E "^${key}=" .env | tail -1 || true)
    if [ -n "$line" ]; then
        printf '%s' "${line#*=}"
    else
        printf '%s' "$fallback"
    fi
}

main "$@"
