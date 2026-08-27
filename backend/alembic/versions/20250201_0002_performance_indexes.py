"""Composite indexes for the hot query paths

Every index here backs a query the application runs on a schedule or on every
page load. The single-column indexes from the initial migration cannot serve
these: Postgres can only use one of them and then has to sort or filter the
rest of the matches in memory.

Revision ID: 0002
Revises: 0001
Create Date: 2025-02-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backup history for one device, newest first. Serves the paginated
    # backup list, "latest configuration", the latest-hash lookup used for
    # deduplication, and the retention delete - all of which order by
    # backed_up_at DESC within a device.
    op.create_index(
        'ix_configurations_device_backed_up_at',
        'configurations',
        ['device_id', 'backed_up_at'],
        unique=False,
        postgresql_ops={'backed_up_at': 'DESC'},
    )

    # Deduplication lookup by (hash, device).
    op.create_index(
        'ix_configurations_device_config_hash',
        'configurations',
        ['device_id', 'config_hash'],
        unique=False,
    )

    # Tenant-scoped device listing, which orders by hostname.
    op.create_index(
        'ix_devices_organization_hostname',
        'devices',
        ['organization_id', 'hostname'],
        unique=False,
    )

    # Active devices per tenant, read by every scheduled job run.
    op.create_index(
        'ix_devices_organization_is_active',
        'devices',
        ['organization_id', 'is_active'],
        unique=False,
    )

    # Tenant-scoped duplicate check on device creation and bulk upload.
    op.create_index(
        'ix_devices_organization_ip_address',
        'devices',
        ['organization_id', 'ip_address'],
        unique=False,
    )

    # Due-job scan: runs once a minute forever, and is partial so the index
    # only holds enabled jobs.
    op.create_index(
        'ix_backup_jobs_due',
        'backup_jobs',
        ['next_run_at'],
        unique=False,
        postgresql_where=sa.text('is_enabled'),
    )

    # Audit log reads are always "most recent first", optionally per user or
    # per resource.
    op.create_index(
        'ix_audit_logs_timestamp_desc',
        'audit_logs',
        ['timestamp'],
        unique=False,
        postgresql_ops={'timestamp': 'DESC'},
    )

    op.create_index(
        'ix_audit_logs_resource',
        'audit_logs',
        ['resource_type', 'resource_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_audit_logs_resource', table_name='audit_logs')
    op.drop_index('ix_audit_logs_timestamp_desc', table_name='audit_logs')
    op.drop_index('ix_backup_jobs_due', table_name='backup_jobs')
    op.drop_index('ix_devices_organization_ip_address', table_name='devices')
    op.drop_index('ix_devices_organization_is_active', table_name='devices')
    op.drop_index('ix_devices_organization_hostname', table_name='devices')
    op.drop_index('ix_configurations_device_config_hash', table_name='configurations')
    op.drop_index('ix_configurations_device_backed_up_at', table_name='configurations')
