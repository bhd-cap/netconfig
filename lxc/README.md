# LXC / bare-metal deployment

Runs the application natively under systemd, with no Docker. PostgreSQL,
Redis, nginx, the API, the backup worker and the scheduler are ordinary system
services.

Use this when the host is a Proxmox VE server (or any Debian/Ubuntu machine)
and you would rather not run a container engine inside a container. Use the
[Docker deployment](../README.md) when you want the stack isolated from the
host or need to run it on macOS or Windows.

## Install

### On a Proxmox VE host — creates the container for you

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/proxmox-create-lxc.sh)"
```

Creates an unprivileged Debian 12 container, installs everything into it, and
prints the URL and credentials. Run it on the **host**, not inside a container.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CTID` | next free | Container ID |
| `CT_HOSTNAME` | `netconfig` | Container hostname |
| `CORES` / `MEMORY` | `2` / `2048` | CPU cores, MB of RAM |
| `DISK` / `SWAP` | `8` / `512` | GB of disk, MB of swap |
| `BRIDGE` | `vmbr0` | Network bridge |
| `IPCONFIG` | `dhcp` | `dhcp`, or `192.168.1.50/24,gw=192.168.1.1` |
| `NAMESERVER` | host's resolver | DNS server for the container |
| `STORAGE` | first active | Storage for the root filesystem |
| `TEMPLATE_STORAGE` | first active | Storage holding the Debian template |
| `ADMIN_PASSWORD` | `changeme` | Initial admin password |
| `DEBUG` | `0` | Set to `1` to trace every command |

```bash
CTID=210 MEMORY=4096 DISK=32 IPCONFIG=192.168.1.50/24,gw=192.168.1.1 \
  bash -c "$(curl -fsSL .../lxc/proxmox-create-lxc.sh)"
```

Every step prints what it is doing and has its own time limit, so a stall is
reported with the command that stalled rather than sitting there silently.
Raise a limit with the matching `T_*` variable (`T_STORAGE`,
`T_TEMPLATE_DOWNLOAD`, `T_CREATE`, `T_BOOT`, `T_NETWORK`, `T_INSTALL`), and
run with `DEBUG=1` to trace every command.

### Inside an existing container, VM or server

Debian 11/12 or Ubuntu 22.04/24.04, as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/install.sh)"
```

or from a checkout:

```bash
sudo ./lxc/install.sh
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `changeme` | Initial admin account |
| `HTTP_PORT` | `80` | Port nginx listens on |
| `APP_DIR` | `/opt/netconfig` | Install location |
| `DATA_DIR` | `/var/lib/netconfig` | Where backups are stored |
| `WEB_CONCURRENCY` | `2` | uvicorn worker processes |
| `CELERY_CONCURRENCY` | `2` | Celery worker processes |
| `SKIP_FRONTEND` | `false` | Install the API only, no web UI |

Re-running is safe: it keeps the existing configuration and secrets, updates
the code, rebuilds, and resets the admin password to `ADMIN_PASSWORD`.

## After installing

- Web UI: `http://<container-ip>`
- API docs: `http://<container-ip>/docs`
- Sign in with `admin` / `changeme` — **change it after first login**

nginx serves the compiled UI and proxies `/api/` to the API on the same
origin, so there is one port to expose and no CORS involved.

## Layout

| Path | Contents |
| --- | --- |
| `/opt/netconfig` | Application source and the Python virtualenv |
| `/opt/netconfig/frontend/dist` | Compiled web UI served by nginx |
| `/etc/netconfig-backup/netconfig.env` | Configuration and secrets, `0640 root:netconfig` |
| `/var/lib/netconfig/backups` | Stored device configurations, `0750 netconfig` |
| `/var/lib/netconfig/celerybeat-schedule` | Scheduler state |
| `/etc/nginx/sites-available/netconfig.conf` | Web server configuration |

Not `/etc/netconfig`: that path is already a regular file on every Debian and
Ubuntu system, shipped by `libtirpc-common` as the RPC network configuration
database (`netconfig(5)`).

## Services

```bash
systemctl status netconfig-api          # API (uvicorn)
systemctl status netconfig-worker       # backup worker (Celery)
systemctl status netconfig-beat         # scheduler (Celery beat)

journalctl -u netconfig-api -f          # follow logs
journalctl -u netconfig-worker -f

systemctl restart netconfig-api         # after editing the config file
```

All three read `/etc/netconfig-backup/netconfig.env`, run as the unprivileged
`netconfig` user, and are memory-capped to roughly what the Docker deployment
allows per container (API 768M, worker 1G, beat 256M). The API applies any
pending database migrations on start, so a restart after an update is enough.

## Sizing

2 vCPU / 2 GB RAM / 8 GB disk handles a few hundred devices. Raise
`CELERY_CONCURRENCY` and `MAX_CONCURRENT_BACKUPS` for more; backups are
network-bound rather than CPU-bound, so concurrency matters more than cores.

Stored configurations accumulate in `/var/lib/netconfig/backups`. Retention is
`DEFAULT_RETENTION_DAYS` (90) and identical configurations are not stored
twice, but size the disk for the device count and how often they change.

## Backups of the installation itself

`/etc/netconfig-backup/netconfig.env` holds `ENCRYPTION_KEY`, which decrypts
every stored device password. Losing it makes saved credentials unrecoverable.
Back up that file, the PostgreSQL database, and `/var/lib/netconfig/backups`:

```bash
cp /etc/netconfig-backup/netconfig.env /root/netconfig.env.bak
sudo -u postgres pg_dump netconfig | gzip > /root/netconfig-db.sql.gz
tar czf /root/netconfig-configs.tar.gz -C /var/lib/netconfig backups
```

On Proxmox, a container backup (`vzdump`) covers all three at once.

## Updating

```bash
cd /opt/netconfig && sudo ./lxc/install.sh
```

Pulls the latest code, reinstalls dependencies, rebuilds the UI, migrates and
restarts. Secrets and stored backups are left alone.

## Troubleshooting

**API will not start** — `journalctl -u netconfig-api -n 50`. Usually the
database is unreachable or `netconfig.env` has a malformed line: values are
literal `KEY=value`, with no quoting and no spaces around the `=`.

**502 from nginx** — the API is down; check the service above.

**403 on the UI** — nginx (running as `www-data`) needs traversal on
`/opt/netconfig` and `/opt/netconfig/frontend` and read access to
`frontend/dist`. Re-running the installer restores these.

**Frontend build runs out of memory** in a small container: give it more RAM
temporarily, or install with `SKIP_FRONTEND=true` and build elsewhere.

**Scheduled backups do not run** — beat queues them and the worker executes
them, so both must be up:
`systemctl status netconfig-beat netconfig-worker`.

### The Proxmox host script

**It stops at "Selecting storage"** — `pvesm status` probes every configured
storage, so one unreachable NFS or CIFS share blocks it. The script now times
out and says so. Name the storages explicitly to skip the probe:

```bash
STORAGE=local-lvm TEMPLATE_STORAGE=local bash -c "$(curl -fsSL .../lxc/proxmox-create-lxc.sh)"
```

**It stops while downloading the template** — that is a ~130 MB pull from
download.proxmox.com and its progress is now shown. If the host cannot reach
it, fetch a template by hand and re-run:

```bash
pveam update && pveam download local debian-12-standard_12.7-1_amd64.tar.zst
```

**"never got an IP address"** — no DHCP server on `$BRIDGE`. Use a static
address: `IPCONFIG=192.168.1.50/24,gw=192.168.1.1`.

**"cannot resolve deb.debian.org"** — the container has an address but no
working resolver, which would otherwise hang the first `apt-get` for a very
long time. Re-run with `NAMESERVER=1.1.1.1`, or fix an existing container with
`pct set <ctid> --nameserver 1.1.1.1`.

**Starting over** — the container is left in place on failure so you can look
at it. Remove it with `pct stop <ctid> && pct destroy <ctid>`.
