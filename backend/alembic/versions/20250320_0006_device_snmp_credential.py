"""Let a device draw its SNMP credential from the vault too

A device could already point at a vault credential through devices.credential_id,
which discovery sets to whichever CLI login worked. That covers SSH and telnet,
but a device polled over SNMP had nowhere to point: its community lived only in
its own snmp_community column, so rotating a community meant editing every
device that used it.

The two are separate columns rather than one, because a device commonly needs
both at once - SSH for the configuration backup and SNMP for inventory - and
the vault keeps its cli and snmp entries apart for the same reason.

ON DELETE SET NULL matches credential_id. The API refuses to delete a
credential a device depends on, so this is the backstop for a row removed
underneath the application rather than the expected path: a device that loses
its reference falls back to whatever is stored on the device itself.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A device that logs in with a vault credential holds no username or
    # password of its own - that is the point, one place to rotate it - and an
    # SNMP-only device is never logged into at all. Both columns were NOT NULL
    # from a time when every device carried its own login.
    op.alter_column('devices', 'username', existing_type=sa.String(100), nullable=True)
    op.alter_column('devices', 'encrypted_password', existing_type=sa.Text(), nullable=True)

    op.add_column(
        'devices',
        sa.Column('snmp_credential_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_devices_snmp_credential_id',
        'devices',
        'credentials',
        ['snmp_credential_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Looking up "which devices use this credential" happens on every attempt
    # to delete or edit one, and has to be quick enough to run inline.
    op.create_index(
        'ix_devices_snmp_credential_id',
        'devices',
        ['snmp_credential_id'],
    )
    op.create_index(
        'ix_devices_credential_id',
        'devices',
        ['credential_id'],
    )


def downgrade() -> None:
    # Re-adding NOT NULL needs the nulls filled, and there is nothing correct
    # to fill them with: the credentials live in the vault, which the older
    # code does not read for logins. Devices that used a vault credential are
    # left with empty ones and will fail to authenticate until someone enters
    # a login - the one lossy step in this downgrade, and the reason it is
    # spelled out here rather than left to be discovered.
    op.execute("UPDATE devices SET username = '' WHERE username IS NULL")
    op.execute(
        "UPDATE devices SET encrypted_password = '' WHERE encrypted_password IS NULL"
    )
    op.alter_column('devices', 'username', existing_type=sa.String(100), nullable=False)
    op.alter_column(
        'devices', 'encrypted_password', existing_type=sa.Text(), nullable=False
    )

    op.drop_index('ix_devices_credential_id', table_name='devices')
    op.drop_index('ix_devices_snmp_credential_id', table_name='devices')
    op.drop_constraint('fk_devices_snmp_credential_id', 'devices', type_='foreignkey')
    op.drop_column('devices', 'snmp_credential_id')
