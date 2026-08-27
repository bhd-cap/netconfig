"""Keep inventory and adjacency history when a device is deleted

neighbors.device_id and host_inventory.device_id both cascaded, so deleting a
switch erased the record of what had been plugged into it and what it had been
cabled to. Removing a device from the backup list is not a statement about the
hosts that were on its ports.

Both become nullable with ON DELETE SET NULL, and each row keeps the switch
hostname it was seen on so an orphaned row still reads sensibly.

Revision ID: 0005
Revises: 0004
Create Date: 2025-03-16

"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------- host_inventory
    op.add_column(
        'host_inventory',
        sa.Column('device_hostname', sa.String(length=255), nullable=True),
    )
    # Fill it in for what is already there, so existing rows survive a later
    # device deletion with the switch name intact.
    op.execute(
        """
        UPDATE host_inventory h
           SET device_hostname = d.hostname
          FROM devices d
         WHERE d.id = h.device_id
           AND h.device_hostname IS NULL
        """
    )

    op.alter_column('host_inventory', 'device_id', nullable=True)
    op.drop_constraint(
        'host_inventory_device_id_fkey', 'host_inventory', type_='foreignkey'
    )
    op.create_foreign_key(
        'host_inventory_device_id_fkey',
        'host_inventory',
        'devices',
        ['device_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # --------------------------------------------------------------- neighbors
    op.add_column(
        'neighbors',
        sa.Column('device_hostname', sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE neighbors n
           SET device_hostname = d.hostname
          FROM devices d
         WHERE d.id = n.device_id
           AND n.device_hostname IS NULL
        """
    )

    op.alter_column('neighbors', 'device_id', nullable=True)
    op.drop_constraint('neighbors_device_id_fkey', 'neighbors', type_='foreignkey')
    op.create_foreign_key(
        'neighbors_device_id_fkey',
        'neighbors',
        'devices',
        ['device_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # Rows orphaned while 0005 was applied cannot be restored to a device, and
    # device_id is NOT NULL again below, so they have to go. Recorded here
    # because it is the one lossy step in the chain.
    op.execute("DELETE FROM neighbors WHERE device_id IS NULL")
    op.execute("DELETE FROM host_inventory WHERE device_id IS NULL")

    op.drop_constraint('neighbors_device_id_fkey', 'neighbors', type_='foreignkey')
    op.create_foreign_key(
        'neighbors_device_id_fkey',
        'neighbors',
        'devices',
        ['device_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.alter_column('neighbors', 'device_id', nullable=False)
    op.drop_column('neighbors', 'device_hostname')

    op.drop_constraint(
        'host_inventory_device_id_fkey', 'host_inventory', type_='foreignkey'
    )
    op.create_foreign_key(
        'host_inventory_device_id_fkey',
        'host_inventory',
        'devices',
        ['device_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.alter_column('host_inventory', 'device_id', nullable=False)
    op.drop_column('host_inventory', 'device_hostname')
