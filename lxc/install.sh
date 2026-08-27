#!/usr/bin/env bash
#
# Network Config Backup System - LXC / bare metal installer
#
# Installs the application natively under systemd, with no Docker: PostgreSQL,
# Redis, nginx, the FastAPI service, a Celery worker and Celery beat all run as
# ordinary system services.
#
# Run inside a Debian 11/12 or Ubuntu 22.04/24.04 container (or on any such
# host):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/install.sh)"
#
# or, from a checkout:
#
#   sudo ./lxc/install.sh
#
# Admin login after install:  admin / changeme
#
# Layout:
#   /opt/netconfig                application source and virtualenv
#   /etc/netconfig-backup/        configuration and secrets (0640)
#   /var/lib/netconfig/backups    stored device configurations
#   journalctl -u netconfig-api   logs
#
# Useful environment variables:
#   ADMIN_USERNAME / ADMIN_PASSWORD   initial admin account (admin / changeme)
#   HTTP_PORT=80                      port nginx listens on
#   APP_DIR=/opt/netconfig            install location
#   DATA_DIR=/var/lib/netconfig       backup storage location
#   WEB_CONCURRENCY=2                 uvicorn worker processes
#   CELERY_CONCURRENCY=2              Celery worker processes
#   SKIP_FRONTEND=true                skip building the web UI (API only)
#   BRANCH=main                       branch to clone

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/bhd-cap/netconfig.git}"
BRANCH="${BRANCH:-main}"

APP_DIR="${APP_DIR:-/opt/netconfig}"
DATA_DIR="${DATA_DIR:-/var/lib/netconfig}"
# Deliberately not /etc/netconfig: that path is a regular file on every
# Debian and Ubuntu system, shipped by libtirpc-common as the RPC network
# configuration database (see netconfig(5)). Creating a directory there fails.
CONFIG_DIR="${CONFIG_DIR:-/etc/netconfig-backup}"
CONFIG_FILE="$CONFIG_DIR/netconfig.env"
SERVICE_USER="${SERVICE_USER:-netconfig}"

DB_NAME="${DB_NAME:-netconfig}"
DB_USER="${DB_USER:-netconfig}"

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-changeme}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"

HTTP_PORT="${HTTP_PORT:-80}"
API_PORT="${API_PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-2}"
MAX_CONCURRENT_BACKUPS="${MAX_CONCURRENT_BACKUPS:-10}"

SKIP_FRONTEND="${SKIP_FRONTEND:-false}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

if [ -t 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

STEP=0
TOTAL_STEPS=11

step()  { STEP=$((STEP + 1)); printf '\n%s[%d/%d]%s %s\n' "$BLUE" "$STEP" "$TOTAL_STEPS" "$NC" "$1"; }
info()  { printf '      %s\n' "$1"; }
ok()    { printf '      %s+%s %s\n' "$GREEN" "$NC" "$1"; }
warn()  { printf '      %s!%s %s\n' "$YELLOW" "$NC" "$1"; }
die()   { printf '\n%serror:%s %s\n' "$RED" "$NC" "$1" >&2; exit 1; }

# Run a long command with a time limit, a dot every 10s so it is visibly
# alive, and its log dumped on failure. Several of these steps take minutes;
# with no output at all they are indistinguishable from a hang.
# Returns the command's exit status (124 on timeout) instead of aborting, for
# callers that have a fallback.
try_with_progress() {
    local limit=$1 log=$2; shift 2
    local rc=0 dots=""

    if [ -t 1 ]; then
        ( while true; do sleep 10; printf '.'; done ) &
        dots=$!
    fi

    timeout --foreground "$limit" "$@" >"$log" 2>&1 || rc=$?

    if [ -n "$dots" ]; then
        kill "$dots" 2>/dev/null || true
        wait "$dots" 2>/dev/null || true
        printf '\n'
    fi

    return "$rc"
}

with_progress() {
    local limit=$1 log=$2 desc=$3; shift 3
    local rc=0

    try_with_progress "$limit" "$log" "$@" || rc=$?

    if [ "$rc" -eq 124 ]; then
        tail -20 "$log" >&2 || true
        die "$desc timed out after ${limit}s (full log: $log)"
    fi

    if [ "$rc" -ne 0 ]; then
        tail -20 "$log" >&2 || true
        die "$desc failed (full log: $log)"
    fi

    return 0
}

on_error() {
    printf '\n%sinstall failed%s (line %s)\n' "$RED" "$NC" "$1" >&2
    if [ -n "${SERVICES_STARTED:-}" ]; then
        printf '\nRecent service logs:\n' >&2
        journalctl -u netconfig-api -u netconfig-worker --no-pager -n 30 2>/dev/null >&2 || true
    fi
    printf '\nInspect with: journalctl -u netconfig-api -f\n' >&2
}
trap 'on_error $LINENO' ERR

banner() {
    printf '%s' "$BOLD"
    cat <<'ART'
  ______  _______ _______ ______  _______ _______ _     _ _____ _____
  |     | |______    |    |     | |_____| |       |____/    |     |
  |_____| |______    |    |_____| |     | |_____  |    \_ __|__ __|__

  Network Config Backup System - LXC installer (no Docker)
ART
    printf '%s\n' "$NC"
}

# --------------------------------------------------------------------------
# 1. preflight
# --------------------------------------------------------------------------
preflight() {
    step "Checking the environment"

    [ "$(id -u)" -eq 0 ] || die "This installer must run as root (try: sudo $0)"

    [ -r /etc/os-release ] || die "Cannot read /etc/os-release; unsupported system"
    # shellcheck disable=SC1091
    . /etc/os-release

    case "${ID:-}" in
        debian|ubuntu) ;;
        *)
            case "${ID_LIKE:-}" in
                *debian*) warn "Untested distribution '$ID'; continuing as Debian-like" ;;
                *) die "Only Debian and Ubuntu are supported (found '${ID:-unknown}')" ;;
            esac
            ;;
    esac
    ok "Distribution: ${PRETTY_NAME:-$ID}"

    [ -d /run/systemd/system ] || die \
"systemd is not running as PID 1.
       In Proxmox this means the container was created without systemd, or you
       are inside a Docker container - use the Docker install (./install.sh)
       there instead."
    ok "systemd is available"

    if [ -f /run/systemd/container ]; then
        ok "Running inside a container: $(cat /run/systemd/container)"
    fi

    command -v systemctl >/dev/null 2>&1 || die "systemctl not found"
    command -v timeout >/dev/null 2>&1 || die "timeout not found (install coreutils)"
}

# --------------------------------------------------------------------------
# 2. packages
# --------------------------------------------------------------------------
install_packages() {
    step "Installing system packages"

    export DEBIAN_FRONTEND=noninteractive

    info "Updating package lists..."
    with_progress 600 /tmp/netconfig-apt-update.log "apt-get update" \
        apt-get update -qq

    local packages=(
        ca-certificates curl git
        postgresql postgresql-client
        redis-server
        nginx
        python3 python3-venv python3-pip
        sudo
    )

    info "Installing PostgreSQL, Redis, nginx and Python (a few minutes)..."
    with_progress 1800 /tmp/netconfig-apt-install.log "Package installation" \
        apt-get install -y -qq --no-install-recommends "${packages[@]}"
    ok "Base packages installed"

    if [ "$SKIP_FRONTEND" != "true" ]; then
        ensure_nodejs
    fi
}

ensure_nodejs() {
    # Vite 5 needs Node 18+. Debian 12 and Ubuntu 24.04 both ship a new enough
    # nodejs, so only reach for NodeSource when the distro's is too old.
    local major=0
    if command -v node >/dev/null 2>&1; then
        major=$(node --version | sed 's/^v\([0-9]*\).*/\1/')
    fi

    if [ "$major" -ge 18 ] 2>/dev/null; then
        ok "Node.js $(node --version) is new enough to build the UI"
        if ! command -v npm >/dev/null 2>&1; then
            apt-get install -y -qq --no-install-recommends npm >/dev/null
        fi
        return
    fi

    info "Installing Node.js from the distribution..."
    if apt-get install -y -qq --no-install-recommends nodejs npm >/dev/null 2>&1; then
        major=$(node --version 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || echo 0)
    fi

    if [ "$major" -ge 18 ] 2>/dev/null; then
        ok "Node.js $(node --version) installed"
        return
    fi

    info "Distribution Node.js is too old; installing Node.js 20 from NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1 \
        || die "Failed to add the NodeSource repository. Set SKIP_FRONTEND=true to install the API only."
    apt-get install -y -qq nodejs >/dev/null \
        || die "Failed to install Node.js 20"
    ok "Node.js $(node --version) installed"
}

# --------------------------------------------------------------------------
# 3. source
# --------------------------------------------------------------------------
obtain_source() {
    step "Installing application source into $APP_DIR"

    # ${BASH_SOURCE[0]} is unset when this script is run as
    # bash -c "$(curl ...)", and under 'set -u' referencing it aborts the
    # subshell, so it needs a default.
    local self="${BASH_SOURCE[0]:-}"
    local here=""
    if [ -n "$self" ]; then
        here="$(cd "$(dirname "$self")/.." 2>/dev/null && pwd || true)"
    fi

    if [ -n "$here" ] && [ -f "$here/docker-compose.yml" ] && [ -d "$here/backend" ]; then
        if [ "$here" = "$APP_DIR" ]; then
            ok "Already installed at $APP_DIR"
        else
            info "Copying the local checkout from $here..."
            mkdir -p "$APP_DIR"
            # Everything except build output and virtualenvs, which are
            # recreated here and are large.
            tar -C "$here" \
                --exclude=./node_modules \
                --exclude=./frontend/node_modules \
                --exclude=./frontend/dist \
                --exclude=./.venv \
                --exclude=./venv \
                --exclude=./__pycache__ \
                -cf - . 2>/dev/null | tar -C "$APP_DIR" -xf -
            ok "Copied into $APP_DIR"
        fi
        drop_stray_env
        return
    fi

    if [ -d "$APP_DIR/.git" ]; then
        info "Updating the existing checkout..."
        git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1 \
            || warn "Could not fetch updates; continuing with the local copy"
        git -C "$APP_DIR" checkout -q "$BRANCH" 2>/dev/null || true
        git -C "$APP_DIR" merge --ff-only "origin/$BRANCH" >/dev/null 2>&1 \
            || warn "Local changes present; not updating"
        ok "Updated $APP_DIR"
        drop_stray_env
        return
    fi

    info "Cloning $REPO_URL ($BRANCH)..."
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR" >/dev/null 2>&1 \
        || die "Failed to clone $REPO_URL"
    ok "Cloned into $APP_DIR"
    drop_stray_env
}

drop_stray_env() {
    # A .env belonging to the Docker deployment is not used by this install -
    # everything is configured through $CONFIG_FILE - and it may hold secrets
    # that have no business sitting in the source tree.
    rm -f "$APP_DIR/.env" "$APP_DIR/backend/.env" "$APP_DIR/frontend/.env"
}

# --------------------------------------------------------------------------
# 4. user and directories
# --------------------------------------------------------------------------
create_user_and_dirs() {
    step "Creating the service user and directories"

    if id "$SERVICE_USER" >/dev/null 2>&1; then
        ok "User $SERVICE_USER already exists"
    else
        useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
        ok "Created system user $SERVICE_USER"
    fi

    if [ -e "$CONFIG_DIR" ] && [ ! -d "$CONFIG_DIR" ]; then
        die "$CONFIG_DIR exists and is not a directory; set CONFIG_DIR=... to relocate"
    fi

    mkdir -p "$DATA_DIR/backups" "$CONFIG_DIR"

    chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
    chmod 750 "$DATA_DIR" "$DATA_DIR/backups"

    # The application only ever reads its own source, and nothing else on the
    # system needs to see it. The web bundle gets opened up selectively in
    # build_frontend.
    chown -R root:"$SERVICE_USER" "$APP_DIR"
    chmod -R u=rwX,g=rX,o= "$APP_DIR"

    ok "Data directory: $DATA_DIR/backups"
}

# --------------------------------------------------------------------------
# 5. python environment
# --------------------------------------------------------------------------
setup_virtualenv() {
    step "Building the Python environment"

    local venv="$APP_DIR/venv"

    if [ ! -x "$venv/bin/python" ]; then
        python3 -m venv "$venv" || die "Failed to create the virtualenv"
        ok "Created virtualenv at $venv"
    else
        ok "Reusing the virtualenv at $venv"
    fi

    "$venv/bin/pip" install --quiet --upgrade pip wheel >/dev/null 2>&1 || true

    info "Installing Python dependencies (a minute or two)..."
    if ! try_with_progress 1800 /tmp/netconfig-pip.log \
            "$venv/bin/pip" install --quiet --requirement "$APP_DIR/backend/requirements.txt"; then
        # Every dependency publishes wheels for the common platforms, so
        # compilers are only needed on an architecture that has none. Install
        # them on demand rather than carrying ~200 MB of toolchain in every
        # container.
        warn "Falling back to building from source; installing build dependencies"
        with_progress 900 /tmp/netconfig-builddeps.log "Build dependency installation" \
            apt-get install -y -qq --no-install-recommends \
            build-essential python3-dev libpq-dev libffi-dev
        with_progress 2400 /tmp/netconfig-pip.log "Python dependency build" \
            "$venv/bin/pip" install --quiet --requirement "$APP_DIR/backend/requirements.txt"
    fi

    chown -R root:"$SERVICE_USER" "$venv"
    ok "Dependencies installed"
}

# --------------------------------------------------------------------------
# 6. postgresql
# --------------------------------------------------------------------------
setup_postgres() {
    step "Configuring PostgreSQL"

    systemctl enable --now postgresql >/dev/null 2>&1 || true

    local tries=0
    until su postgres -c "psql -tAc 'SELECT 1'" >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -lt 30 ] || die "PostgreSQL did not become ready"
        sleep 1
    done
    ok "PostgreSQL is running"

    local role_exists db_exists
    role_exists=$(su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\"" 2>/dev/null || echo "")
    db_exists=$(su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$DB_NAME'\"" 2>/dev/null || echo "")

    if [ "$role_exists" = "1" ]; then
        if [ "$GENERATED_CONFIG" = "true" ]; then
            # Fresh config means a freshly generated password, so the existing
            # role has to be brought in line with it or nothing can connect.
            su postgres -c "psql -q -c \"ALTER ROLE \\\"$DB_USER\\\" WITH LOGIN PASSWORD '$DB_PASSWORD'\"" >/dev/null
            ok "Reset the password for existing role $DB_USER"
        else
            ok "Role $DB_USER already exists"
        fi
    else
        su postgres -c "psql -q -c \"CREATE ROLE \\\"$DB_USER\\\" WITH LOGIN PASSWORD '$DB_PASSWORD'\"" >/dev/null
        ok "Created role $DB_USER"
    fi

    if [ "$db_exists" = "1" ]; then
        ok "Database $DB_NAME already exists"
    else
        su postgres -c "createdb -O \"$DB_USER\" \"$DB_NAME\"" >/dev/null
        ok "Created database $DB_NAME owned by $DB_USER"
    fi

    su postgres -c "psql -q -d \"$DB_NAME\" -c \"GRANT ALL ON SCHEMA public TO \\\"$DB_USER\\\"\"" >/dev/null 2>&1 || true
}

# --------------------------------------------------------------------------
# 7. redis
# --------------------------------------------------------------------------
setup_redis() {
    step "Configuring Redis"

    systemctl enable --now redis-server >/dev/null 2>&1 || true

    local tries=0
    until redis-cli ping >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -lt 30 ] || die "Redis did not become ready"
        sleep 1
    done

    # Applied through CONFIG SET + CONFIG REWRITE so the running server and
    # redis.conf agree, without this script having to edit a config file it
    # does not own. Redis here is only a Celery broker: the queue is bounded
    # rather than allowed to consume the container, and nothing needs to
    # survive a restart, so both persistence mechanisms are off.
    redis-cli config set maxmemory "${REDIS_MAXMEMORY:-256mb}" >/dev/null
    redis-cli config set maxmemory-policy noeviction >/dev/null
    redis-cli config set appendonly no >/dev/null
    redis-cli config set save "" >/dev/null
    redis-cli config rewrite >/dev/null 2>&1 || warn "Could not persist Redis settings to redis.conf"

    ok "Redis is running (maxmemory ${REDIS_MAXMEMORY:-256mb}, persistence off)"
}

# --------------------------------------------------------------------------
# 8. configuration
# --------------------------------------------------------------------------
random_hex() {
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
    else
        raw=$(head -c 32 /dev/urandom | base64)
    fi
    printf '%s' "$raw" | tr -d '\n' | tr '+/' '-_'
}

write_config() {
    step "Writing configuration"

    if [ -f "$CONFIG_FILE" ]; then
        GENERATED_CONFIG=false
        # shellcheck disable=SC1090
        DB_PASSWORD=$(config_value POSTGRES_PASSWORD "")
        [ -n "$DB_PASSWORD" ] || die "$CONFIG_FILE exists but has no POSTGRES_PASSWORD; remove it to reinstall"
        ADMIN_USERNAME=$(config_value ADMIN_USERNAME "$ADMIN_USERNAME")
        HTTP_PORT=$(config_value HTTP_PORT "$HTTP_PORT")
        ok "Keeping existing configuration and secrets ($CONFIG_FILE)"
        return
    fi

    GENERATED_CONFIG=true
    DB_PASSWORD=$(random_hex | cut -c1-24)

    local secret_key encryption_key
    secret_key=$(random_hex)
    encryption_key=$(random_fernet_key)

    mkdir -p "$CONFIG_DIR"
    umask 077
    cat > "$CONFIG_FILE" <<EOF
# Generated by lxc/install.sh on $(date -u '+%Y-%m-%dT%H:%M:%SZ')
#
# Read by the netconfig-api, netconfig-worker and netconfig-beat services as a
# systemd EnvironmentFile. Values must be bare KEY=value - no quoting, no shell
# expansion, no spaces around the equals sign.
#
# Back up ENCRYPTION_KEY. It decrypts every stored device password and cannot
# be recovered if lost.

# Database
POSTGRES_DB=$DB_NAME
POSTGRES_USER=$DB_USER
POSTGRES_PASSWORD=$DB_PASSWORD
DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME

# Redis / Celery
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
CELERY_CONCURRENCY=$CELERY_CONCURRENCY

# Security
SECRET_KEY=$secret_key
JWT_SECRET_KEY=$secret_key
ENCRYPTION_KEY=$encryption_key
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Application
DEBUG=false
LOG_LEVEL=INFO
API_V1_PREFIX=/api/v1
# nginx serves the UI and the API from one origin, so no cross-origin
# requests are made and this list only matters for external API clients.
CORS_ORIGINS=http://localhost,http://localhost:$HTTP_PORT

# Backups
BACKUP_BASE_PATH=$DATA_DIR/backups
MAX_CONCURRENT_BACKUPS=$MAX_CONCURRENT_BACKUPS
DEFAULT_RETENTION_DAYS=90
DEFAULT_SSH_TIMEOUT=60
BACKUP_DEDUPLICATE=true

# Connection pool, per process
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10

# Service ports
API_PORT=$API_PORT
HTTP_PORT=$HTTP_PORT
WEB_CONCURRENCY=$WEB_CONCURRENCY

# Admin user created during initialization
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD
ADMIN_ORG_NAME=Default Organization
EOF
    umask 022

    chown root:"$SERVICE_USER" "$CONFIG_FILE"
    chmod 640 "$CONFIG_FILE"

    ok "Generated $CONFIG_FILE with a random database password, JWT secret and Fernet key"
}

config_value() {
    local key=$1 fallback=$2 line
    line=$(grep -E "^${key}=" "$CONFIG_FILE" 2>/dev/null | tail -1 || true)
    if [ -n "$line" ]; then
        printf '%s' "${line#*=}"
    else
        printf '%s' "$fallback"
    fi
}

# --------------------------------------------------------------------------
# 9. frontend
# --------------------------------------------------------------------------
build_frontend() {
    step "Building the web interface"

    if [ "$SKIP_FRONTEND" = "true" ]; then
        warn "SKIP_FRONTEND=true; the API will run without a UI"
        # nginx still needs a document root that exists, or it refuses to
        # serve anything at all - including the /api proxy.
        mkdir -p "$APP_DIR/frontend/dist"
        cat > "$APP_DIR/frontend/dist/index.html" <<'PLACEHOLDER'
<!doctype html>
<title>NetConfig Backup</title>
<p>The web interface was not built (SKIP_FRONTEND=true).
   The API is available under <a href="/docs">/docs</a>.</p>
PLACEHOLDER
        publish_bundle
        return
    fi

    cd "$APP_DIR/frontend"

    info "Installing UI dependencies (a few minutes)..."
    if [ -f package-lock.json ]; then
        with_progress 1800 /tmp/netconfig-npm.log "npm ci" \
            npm ci --no-audit --no-fund --silent
    else
        with_progress 1800 /tmp/netconfig-npm.log "npm install" \
            npm install --no-audit --no-fund --silent
    fi

    info "Compiling the bundle..."
    # Relative base URL: nginx serves the UI and proxies /api on the same
    # origin, so the browser never makes a cross-origin request.
    with_progress 1200 /tmp/netconfig-build.log "Frontend build" \
        env VITE_API_URL=/api/v1 npm run build

    # node_modules is ~200 MB and is not needed once the bundle exists.
    rm -rf node_modules

    publish_bundle

    ok "Built $(find "$APP_DIR/frontend/dist" -type f | wc -l) files into frontend/dist"
    cd - >/dev/null
}

publish_bundle() {
    # nginx runs as www-data, which is neither the owner nor in the group, so
    # it needs execute (traversal) on the path down to the bundle and read
    # access to the bundle itself. Only those are opened up - the backend
    # source, the virtualenv and anything else under $APP_DIR stay private.
    chown -R root:"$SERVICE_USER" "$APP_DIR/frontend/dist"
    chmod o+x "$APP_DIR" "$APP_DIR/frontend"
    chmod -R o+rX "$APP_DIR/frontend/dist"
}

# --------------------------------------------------------------------------
# 10. services
# --------------------------------------------------------------------------
install_services() {
    step "Installing systemd services and nginx"

    local src="$APP_DIR/lxc"

    for unit in netconfig-api netconfig-worker netconfig-beat; do
        [ -f "$src/systemd/$unit.service" ] || die "Missing unit file: $src/systemd/$unit.service"
        sed -e "s|@APP_DIR@|$APP_DIR|g" \
            -e "s|@DATA_DIR@|$DATA_DIR|g" \
            -e "s|@CONFIG_FILE@|$CONFIG_FILE|g" \
            -e "s|@USER@|$SERVICE_USER|g" \
            "$src/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
        chmod 644 "/etc/systemd/system/$unit.service"
    done
    ok "Installed 3 systemd units"

    # Only add the IPv6 listener where the kernel actually has IPv6. A
    # hard-coded "listen [::]:80" makes nginx fail to start outright on an
    # IPv4-only container, which many LXC deployments are.
    local listen6="    # IPv6 is not available on this host"
    if [ -f /proc/net/if_inet6 ]; then
        listen6="    listen [::]:$HTTP_PORT;"
    fi

    sed -e "s|@APP_DIR@|$APP_DIR|g" \
        -e "s|@HTTP_PORT@|$HTTP_PORT|g" \
        -e "s|@API_PORT@|$API_PORT|g" \
        -e "s|@LISTEN6@|$listen6|g" \
        "$src/nginx/netconfig.conf" > /etc/nginx/sites-available/netconfig.conf

    ln -sf /etc/nginx/sites-available/netconfig.conf /etc/nginx/sites-enabled/netconfig.conf

    # The distribution default site also listens on port 80 and would win or
    # conflict depending on ordering.
    rm -f /etc/nginx/sites-enabled/default

    nginx -t >/tmp/netconfig-nginx.log 2>&1 \
        || { cat /tmp/netconfig-nginx.log >&2; die "nginx configuration is invalid"; }
    ok "nginx configuration valid"

    systemctl daemon-reload
    systemctl enable netconfig-api netconfig-worker netconfig-beat >/dev/null 2>&1
    ok "Services enabled at boot"
}

# --------------------------------------------------------------------------
# 11. migrate, start, initialize
# --------------------------------------------------------------------------
start_and_initialize() {
    step "Migrating the database and starting services"

    # Migrations run before the API so the first request never races the
    # schema. The API unit repeats this on every start, which is a no-op when
    # the schema is already current.
    run_as_service "$APP_DIR/venv/bin/alembic upgrade head" "$APP_DIR/backend" \
        || die "Database migration failed"
    ok "Schema is up to date"

    SERVICES_STARTED=1
    systemctl restart netconfig-api netconfig-worker netconfig-beat
    systemctl reload-or-restart nginx
    ok "Services started"

    info "Waiting for the API to report healthy..."
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) body=""
    while [ $SECONDS -lt $deadline ]; do
        if body=$(curl -fsS --max-time 5 "http://127.0.0.1:$API_PORT/api/v1/health" 2>/dev/null); then
            case "$body" in
                *'"status":"healthy"'*) ok "API reports healthy"; break ;;
                *'"status":"degraded"'*) info "API is up, dependencies still starting..." ;;
            esac
        fi
        sleep 2
        printf '.'
    done
    printf '\n'

    case "$body" in
        *'"status":"healthy"'*) ;;
        *)
            warn "API did not report healthy within ${HEALTH_TIMEOUT}s"
            [ -n "$body" ] && info "last response: $body"
            journalctl -u netconfig-api --no-pager -n 20 >&2 || true
            ;;
    esac

    run_as_service "$APP_DIR/venv/bin/python init_db.py --reset-admin-password" "$APP_DIR/backend" \
        || die "Failed to create the admin user"
}

run_as_service() {
    local command=$1 workdir=$2

    # Build the same environment the services get, so a migration cannot
    # succeed here and then fail under systemd because of a different
    # DATABASE_URL.
    #
    # The file is parsed the way systemd parses an EnvironmentFile - each line
    # is a literal KEY=value with one optional layer of surrounding quotes -
    # rather than sourced by the shell. Sourcing would execute
    # "ADMIN_ORG_NAME=Default Organization" as a command named Organization,
    # and would run anything else the file happened to contain.
    local -a env_args=()
    local line key value

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*|';'*) continue ;;
        esac

        [ "${line#*=}" != "$line" ] || continue

        key=${line%%=*}
        value=${line#*=}

        case "$value" in
            \"*\") value=${value#\"}; value=${value%\"} ;;
            \'*\') value=${value#\'}; value=${value%\'} ;;
        esac

        env_args+=("$key=$value")
    done < "$CONFIG_FILE"

    # shellcheck disable=SC2086 # $command is a literal built above
    ( cd "$workdir" && sudo --user "$SERVICE_USER" \
        env "${env_args[@]}" "PATH=$APP_DIR/venv/bin:$PATH" $command )
}

# --------------------------------------------------------------------------
summary() {
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -n "$ip" ] || ip="localhost"

    local url="http://$ip"
    [ "$HTTP_PORT" = "80" ] || url="http://$ip:$HTTP_PORT"

    printf '\n%s' "$GREEN"
    printf '=======================================================================\n'
    printf '  Installation complete\n'
    printf '=======================================================================%s\n\n' "$NC"

    printf '  %sWeb UI%s        %s\n' "$BOLD" "$NC" "$url"
    printf '  %sAPI docs%s      %s/docs\n' "$BOLD" "$NC" "$url"

    printf '\n  %sSign in with%s\n' "$BOLD" "$NC"
    printf '    username  %s\n' "$ADMIN_USERNAME"
    printf '    password  %s\n' "$ADMIN_PASSWORD"

    printf '\n  %s! This is a temporary password. Change it after first login.%s\n' "$YELLOW" "$NC"
    printf '  %s! Back up %s - its ENCRYPTION_KEY decrypts stored%s\n' "$YELLOW" "$CONFIG_FILE" "$NC"
    printf '  %s  device credentials and cannot be recovered if lost.%s\n' "$YELLOW" "$NC"

    printf '\n  %sManaging the services%s\n' "$BOLD" "$NC"
    printf '    systemctl status netconfig-api      API status\n'
    printf '    journalctl -u netconfig-api -f      follow API logs\n'
    printf '    journalctl -u netconfig-worker -f   follow backup worker logs\n'
    printf '    systemctl restart netconfig-api     restart after a config change\n'
    printf '    %s                  configuration and secrets\n' "$CONFIG_FILE"
    printf '\n'
}

main() {
    banner
    preflight
    install_packages
    obtain_source
    create_user_and_dirs
    write_config
    setup_virtualenv
    setup_postgres
    setup_redis
    build_frontend
    install_services
    start_and_initialize
    summary
}

main "$@"
