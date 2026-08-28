"""Hardware inventory and environmental readings polled over SNMP

Three tables:

- device_components: what a device is made of, with a serial number per part.
  Aged rather than deleted, because "the power supply that used to be in slot
  B" is the question a serial-number record exists to answer.
- device_sensors: the current reading per sensor, upserted, so "what is this
  device doing right now" is one indexed query.
- sensor_readings: the history behind the charts, and the only table here that
  grows without bound - hence the index the retention sweep deletes by.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'device_components',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('entity_index', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('component_class', sa.String(length=20), nullable=False,
                  server_default='unknown'),
        sa.Column('model_name', sa.String(length=128), nullable=True),
        sa.Column('serial_number', sa.String(length=128), nullable=True),
        sa.Column('hardware_rev', sa.String(length=64), nullable=True),
        sa.Column('firmware_rev', sa.String(length=64), nullable=True),
        sa.Column('software_rev', sa.String(length=128), nullable=True),
        sa.Column('parent_index', sa.String(length=32), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('first_seen', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'entity_index',
                            name='uq_component_device_index'),
    )
    op.create_index('ix_device_components_id', 'device_components', ['id'])
    op.create_index('ix_device_components_serial_number', 'device_components',
                    ['serial_number'])
    op.create_index('ix_components_org_class', 'device_components',
                    ['organization_id', 'component_class'])
    op.create_index('ix_components_device_active', 'device_components',
                    ['device_id', 'is_active'])

    op.create_table(
        'device_sensors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.Integer(), nullable=False),
        sa.Column('sensor_key', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('sensor_type', sa.String(length=20), nullable=False),
        sa.Column('unit', sa.String(length=16), nullable=False, server_default=''),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='ok'),
        sa.Column('source', sa.String(length=32), nullable=False,
                  server_default='entity-sensor'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('first_seen', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('last_reading_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'sensor_key', name='uq_sensor_device_key'),
    )
    op.create_index('ix_device_sensors_id', 'device_sensors', ['id'])
    op.create_index('ix_device_sensors_sensor_type', 'device_sensors', ['sensor_type'])
    op.create_index('ix_sensors_org_type', 'device_sensors',
                    ['organization_id', 'sensor_type'])
    op.create_index('ix_sensors_device_active', 'device_sensors',
                    ['device_id', 'is_active'])

    op.create_table(
        'sensor_readings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sensor_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='ok'),
        sa.Column('recorded_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['sensor_id'], ['device_sensors.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sensor_readings_id', 'sensor_readings', ['id'])
    # Every chart asks for one sensor over a window and the retention sweep
    # deletes by age; this serves both.
    op.create_index('ix_readings_sensor_time', 'sensor_readings',
                    ['sensor_id', 'recorded_at'])


def downgrade() -> None:
    op.drop_table('sensor_readings')
    op.drop_table('device_sensors')
    op.drop_table('device_components')
