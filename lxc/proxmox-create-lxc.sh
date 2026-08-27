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
# Every step prints what it is doing and has a time limit, so a stall is
# reported rather than left to look like a hang. Set DEBUG=1 to trace every
# command.
#
# Useful environment variables:
#   CTID=210                 container ID (default: next free)
#   CT_HOSTNAME=netconfig    container hostname
#   CORES=2 MEMORY=2048      CPU cores and MB of RAM
#   SWAP=512 DISK=8          MB of swap and GB of disk
#   BRIDGE=vmbr0             network bridge
#   IPCONFIG=dhcp            "dhcp" or e.g. "192.168.1.50/24,gw=192.168.1.1"
#   NAMESERVER=1.1.1.1       DNS server for the container (default: host's)
#   STORAGE=local-lvm        storage for the container root filesystem
#   TEMPLATE_STORAGE=local   storage holding the container template
#   ADMIN_PASSWORD=changeme  initial admin password for the application
#   START_ON_BOOT=1          start the container at host boot
#   UNPRIVILEGED=1           create an unprivileged container
#   DEBUG=1                  trace every command
#
# Time limits (seconds), raise on slow hosts or links:
#   T_STORAGE=60 T_TEMPLATE_UPDATE=180 T_TEMPLATE_DOWNLOAD=1800
#   T_CREATE=900 T_BOOT=120 T_NETWORK=180 T_INSTALL=3600

set -Eeuo pipefail

[ "${DEBUG:-0}" = "1" ] && set -x

CTID="${CTID:-}"
# Not HOSTNAME: bash sets that variable to the machine's own hostname, so
# "${HOSTNAME:-netconfig}" silently named every container after the Proxmox
# host it was created on.
CT_HOSTNAME="${CT_HOSTNAME:-netconfig}"
CORES="${CORES:-2}"
MEMORY="${MEMORY:-2048}"
SWAP="${SWAP:-512}"
DISK="${DISK:-8}"
BRIDGE="${BRIDGE:-vmbr0}"
IPCONFIG="${IPCONFIG:-dhcp}"
NAMESERVER="${NAMESERVER:-}"
STORAGE="${STORAGE:-}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"
START_ON_BOOT="${START_ON_BOOT:-1}"
TEMPLATE_PATTERN="${TEMPLATE_PATTERN:-debian-12-standard}"

ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-changeme}"
BRANCH="${BRANCH:-main}"
REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/bhd-cap/netconfig/$BRANCH}"

# Time limits. Every external command that touches the network, storage or the
# container gets one; without them a single unreachable NFS share or stalled
# download stops the whole script with no output.
T_STORAGE="${T_STORAGE:-60}"
T_TEMPLATE_UPDATE="${T_TEMPLATE_UPDATE:-180}"
T_TEMPLATE_DOWNLOAD="${T_TEMPLATE_DOWNLOAD:-1800}"
T_CREATE="${T_CREATE:-900}"
T_BOOT="${T_BOOT:-120}"
T_NETWORK="${T_NETWORK:-180}"
T_INSTALL="${T_INSTALL:-3600}"

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
    printf '\nRe-run with DEBUG=1 to trace every command.\n' >&2
}
trap 'on_error $LINENO' ERR

# Run a command with a time limit, reporting a stall as a stall rather than
# letting it look like a hang.
#
#   run_limited <seconds> <what it is doing> <command...>
run_limited() {
    local limit=$1 what=$2; shift 2
    local rc=0

    timeout --foreground "$limit" "$@" || rc=$?

    if [ "$rc" -eq 124 ]; then
        die "$what timed out after ${limit}s.
       Command: $*
       This usually means it is waiting on something unreachable. Raise the
       limit with the matching T_* variable, or investigate that command by
       hand."
    fi

    return "$rc"
}

# Same, but capture stdout.
capture_limited() {
    local limit=$1 what=$2; shift 2
    local out rc=0

    out=$(timeout --foreground "$limit" "$@" 2>/dev/null) || rc=$?

    if [ "$rc" -eq 124 ]; then
        die "$what timed out after ${limit}s.
       Command: $*
       On a Proxmox host this is most often a storage that is configured but
       unreachable (a dead NFS or CIFS share): 'pvesm status' blocks on it.
       Disable that storage, or name the one to use explicitly - see
       STORAGE= and TEMPLATE_STORAGE=."
    fi

    printf '%s' "$out"
    return 0
}

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
    command -v timeout >/dev/null 2>&1 || die "timeout not found (install coreutils)"

    ok "Proxmox VE $(pveversion 2>/dev/null | head -1 | cut -d/ -f2 || echo detected)"

    if [ -z "$CTID" ]; then
        info "Asking the cluster for a free container ID..."
        CTID=$(capture_limited 30 "pvesh get /cluster/nextid" pvesh get /cluster/nextid)
        CTID=$(printf '%s' "$CTID" | tr -d '"[:space:]')
        [ -n "$CTID" ] || die "Could not determine a free container ID; set CTID=..."
    fi

    if pct status "$CTID" >/dev/null 2>&1; then
        die "Container $CTID already exists. Choose another with CTID=..."
    fi
    ok "Container ID: $CTID"

    if ! ip link show "$BRIDGE" >/dev/null 2>&1; then
        warn "Bridge $BRIDGE not found on this host; creation may fail"
    fi

    ok "Hostname: $CT_HOSTNAME"
}

# --------------------------------------------------------------------------
# 2. storage
# --------------------------------------------------------------------------
pick_storage() {
    step "Selecting storage"

    # pvesm status probes every configured storage, so one unreachable share
    # blocks it. Bounded, and the timeout message says exactly that.
    if [ -z "$TEMPLATE_STORAGE" ]; then
        info "Looking for a storage that holds container templates..."
        TEMPLATE_STORAGE=$(capture_limited "$T_STORAGE" "pvesm status" \
            pvesm status --content vztmpl \
            | awk 'NR>1 && $3=="active" {print $1; exit}')
        [ -n "$TEMPLATE_STORAGE" ] || die \
"No active storage accepts container templates (content type vztmpl).
       Enable one in Datacenter -> Storage, or set TEMPLATE_STORAGE=..."
    fi
    ok "Template storage: $TEMPLATE_STORAGE"

    if [ -z "$STORAGE" ]; then
        info "Looking for a storage for the root filesystem..."
        STORAGE=$(capture_limited "$T_STORAGE" "pvesm status" \
            pvesm status --content rootdir \
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
    existing=$(capture_limited 60 "pveam list" pveam list "$TEMPLATE_STORAGE" \
        | awk -v pat="$TEMPLATE_PATTERN" '$1 ~ pat {print $1}' | tail -1)

    if [ -n "$existing" ]; then
        TEMPLATE_VOLUME="$existing"
        ok "Using the template already on $TEMPLATE_STORAGE: ${existing##*/}"
        return
    fi

    info "Refreshing the template catalogue (up to ${T_TEMPLATE_UPDATE}s)..."
    run_limited "$T_TEMPLATE_UPDATE" "pveam update" pveam update >/dev/null 2>&1 \
        || warn "pveam update failed; using the cached catalogue"

    local available
    available=$(capture_limited 60 "pveam available" pveam available --section system \
        | awk -v pat="$TEMPLATE_PATTERN" '$2 ~ pat {print $2}' | sort -V | tail -1)
    [ -n "$available" ] || die \
"No template matching '$TEMPLATE_PATTERN' is available.
       Check connectivity to download.proxmox.com, or download a template by
       hand and re-run:  pveam download $TEMPLATE_STORAGE <template>"

    # Progress is left on screen deliberately: this is a ~130 MB download and
    # silence here was indistinguishable from a hang.
    info "Downloading $available"
    info "(~130 MB; progress below, up to ${T_TEMPLATE_DOWNLOAD}s)"
    run_limited "$T_TEMPLATE_DOWNLOAD" "Template download" \
        pveam download "$TEMPLATE_STORAGE" "$available" \
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

    # Give the container a resolver. A container that gets an address by DHCP
    # but no usable nameserver is the most common reason the install stalls
    # later, and Proxmox only passes the host's resolver on by default when
    # the host has one configured.
    local nameserver="$NAMESERVER"
    if [ -z "$nameserver" ] && [ -r /etc/resolv.conf ]; then
        nameserver=$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf || true)
    fi

    local -a create_args=(
        "$CTID" "$TEMPLATE_VOLUME"
        --hostname "$CT_HOSTNAME"
        --cores "$CORES"
        --memory "$MEMORY"
        --swap "$SWAP"
        --rootfs "$STORAGE:$DISK"
        --net0 "$net"
        --ostype debian
        --unprivileged "$UNPRIVILEGED"
        --features nesting=1
        --onboot "$START_ON_BOOT"
        --description "Network Config Backup System"
    )
    [ -n "$nameserver" ] && create_args+=(--nameserver "$nameserver")

    info "$CORES cores, ${MEMORY}MB RAM, ${DISK}GB disk, $IPCONFIG on $BRIDGE"
    [ -n "$nameserver" ] && info "DNS: $nameserver"
    info "Extracting the template (up to ${T_CREATE}s)..."

    run_limited "$T_CREATE" "pct create" pct create "${create_args[@]}" \
        || die "pct create failed"

    CREATED_CTID="$CTID"
    ok "Container created"

    info "Starting..."
    run_limited 120 "pct start" pct start "$CTID" || die "Failed to start container $CTID"

    # Wall-clock deadlines, not iteration counts: each probe can itself block,
    # so counting attempts does not bound how long the loop runs.
    info "Waiting for it to finish booting (up to ${T_BOOT}s)..."
    local deadline=$((SECONDS + T_BOOT))
    until timeout 10 pct exec "$CTID" -- test -d /run/systemd/system >/dev/null 2>&1; do
        [ $SECONDS -lt $deadline ] || die \
"Container $CTID did not finish booting within ${T_BOOT}s.
       Look at its console:  pct console $CTID   (or: pct enter $CTID)"
        printf '.'
        sleep 2
    done
    printf '\n'
    ok "Container is up"

    wait_for_network
}

wait_for_network() {
    # Separated into address then DNS so the error names the actual problem
    # instead of "no network".
    info "Waiting for an IP address (up to ${T_NETWORK}s)..."
    local deadline=$((SECONDS + T_NETWORK)) addr=""

    while [ $SECONDS -lt $deadline ]; do
        addr=$(timeout 10 pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
        [ -n "$addr" ] && break
        printf '.'
        sleep 2
    done
    printf '\n'

    [ -n "$addr" ] || die \
"Container $CTID never got an IP address.
       IPCONFIG is '$IPCONFIG' on bridge '$BRIDGE'.
       With dhcp, check a DHCP server is reachable on that bridge; otherwise
       set a static address:  IPCONFIG=192.168.1.50/24,gw=192.168.1.1"
    ok "Address: $addr"

    info "Checking DNS resolution..."
    deadline=$((SECONDS + 60))
    while [ $SECONDS -lt $deadline ]; do
        if timeout 10 pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then
            ok "DNS is working"
            return 0
        fi
        printf '.'
        sleep 3
    done
    printf '\n'

    die \
"Container $CTID has the address $addr but cannot resolve deb.debian.org.
       Its resolver is not working, so package installation would hang.
       Fix it with a working nameserver and re-run:
         pct set $CTID --nameserver 1.1.1.1
       or re-run this script with NAMESERVER=1.1.1.1"
}

# --------------------------------------------------------------------------
# 5. install
# --------------------------------------------------------------------------
install_application() {
    step "Installing the application inside the container"

    info "Installing curl and git inside the container..."
    run_limited 600 "apt-get inside the container" \
        pct exec "$CTID" -- bash -c \
        "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl git" \
        || die "Failed to install prerequisites inside the container"
    ok "Prerequisites installed"

    # ${BASH_SOURCE[0]} is unset when this script is run as
    # bash -c "$(curl ...)", and under 'set -u' referencing it aborts the
    # subshell, so it needs a default.
    local self="${BASH_SOURCE[0]:-}"
    local here=""
    if [ -n "$self" ]; then
        here="$(cd "$(dirname "$self")/.." 2>/dev/null && pwd || true)"
    fi

    if [ -n "$here" ] && [ -f "$here/lxc/install.sh" ] && [ -d "$here/backend" ]; then
        # Running from a checkout on the host: push that exact tree in, so the
        # container gets the code being looked at rather than whatever is on
        # the branch.
        info "Copying the local checkout from $here..."
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

        info "Running the installer - full output follows (up to ${T_INSTALL}s)"
        printf '      %s\n\n' "-----------------------------------------------------------"
        run_limited "$T_INSTALL" "The in-container installer" \
            pct exec "$CTID" -- bash -c \
            "ADMIN_USERNAME='$ADMIN_USERNAME' ADMIN_PASSWORD='$ADMIN_PASSWORD' bash /opt/netconfig/lxc/install.sh" \
            || die "The in-container installer failed"
    else
        info "Fetching the installer from $REPO_RAW..."
        run_limited 120 "Downloading the installer" \
            pct exec "$CTID" -- curl -fsSL "$REPO_RAW/lxc/install.sh" -o /tmp/netconfig-install.sh \
            || die \
"Could not download the installer from
       $REPO_RAW/lxc/install.sh
       Check that the branch exists and the container can reach GitHub."

        info "Running the installer - full output follows (up to ${T_INSTALL}s)"
        printf '      %s\n\n' "-----------------------------------------------------------"
        run_limited "$T_INSTALL" "The in-container installer" \
            pct exec "$CTID" -- bash -c \
            "ADMIN_USERNAME='$ADMIN_USERNAME' ADMIN_PASSWORD='$ADMIN_PASSWORD' BRANCH='$BRANCH' bash /tmp/netconfig-install.sh" \
            || die "The in-container installer failed"
    fi

    ok "Application installed"
}

# --------------------------------------------------------------------------
summary() {
    local ip
    ip=$(timeout 15 pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)
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
