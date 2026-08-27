"""Credential vault, probe results, device facts and discovered hostnames

* credentials        - ordered CLI and SNMP credential sets discovery tries
* device_probes      - the last outcome per device per transport
* devices            - authentication state, the credential that worked, and
                       the facts learned from the device itself
* host_inventory     - the hostname LLDP/CDP announced for a MAC, kept apart
                       from the one a person typed

Revision ID: 0004
Revises: 0003
Create Date: 2025-03-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----------------------------------------------------------------- vault
    op.create_table(
        'credentials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('kind', sa.String(length=10), nullable=False,
                  server_default='cli'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('is_enabled', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),

        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('encrypted_password', sa.Text(), nullable=True),
        sa.Column('encrypted_enable_secret', sa.Text(), nullable=True),
        sa.Column('ssh_key_path', sa.Text(), nullable=True),

        sa.Column('snmp_version', sa.String(length=5), nullable=True),
        sa.Column('encrypted_community', sa.Text(), nullable=True),
        sa.Column('snmp_v3_user', sa.String(length=100), nullable=True),
        sa.Column('encrypted_v3_auth_key', sa.Text(), nullable=True),
        sa.Column('encrypted_v3_priv_key', sa.Text(), nullable=True),
        sa.Column('snmp_v3_auth_protocol', sa.String(length=20), nullable=True),
        sa.Column('snmp_v3_priv_protocol', sa.String(length=20), nullable=True),

        sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failure_at', sa.DateTime(timezone=True), nullable=True),

        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),

        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name',
                            name='uq_credential_name_per_org'),
    )
    op.create_index('ix_credentials_id', 'credentials', ['id'])
    op.create_index(
        'ix_credentials_org_kind_priority',
        'credentials',
        ['organization_id', 'kind', 'priority'],
    )

    # ---------------------------------------------------------------- probes
    op.create_table(
        'device_probes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('transport', sa.String(length=10), nullable=False),
        sa.Column('result', sa.String(length=20), nullable=False),
        sa.Column('credential_id', sa.Integer(), nullable=True),
        sa.Column('credential_name', sa.String(length=100), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('duration', sa.Integer(), nullable=True),
        sa.Column('probed_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),

        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['credential_id'], ['credentials.id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'transport',
                            name='uq_probe_device_transport'),
    )
    op.create_index('ix_device_probes_id', 'device_probes', ['id'])
    op.create_index('ix_device_probes_org', 'device_probes', ['organization_id'])

    # --------------------------------------------------------------- devices
    op.add_column(
        'devices',
        sa.Column('last_auth_status', sa.String(length=20), nullable=False,
                  server_default='never'),
    )
    op.add_column('devices', sa.Column('last_auth_at', sa.DateTime(timezone=True),
                                       nullable=True))
    op.add_column('devices', sa.Column('auth_error', sa.Text(), nullable=True))
    op.add_column('devices', sa.Column('credential_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_devices_credential', 'devices', 'credentials',
        ['credential_id'], ['id'], ondelete='SET NULL',
    )

    op.add_column('devices', sa.Column('model', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('serial_number', sa.String(length=255),
                                       nullable=True))
    op.add_column('devices', sa.Column('os_version', sa.String(length=255),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_sysname', sa.String(length=255),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_sysdescr', sa.Text(), nullable=True))
    op.add_column('devices', sa.Column('snmp_location', sa.String(length=255),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_contact', sa.String(length=255),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_uptime_seconds', sa.BigInteger(),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_last_polled_at',
                                       sa.DateTime(timezone=True), nullable=True))
    op.add_column('devices', sa.Column('discovered_facts',
                                       postgresql.JSONB(astext_type=sa.Text()),
                                       nullable=True))

    # Devices that already exist were entered by hand with credentials that
    # presumably work, so they keep their eligibility rather than being
    # demoted to 'never' and dropping off every backup schedule.
    op.execute(
        """
        UPDATE devices
           SET last_auth_status = 'success'
         WHERE last_backup_status = 'success'
        """
    )

    # The Devices page filters and sorts on this constantly.
    op.create_index(
        'ix_devices_org_auth', 'devices', ['organization_id', 'last_auth_status']
    )

    # -------------------------------------------------------------- inventory
    op.add_column(
        'host_inventory',
        sa.Column('discovered_hostname', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'host_inventory',
        sa.Column('discovered_via', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'host_inventory',
        sa.Column('discovered_platform', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('host_inventory', 'discovered_platform')
    op.drop_column('host_inventory', 'discovered_via')
    op.drop_column('host_inventory', 'discovered_hostname')

    op.drop_index('ix_devices_org_auth', table_name='devices')

    op.drop_column('devices', 'discovered_facts')
    op.drop_column('devices', 'snmp_last_polled_at')
    op.drop_column('devices', 'snmp_uptime_seconds')
    op.drop_column('devices', 'snmp_contact')
    op.drop_column('devices', 'snmp_location')
    op.drop_column('devices', 'snmp_sysdescr')
    op.drop_column('devices', 'snmp_sysname')
    op.drop_column('devices', 'os_version')
    op.drop_column('devices', 'serial_number')
    op.drop_column('devices', 'model')

    op.drop_constraint('fk_devices_credential', 'devices', type_='foreignkey')
    op.drop_column('devices', 'credential_id')
    op.drop_column('devices', 'auth_error')
    op.drop_column('devices', 'last_auth_at')
    op.drop_column('devices', 'last_auth_status')

    op.drop_index('ix_device_probes_org', table_name='device_probes')
    op.drop_index('ix_device_probes_id', table_name='device_probes')
    op.drop_table('device_probes')

    op.drop_index('ix_credentials_org_kind_priority', table_name='credentials')
    op.drop_index('ix_credentials_id', table_name='credentials')
    op.drop_table('credentials')
