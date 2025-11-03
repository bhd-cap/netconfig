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

- Docker 20.10+
- Docker Compose 2.0+
- 4GB RAM minimum
- 50GB disk space

### Installation

1. **Clone the repository**:
```bash
git clone https://github.com/yourorg/netconfig-backup.git
cd netconfig-backup
```

2. **Configure environment variables**:
```bash
cp .env.example .env
nano .env
```

3. **Generate encryption keys**:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Add the generated key to `.env` as `ENCRYPTION_KEY`

4. **Deploy the application**:
```bash
docker-compose up -d
```

5. **Access the application**:
- Web UI: http://localhost
- API Documentation: http://localhost:8000/docs
- Celery Flower (Task Monitor): http://localhost:5555

6. **Login with default credentials**:
- Username: `admin`
- Password: `changeme` (change this immediately!)

## Architecture

```
┌─────────────┐
│   Nginx     │  :80, :443
│ (Reverse    │
│   Proxy)    │
└──────┬──────┘
       │
   ┌───┴────────────────┐
   │                    │
┌──▼──────┐      ┌─────▼──────┐
│ React   │      │  FastAPI   │
│Frontend │      │  Backend   │
│  :3000  │      │   :8000    │
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
