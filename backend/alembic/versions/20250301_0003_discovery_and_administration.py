"""Discovery, inventory, diagrams, roles, settings and remote backup targets

Adds everything the network discovery and administration features need:

* neighbors            - LLDP/CDP adjacencies with first/last seen
* host_inventory       - MACs seen on switch ports, with first/last seen
* oui_vendors          - IEEE OUI prefix to vendor name
* topology_diagrams    - saved, user-edited diagram layouts
* discovery_runs       - history of discovery crawls
* roles                - named permission sets
* app_settings         - per-organization retention, email, schedule, windows
* backup_targets       - SFTP/FTP destinations for stored configurations

and the new columns on devices (transport, SNMP credentials, discovery
provenance) and users (role, password reset bookkeeping).

Revision ID: 0003
Revises: 0002
Create Date: 2025-03-01

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------- roles
    op.create_table(
        'roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('permissions', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='[]'),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_role_name_per_org'),
    )
    op.create_index('ix_roles_org', 'roles', ['organization_id'])
    op.create_index(op.f('ix_roles_id'), 'roles', ['id'])

    # ------------------------------------------------------- users: role FK
    op.add_column('users', sa.Column('role_id', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('full_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('must_change_password', sa.Boolean(), nullable=False,
                                     server_default='false'))
    op.add_column('users', sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_users_role_id', 'users', 'roles', ['role_id'], ['id'],
                          ondelete='SET NULL')

    # ------------------------------------------------- devices: transports
    op.add_column('devices', sa.Column('transport', sa.String(length=10), nullable=False,
                                       server_default='ssh'))
    op.add_column('devices', sa.Column('snmp_version', sa.String(length=5), nullable=True))
    op.add_column('devices', sa.Column('snmp_community', sa.Text(), nullable=True))
    op.add_column('devices', sa.Column('snmp_port', sa.Integer(), nullable=False,
                                       server_default='161'))
    op.add_column('devices', sa.Column('snmp_v3_user', sa.String(length=100), nullable=True))
    op.add_column('devices', sa.Column('snmp_v3_auth_key', sa.Text(), nullable=True))
    op.add_column('devices', sa.Column('snmp_v3_priv_key', sa.Text(), nullable=True))
    op.add_column('devices', sa.Column('snmp_v3_auth_protocol', sa.String(length=20),
                                       nullable=True))
    op.add_column('devices', sa.Column('snmp_v3_priv_protocol', sa.String(length=20),
                                       nullable=True))
    op.add_column('devices', sa.Column('discovered', sa.Boolean(), nullable=False,
                                       server_default='false'))
    op.add_column('devices', sa.Column('discovery_source', sa.String(length=255), nullable=True))
    op.add_column('devices', sa.Column('last_discovered_at', sa.DateTime(timezone=True),
                                       nullable=True))

    # ------------------------------------------------------------ neighbors
    op.create_table(
        'neighbors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('local_interface', sa.String(length=100), nullable=False),
        sa.Column('remote_hostname', sa.String(length=255), nullable=False),
        # Not nullable: this is part of the upsert key, and NULLs never
        # compare equal, so a nullable column would defeat ON CONFLICT and
        # let the same link insert over and over. '' means not reported.
        sa.Column('remote_interface', sa.String(length=100), nullable=False,
                  server_default=''),
        sa.Column('remote_platform', sa.Text(), nullable=True),
        sa.Column('remote_mgmt_ip', sa.String(length=45), nullable=True),
        sa.Column('remote_chassis_id', sa.String(length=64), nullable=True),
        sa.Column('capabilities', sa.String(length=255), nullable=True),
        sa.Column('protocol', sa.String(length=10), nullable=False, server_default='lldp'),
        sa.Column('remote_device_id', sa.Integer(), nullable=True),
        sa.Column('first_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['remote_device_id'], ['devices.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'local_interface', 'remote_hostname',
                            'remote_interface', name='uq_neighbor_link'),
    )
    op.create_index('ix_neighbors_org_active', 'neighbors', ['organization_id', 'is_active'])
    op.create_index('ix_neighbors_device', 'neighbors', ['device_id'])
    op.create_index('ix_neighbors_remote_device', 'neighbors', ['remote_device_id'])
    op.create_index(op.f('ix_neighbors_id'), 'neighbors', ['id'])

    # ------------------------------------------------------- host inventory
    op.create_table(
        'host_inventory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('interface', sa.String(length=100), nullable=False),
        sa.Column('mac_address', sa.String(length=17), nullable=False),
        # Not nullable for the same reason as neighbors.remote_interface.
        # 0 means the device did not report a VLAN.
        sa.Column('vlan', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('entry_type', sa.String(length=20), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('vendor', sa.String(length=255), nullable=True),
        sa.Column('first_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'interface', 'mac_address', 'vlan',
                            name='uq_host_on_port'),
    )
    op.create_index('ix_host_inventory_org_last_seen', 'host_inventory',
                    ['organization_id', 'last_seen'])
    op.create_index('ix_host_inventory_mac', 'host_inventory', ['mac_address'])
    op.create_index('ix_host_inventory_device_iface', 'host_inventory',
                    ['device_id', 'interface'])
    op.create_index('ix_host_inventory_org_vendor', 'host_inventory',
                    ['organization_id', 'vendor'])
    op.create_index(op.f('ix_host_inventory_id'), 'host_inventory', ['id'])

    # ---------------------------------------------------------- OUI vendors
    op.create_table(
        'oui_vendors',
        sa.Column('oui', sa.String(length=6), nullable=False),
        sa.Column('vendor_name', sa.String(length=255), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('oui'),
    )

    # ----------------------------------------------------- topology diagrams
    op.create_table(
        'topology_diagrams',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('layout', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='{}'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_diagram_name_per_org'),
    )
    op.create_index('ix_topology_diagrams_org', 'topology_diagrams', ['organization_id'])
    op.create_index(op.f('ix_topology_diagrams_id'), 'topology_diagrams', ['id'])

    # ------------------------------------------------------- discovery runs
    op.create_table(
        'discovery_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('seed_device_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
        sa.Column('max_hops', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('devices_probed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('neighbors_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hosts_found', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('devices_created', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  server_default='{}'),
        sa.Column('triggered_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['seed_device_id'], ['devices.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_discovery_runs_org_started', 'discovery_runs',
                    ['organization_id', 'started_at'])
    op.create_index(op.f('ix_discovery_runs_id'), 'discovery_runs', ['id'])

    # --------------------------------------------------------- app settings
    op.create_table(
        'app_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False, server_default='90'),
        sa.Column('retention_max_per_device', sa.Integer(), nullable=True),
        sa.Column('retention_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('default_schedule_cron', sa.String(length=100), nullable=False,
                  server_default='0 2 * * *'),
        sa.Column('default_schedule_enabled', sa.Boolean(), nullable=False,
                  server_default='false'),
        sa.Column('max_concurrent_backups', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('smtp_host', sa.String(length=255), nullable=True),
        sa.Column('smtp_port', sa.Integer(), nullable=False, server_default='587'),
        sa.Column('smtp_username', sa.String(length=255), nullable=True),
        sa.Column('smtp_password_encrypted', sa.Text(), nullable=True),
        sa.Column('smtp_use_tls', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('smtp_from_address', sa.String(length=255), nullable=True),
        sa.Column('notifications_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notify_recipients', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='[]'),
        sa.Column('notify_on_backup_failure', sa.Boolean(), nullable=False,
                  server_default='true'),
        sa.Column('notify_on_backup_success', sa.Boolean(), nullable=False,
                  server_default='false'),
        sa.Column('notify_on_config_change', sa.Boolean(), nullable=False,
                  server_default='true'),
        sa.Column('notify_on_new_host', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('maintenance_windows', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False, server_default='[]'),
        sa.Column('maintenance_timezone', sa.String(length=64), nullable=False,
                  server_default='UTC'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id'),
    )
    op.create_index(op.f('ix_app_settings_id'), 'app_settings', ['id'])

    # ------------------------------------------------------- backup targets
    op.create_table(
        'backup_targets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('protocol', sa.String(length=10), nullable=False, server_default='sftp'),
        sa.Column('host', sa.String(length=255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False, server_default='22'),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('encrypted_password', sa.Text(), nullable=True),
        sa.Column('private_key', sa.Text(), nullable=True),
        sa.Column('private_key_passphrase', sa.Text(), nullable=True),
        sa.Column('remote_path', sa.Text(), nullable=False, server_default='/'),
        sa.Column('use_device_subdirectories', sa.Boolean(), nullable=False,
                  server_default='true'),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('upload_on_backup', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('verify_host_key', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('known_host_key', sa.Text(), nullable=True),
        sa.Column('last_status', sa.String(length=20), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('uploads_succeeded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('uploads_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'name', name='uq_backup_target_name'),
    )
    op.create_index('ix_backup_targets_org_enabled', 'backup_targets',
                    ['organization_id', 'is_enabled'])
    op.create_index(op.f('ix_backup_targets_id'), 'backup_targets', ['id'])


def downgrade() -> None:
    op.drop_table('backup_targets')
    op.drop_table('app_settings')
    op.drop_table('discovery_runs')
    op.drop_table('topology_diagrams')
    op.drop_table('oui_vendors')
    op.drop_table('host_inventory')
    op.drop_table('neighbors')

    for column in (
        'last_discovered_at', 'discovery_source', 'discovered',
        'snmp_v3_priv_protocol', 'snmp_v3_auth_protocol', 'snmp_v3_priv_key',
        'snmp_v3_auth_key', 'snmp_v3_user', 'snmp_port', 'snmp_community',
        'snmp_version', 'transport',
    ):
        op.drop_column('devices', column)

    op.drop_constraint('fk_users_role_id', 'users', type_='foreignkey')
    for column in (
        'deactivated_at', 'last_login_at', 'must_change_password', 'full_name', 'role_id',
    ):
        op.drop_column('users', column)

    op.drop_table('roles')
