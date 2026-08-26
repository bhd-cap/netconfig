# BlackHawk NetConfig

Professional Network Configuration Management - A multi-tenant, web-based network configuration backup system supporting Cisco, Arista, Fortinet, Juniper, Aruba, and HPE devices.

**Developed by BlackHawk Data**
Website: [blackhawk11.com](https://blackhawk11.com)
Email: [info@blackhawk11.com](mailto:info@blackhawk11.com)

## Features

- **Multi-Vendor Support**: Cisco IOS/IOS-XE/NX-OS, Arista EOS, Fortinet FortiOS, Juniper JunOS, Aruba ArubaOS, HPE Comware/ProCurve
- **Web Interface**: User-friendly dashboard for device management and backup operations
- **Automated Backups**: Schedule backups with cron-like syntax
- **Configuration Comparison**: Side-by-side diff viewer with syntax highlighting
- **Multi-Tenancy**: Organization-based isolation for enterprise deployments
- **Secure**: JWT authentication, encrypted credentials, comprehensive audit logging
- **Scalable**: Handles 100-1000 devices with concurrent backup support

## Quick Start

### Prerequisites

For the Docker deployment:

- Docker 20.10+
- Docker Compose 2.0+
- 4GB RAM minimum
- 50GB disk space

For the [LXC deployment](lxc/README.md):

- Debian 11/12 or Ubuntu 22.04/24.04 with systemd
- 2 vCPU / 2GB RAM / 8GB disk minimum

### Installation (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/install.sh | bash
```

The installer checks Docker, fetches the source, generates a `.env` with a
random database password, JWT secret and encryption key, builds the images,
waits for the API to report healthy, applies migrations, and creates the
admin user.

Already have a checkout? Run it from inside the repository:

```bash
./install.sh
```

Then open:

- Web UI: http://localhost:3000
- API documentation: http://localhost:8000/docs

Sign in with:

- Username: `admin`
- Password: `changeme` — **temporary, change it after first login**

Back up the generated `.env`. Its `ENCRYPTION_KEY` decrypts every stored
device password; if you lose it, saved credentials cannot be recovered.

#### Installer options

| Variable | Default | Purpose |
| --- | --- | --- |
| `INSTALL_DIR` | `./netconfig` | Where to install |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `changeme` | Initial admin account |
| `FRONTEND_PORT` / `BACKEND_PORT` | `3000` / `8000` | Published ports |
| `WITH_MONITORING` | `false` | Also start Flower on `FLOWER_PORT` |
| `SKIP_BUILD` | `false` | Reuse existing images |
| `BRANCH` | `main` | Branch to clone |

```bash
INSTALL_DIR=/opt/netconfig WITH_MONITORING=true ./install.sh
```

### Install into an LXC container instead (no Docker)

Runs natively under systemd - PostgreSQL, Redis, nginx and the application as
ordinary services. On a Proxmox VE host, this creates the container for you:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/proxmox-create-lxc.sh)"
```

Inside an existing Debian/Ubuntu container, VM or server:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/bhd-cap/netconfig/main/lxc/install.sh)"
```

The UI and API are served from one origin on port 80. See
[lxc/README.md](lxc/README.md) for options, layout and operations.

### Manual installation

```bash
cp .env.example .env
# Set SECRET_KEY, and generate ENCRYPTION_KEY with:
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d --build
docker compose exec backend python init_db.py
```

### Optional services

Both are off by default so they cost nothing when unused:

```bash
docker compose --profile monitoring up -d   # Flower task monitor, :5555
docker compose --profile proxy up -d        # single-origin TLS proxy, :80/:443
```

## Architecture

```
┌─────────────┐
│   Nginx     │  :80, :443  (optional --profile proxy)
│ (Reverse    │
│   Proxy)    │
└──────┬──────┘
       │
   ┌───┴────────────────┐
   │                    │
┌──▼──────┐      ┌─────▼──────┐
│ React   │      │  FastAPI   │
│ (static │      │  Backend   │
│  nginx) │      │   :8000    │
│  :3000  │      │            │
└─────────┘      └─────┬──────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
     ┌────▼───┐   ┌───▼───┐   ┌───▼────┐
     │Postgres│   │ Redis │   │ Celery │
     │  :5432 │   │ :6379 │   │Workers │
     └────────┘   └───────┘   └────────┘
```

## Usage

### Adding Devices

**Manual Entry**:
1. Navigate to "Devices" → "Add Device"
2. Enter device details (hostname, IP, credentials, device type)
3. Click "Test Connection" to verify
4. Save the device

**Bulk Upload (CSV)**:
1. Download the CSV template from "Devices" → "Bulk Upload"
2. Fill in device details
3. Upload the CSV file
4. Review and confirm imports

### Running Backups

**Manual Backup**:
- Select device(s) and click "Backup Now"
- Monitor progress in real-time

**Scheduled Backups**:
1. Navigate to "Jobs" → "Create Schedule"
2. Define cron schedule (e.g., `0 2 * * *` for 2 AM daily)
3. Select devices or use filters
4. Save the schedule

### Comparing Configurations

1. Navigate to "Backups" → select a device
2. Choose two configurations to compare
3. View side-by-side diff with highlighted changes

## CSV Upload Format

```csv
hostname,ip_address,device_type,username,password,port,description,location
router-nyc-01,192.168.1.1,cisco_ios,admin,P@ssw0rd,22,NYC Core Router,New York DC
switch-la-01,192.168.1.10,arista_eos,admin,P@ssw0rd,22,LA Access Switch,Los Angeles DC
fw-chi-01,192.168.1.20,fortinet,admin,P@ssw0rd,22,Chicago Firewall,Chicago DC
```

## Supported Device Types

| Vendor | Device Type | Command |
|--------|------------|---------|
| Cisco IOS | `cisco_ios` | `show running-config` |
| Cisco IOS-XE | `cisco_ios_xe` | `show running-config` |
| Cisco NX-OS | `cisco_nxos` | `show running-config` |
| Arista EOS | `arista_eos` | `show running-config` |
| Fortinet | `fortinet` | `show full-configuration` |
| Juniper JunOS | `juniper_junos` | `show configuration \| display set` |
| Aruba OS | `aruba_os` | `show running-config` |
| HPE Comware | `hp_comware` | `display current-configuration` |
| HPE ProCurve | `hp_procurve` | `show running-config` |

## Configuration

Key environment variables in `.env`:

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `SECRET_KEY`: JWT secret key
- `ENCRYPTION_KEY`: Fernet encryption key for credentials
- `BACKUP_BASE_PATH`: File storage path for configurations
- `DEFAULT_RETENTION_DAYS`: Configuration retention policy (default: 90)

## Backup Storage

Configurations are stored at:
```
/backups/{organization_id}/{hostname}/{hostname}_{timestamp}.cfg
```

Example:
```
/backups/1/router-nyc-01/router-nyc-01_20250131_143022.cfg
```

## Maintenance

### Database Backup
```bash
docker-compose exec postgres pg_dump -U netbackup netbackup > backup.sql
```

### View Logs
```bash
docker-compose logs -f backend
docker-compose logs -f celery_worker
```

### Update Application
```bash
git pull
docker-compose down
docker-compose up -d --build
docker-compose exec backend alembic upgrade head
```

## Troubleshooting

### Device Connection Failures
- Verify network connectivity: `ping <device-ip>`
- Check SSH port: `telnet <device-ip> 22`
- Verify credentials and device type
- Check firewall rules

### Backup Task Stuck
- Check Celery worker logs: `docker-compose logs celery_worker`
- Restart workers: `docker-compose restart celery_worker`
- Monitor with Flower: http://localhost:5555

### Database Issues
- Check PostgreSQL logs: `docker-compose logs postgres`
- Verify connection: `docker-compose exec postgres psql -U netbackup`

## Security Best Practices

1. **Change default credentials** immediately after installation
2. **Use strong encryption keys** (generate with cryptography library)
3. **Enable HTTPS** in production (configure nginx with SSL certificates)
4. **Restrict network access** to the application
5. **Regular backups** of database and configuration files
6. **Update dependencies** regularly for security patches

## Documentation

- [User Guide](docs/user-guide.md)
- [Administrator Guide](docs/admin-guide.md)
- [API Reference](http://localhost:8000/docs)
- [Troubleshooting Guide](docs/troubleshooting.md)

## Development

See [Development Guide](docs/development.md) for local development setup and contribution guidelines.

## License

Copyright © 2025. All rights reserved.

## Support

For issues and feature requests, please contact your system administrator.
