#!/usr/bin/env bash
#
# Network Config Backup System - Proxmox VE container creator
#
# Run this ON THE PROXMOX HOST (not inside a container). It creates an
# unprivileged Debian 12 LXC container and installs the application into it
# with lxc/install.sh.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/proxmox-create-lxc.sh)"
#
# or, from a checkout on the host:
#
#   ./lxc/proxmox-create-lxc.sh
#
# Useful environment variables:
#   CTID=210                 container ID (default: next free)
#   HOSTNAME=netconfig       container hostname
#   CORES=2 MEMORY=2048      CPU cores and MB of RAM
#   SWAP=512 DISK=8          MB of swap and GB of disk
#   BRIDGE=vmbr0             network bridge
#   IPCONFIG=dhcp            "dhcp" or e.g. "192.168.1.50/24,gw=192.168.1.1"
#   STORAGE=local-lvm        storage for the container root filesystem
#   TEMPLATE_STORAGE=local   storage holding the container template
#   ADMIN_PASSWORD=changeme  initial admin password for the application
#   START_ON_BOOT=1          start the container at host boot
#   UNPRIVILEGED=1           create an unprivileged container

set -Eeuo pipefail

CTID="${CTID:-}"
CT_HOSTNAME="${HOSTNAME:-netconfig}"
CORES="${CORES:-2}"
MEMORY="${MEMORY:-2048}"
SWAP="${SWAP:-512}"
DISK="${DISK:-8}"
BRIDGE="${BRIDGE:-vmbr0}"
IPCONFIG="${IPCONFIG:-dhcp}"
STORAGE="${STORAGE:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"
START_ON_BOOT="${START_ON_BOOT:-1}"
TEMPLATE_PATTERN="${TEMPLATE_PATTERN:-debian-12-standard}"

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-changeme}"
BRANCH="${BRANCH:-main}"
REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/bhd-cap/netconfig/$BRANCH}"

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

CREATED_CTID=""
on_error() {
    printf '\n%screate failed%s (line %s)\n' "$RED" "$NC" "$1" >&2
    if [ -n "$CREATED_CTID" ]; then
        printf 'The container %s was created and left in place for inspection:\n' "$CREATED_CTID" >&2
        printf '  pct status %s\n  pct enter %s\n' "$CREATED_CTID" "$CREATED_CTID" >&2
        printf 'Remove it with: pct stop %s && pct destroy %s\n' "$CREATED_CTID" "$CREATED_CTID" >&2
    fi
}
trap 'on_error $LINENO' ERR

banner() {
    printf '%s' "$BOLD"
    cat <<'ART'
  ______  _______ _______ ______  _______ _______ _     _ _____ _____
  |     | |______    |    |     | |_____| |       |____/    |     |
  |_____| |______    |    |_____| |     | |_____  |    \_ __|__ __|__

  Network Config Backup System - Proxmox LXC creator
ART
    printf '%s\n' "$NC"
}

# --------------------------------------------------------------------------
# 1. preflight
# --------------------------------------------------------------------------
preflight() {
    step "Checking the Proxmox host"

    [ "$(id -u)" -eq 0 ] || die "This script must run as root on the Proxmox host"

    command -v pct >/dev/null 2>&1 || die \
"pct not found. This script runs on a Proxmox VE host.
       To install inside an existing container instead, run lxc/install.sh
       from within it."

    command -v pveam >/dev/null 2>&1 || die "pveam not found; is this a Proxmox VE host?"
    ok "Proxmox VE $(pveversion 2>/dev/null | head -1 | cut -d/ -f2 || echo detected)"

    if [ -z "$CTID" ]; then
        CTID=$(pvesh get /cluster/nextid 2>/dev/null) \
            || die "Could not determine a free container ID; set CTID=..."
    fi

    if pct status "$CTID" >/dev/null 2>&1; then
        die "Container $CTID already exists. Choose another with CTID=..."
    fi
    ok "Container ID: $CTID"

    if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
        warn "Bridge $BRIDGE not found on this host; creation may fail"
    fi
}

# --------------------------------------------------------------------------
# 2. storage
# --------------------------------------------------------------------------
pick_storage() {
    step "Selecting storage"

    if [ -z "$TEMPLATE_STORAGE" ]; then
        TEMPLATE_STORAGE=$(pvesm status --content vztmpl 2>/dev/null \
            | awk 'NR>1 && $3=="active" {print $1; exit}')
        [ -n "$TEMPLATE_STORAGE" ] || die \
"No active storage accepts container templates (content type vztmpl).
       Enable one in Datacenter -> Storage, or set TEMPLATE_STORAGE=..."
    fi
    ok "Template storage: $TEMPLATE_STORAGE"

    if [ -z "$STORAGE" ]; then
        STORAGE=$(pvesm status --content rootdir 2>/dev/null \
            | awk 'NR>1 && $3=="active" {print $1; exit}')
        [ -n "$STORAGE" ] || die \
"No active storage accepts container root filesystems (content type rootdir).
       Enable one in Datacenter -> Storage, or set STORAGE=..."
    fi
    ok "Root filesystem storage: $STORAGE ($DISK GB)"
}

# --------------------------------------------------------------------------
# 3. template
# --------------------------------------------------------------------------
ensure_template() {
    step "Preparing the Debian template"

    local existing
    existing=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null \
        | awk -v pat="$TEMPLATE_PATTERN" '$1 ~ pat {print $1}' | tail -1)

    if [ -n "$existing" ]; then
        TEMPLATE_VOLUME="$existing"
        ok "Using the template already on $TEMPLATE_STORAGE"
        return
    fi

    info "Refreshing the template catalogue..."
    pveam update >/dev/null 2>&1 || warn "pveam update failed; using the cached catalogue"

    local available
    available=$(pveam available --section system 2>/dev/null \
        | awk -v pat="$TEMPLATE_PATTERN" '$2 ~ pat {print $2}' | sort | tail -1)
    [ -n "$available" ] || die "No template matching '$TEMPLATE_PATTERN' is available"

    info "Downloading $available (this takes a minute)..."
    pveam download "$TEMPLATE_STORAGE" "$available" >/dev/null \
        || die "Failed to download the template"

    TEMPLATE_VOLUME="$TEMPLATE_STORAGE:vztmpl/$available"
    ok "Downloaded $available"
}

# --------------------------------------------------------------------------
# 4. create
# --------------------------------------------------------------------------
create_container() {
    step "Creating container $CTID"

    local net="name=eth0,bridge=$BRIDGE"
    if [ "$IPCONFIG" = "dhcp" ]; then
        net="$net,ip=dhcp"
    else
        net="$net,ip=$IPCONFIG"
    fi

    info "$CORES cores, ${MEMORY}MB RAM, ${DISK}GB disk, $IPCONFIG on $BRIDGE"

    pct create "$CTID" "$TEMPLATE_VOLUME" \
        --hostname "$CT_HOSTNAME" \
        --cores "$CORES" \
        --memory "$MEMORY" \
        --swap "$SWAP" \
        --rootfs "$STORAGE:$DISK" \
        --net0 "$net" \
        --ostype debian \
        --unprivileged "$UNPRIVILEGED" \
        --features nesting=1 \
        --onboot "$START_ON_BOOT" \
        --description "Network Config Backup System" \
        >/dev/null || die "pct create failed"

    CREATED_CTID="$CTID"
    ok "Container created"

    info "Starting..."
    pct start "$CTID" >/dev/null || die "Failed to start container $CTID"

    local tries=0
    until pct exec "$CTID" -- test -d /run/systemd/system >/dev/null 2>&1; do
        tries=$((tries + 1))
        [ "$tries" -lt 60 ] || die "Container $CTID did not finish booting"
        sleep 1
    done
    ok "Container is up"

    info "Waiting for network..."
    tries=0
    until pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 60 ]; then
            die "No network inside the container. Check the bridge ($BRIDGE) and IP settings ($IPCONFIG)."
        fi
        sleep 2
    done
    ok "Network is up"
}

# --------------------------------------------------------------------------
# 5. install
# --------------------------------------------------------------------------
install_application() {
    step "Installing the application inside the container"

    info "Installing curl and git..."
    pct exec "$CTID" -- bash -c \
        "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl git >/dev/null" \
        || die "Failed to install prerequisites inside the container"

    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"

    if [ -n "$here" ] && [ -f "$here/lxc/install.sh" ] && [ -d "$here/backend" ]; then
        # Running from a checkout on the host: push that exact tree in, so the
        # container gets the code being looked at rather than whatever is on
        # the branch.
        info "Copying the local checkout into the container..."
        pct exec "$CTID" -- mkdir -p /opt/netconfig
        tar -C "$here" \
            --exclude=./.git \
            --exclude=./node_modules \
            --exclude=./frontend/node_modules \
            --exclude=./frontend/dist \
            --exclude=./.venv \
            --exclude=./venv \
            -cf - . 2>/dev/null \
            | pct exec "$CTID" -- tar -C /opt/netconfig -xf - \
            || die "Failed to copy the source into the container"

        info "Running the installer (several minutes)..."
        pct exec "$CTID" -- bash -c \
            "ADMIN_USERNAME='$ADMIN_USERNAME' ADMIN_PASSWORD='$ADMIN_PASSWORD' bash /opt/netconfig/lxc/install.sh" \
            || die "The in-container installer failed"
    else
        info "Fetching and running the installer (several minutes)..."
        pct exec "$CTID" -- bash -c \
            "curl -fsSL '$REPO_RAW/lxc/install.sh' -o /tmp/netconfig-install.sh && ADMIN_USERNAME='$ADMIN_USERNAME' ADMIN_PASSWORD='$ADMIN_PASSWORD' BRANCH='$BRANCH' bash /tmp/netconfig-install.sh" \
            || die "The in-container installer failed"
    fi

    ok "Application installed"
}

# --------------------------------------------------------------------------
summary() {
    local ip
    ip=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
    [ -n "$ip" ] || ip="<container-ip>"

    printf '\n%s' "$GREEN"
    printf '=======================================================================\n'
    printf '  Container %s is ready\n' "$CTID"
    printf '=======================================================================%s\n\n' "$NC"

    printf '  %sWeb UI%s        http://%s\n' "$BOLD" "$NC" "$ip"
    printf '  %sAPI docs%s      http://%s/docs\n' "$BOLD" "$NC" "$ip"

    printf '\n  %sSign in with%s\n' "$BOLD" "$NC"
    printf '    username  %s\n' "$ADMIN_USERNAME"
    printf '    password  %s\n' "$ADMIN_PASSWORD"
    printf '\n  %s! This is a temporary password. Change it after first login.%s\n' "$YELLOW" "$NC"

    printf '\n  %sManaging the container%s\n' "$BOLD" "$NC"
    printf '    pct enter %s                        shell inside the container\n' "$CTID"
    printf '    pct stop %s / pct start %s\n' "$CTID" "$CTID"
    printf '    pct exec %s -- journalctl -u netconfig-api -f\n' "$CTID"
    printf '\n'
}

main() {
    banner
    preflight
    pick_storage
    ensure_template
    create_container
    install_application
    summary
}

main "$@"
