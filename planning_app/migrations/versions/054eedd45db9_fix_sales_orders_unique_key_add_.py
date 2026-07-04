"""fix sales_orders unique key add assembly_seq

Revision ID: 054eedd45db9
Revises: 6a3ec5fd7880
Create Date: 2026-07-04 21:13:27.796032

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '054eedd45db9'
down_revision = '6a3ec5fd7880'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite can't alter constraints — drop and recreate with correct unique key.
    op.drop_table('sales_orders')
    op.create_table(
        'sales_orders',
        sa.Column('id',          sa.Integer(), nullable=False),
        sa.Column('order_num',   sa.Integer(), nullable=False),
        sa.Column('order_line',  sa.Integer(), nullable=False),
        sa.Column('rel_num',     sa.Integer(), nullable=False),
        sa.Column('assembly_seq',sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_num','order_line','rel_num','assembly_seq',
                            name='uq_so_num_line_rel_asm'),
    )
    # Re-add all other columns via batch_alter (table is empty so safe)
    with op.batch_alter_table('sales_orders') as batch_op:
        for col in [
            sa.Column('po_num',      sa.String(50),  nullable=True),
            sa.Column('open_order',  sa.Boolean(),   nullable=True),
            sa.Column('open_line',   sa.Boolean(),   nullable=True),
            sa.Column('open_release',sa.Boolean(),   nullable=True),
            sa.Column('firm_order',  sa.Boolean(),   nullable=True),
            sa.Column('firm_line',   sa.Boolean(),   nullable=True),
            sa.Column('firm_release',sa.Boolean(),   nullable=True),
            sa.Column('void_line',   sa.Boolean(),   nullable=True),
            sa.Column('order_held',  sa.Boolean(),   nullable=True),
            sa.Column('so_credit_hold',        sa.Boolean(), nullable=True),
            sa.Column('customer_credit_hold',  sa.Boolean(), nullable=True),
            sa.Column('guaranteed_christmas',  sa.Boolean(), nullable=True),
            sa.Column('display_order',         sa.Boolean(), nullable=True),
            sa.Column('order_date',     sa.Date(), nullable=True),
            sa.Column('need_by_date',   sa.Date(), nullable=True),
            sa.Column('req_date',       sa.Date(), nullable=True),
            sa.Column('original_ship_by',     sa.Date(), nullable=True),
            sa.Column('original_need_by',     sa.Date(), nullable=True),
            sa.Column('order_received_date',  sa.Date(), nullable=True),
            sa.Column('customer_delivery_requested', sa.Date(), nullable=True),
            sa.Column('last_xmas_order_date', sa.Date(), nullable=True),
            sa.Column('last_xmas_delivery',   sa.Date(), nullable=True),
            sa.Column('order_ack_sent',        sa.Date(), nullable=True),
            sa.Column('customer_id',      sa.String(20),  nullable=True),
            sa.Column('customer_name',    sa.String(150), nullable=True),
            sa.Column('customer_country', sa.String(100), nullable=True),
            sa.Column('customer_group',   sa.String(50),  nullable=True),
            sa.Column('ship_to_num',      sa.String(20),  nullable=True),
            sa.Column('sales_rep',        sa.String(50),  nullable=True),
            sa.Column('sur_name',         sa.String(100), nullable=True),
            sa.Column('so_type',          sa.String(20),  nullable=True),
            sa.Column('so_type_desc',     sa.String(100), nullable=True),
            sa.Column('channel',          sa.String(50),  nullable=True),
            sa.Column('prod_code',        sa.String(50),  nullable=True),
            sa.Column('entry_person',     sa.String(50),  nullable=True),
            sa.Column('created_by',       sa.String(50),  nullable=True),
            sa.Column('job_num',          sa.String(20),  nullable=True),
            sa.Column('job_released',     sa.Boolean(),   nullable=True),
            sa.Column('job_firm',         sa.Boolean(),   nullable=True),
            sa.Column('prod_plnwk',       sa.String(20),  nullable=True),
            sa.Column('part_num',         sa.String(50),  nullable=True),
            sa.Column('part_desc',        sa.String(255), nullable=True),
            sa.Column('base_part_num',    sa.String(50),  nullable=True),
            sa.Column('xpart_num',        sa.String(50),  nullable=True),
            sa.Column('ium',              sa.String(10),  nullable=True),
            sa.Column('wip_bin',          sa.String(20),  nullable=True),
            sa.Column('model',            sa.String(100), nullable=True),
            sa.Column('size_desc',        sa.String(100), nullable=True),
            sa.Column('prod_size',        sa.String(100), nullable=True),
            sa.Column('cover',            sa.String(50),  nullable=True),
            sa.Column('cover_desc',       sa.String(100), nullable=True),
            sa.Column('leg',              sa.String(100), nullable=True),
            sa.Column('leg_mtl',          sa.String(50),  nullable=True),
            sa.Column('castor_mtl',       sa.String(50),  nullable=True),
            sa.Column('stud1_mtl',        sa.String(50),  nullable=True),
            sa.Column('stud2_mtl',        sa.String(50),  nullable=True),
            sa.Column('seat_interior_mtl',sa.String(50),  nullable=True),
            sa.Column('back_interior_mtl',sa.String(50),  nullable=True),
            sa.Column('scat_interior_mtl',sa.String(50),  nullable=True),
            sa.Column('material_1',       sa.String(50),  nullable=True),
            sa.Column('material_1_desc',  sa.String(100), nullable=True),
            sa.Column('material_2',       sa.String(50),  nullable=True),
            sa.Column('material_2_desc',  sa.String(100), nullable=True),
            sa.Column('material_3',       sa.String(50),  nullable=True),
            sa.Column('material_3_desc',  sa.String(100), nullable=True),
            sa.Column('material_4',       sa.String(50),  nullable=True),
            sa.Column('material_4_desc',  sa.String(100), nullable=True),
            sa.Column('material_5',       sa.String(50),  nullable=True),
            sa.Column('material_5_desc',  sa.String(100), nullable=True),
            sa.Column('material_6',       sa.String(50),  nullable=True),
            sa.Column('material_6_desc',  sa.String(100), nullable=True),
            sa.Column('material_7',       sa.String(50),  nullable=True),
            sa.Column('material_7_desc',  sa.String(100), nullable=True),
            sa.Column('material_8',       sa.String(50),  nullable=True),
            sa.Column('material_8_desc',  sa.String(100), nullable=True),
            sa.Column('selling_qty',      sa.Numeric(12,3), nullable=True),
            sa.Column('shipped_qty',      sa.Numeric(12,3), nullable=True),
            sa.Column('required_qty',     sa.Numeric(12,3), nullable=True),
            sa.Column('qty_completed',    sa.Numeric(12,3), nullable=True),
            sa.Column('release_qty',      sa.Numeric(12,3), nullable=True),
            sa.Column('release_price',    sa.Numeric(14,4), nullable=True),
            sa.Column('release_price_gbp',sa.Numeric(14,4), nullable=True),
            sa.Column('currency_code',    sa.String(10),  nullable=True),
            sa.Column('exchange_rate',    sa.Numeric(12,6), nullable=True),
            sa.Column('order_book_comments',   sa.Text(),    nullable=True),
            sa.Column('ship_by_changed_count', sa.Integer(), nullable=True),
            sa.Column('need_by_changed_count', sa.Integer(), nullable=True),
            sa.Column('imported_at', sa.DateTime(timezone=True), nullable=True),
        ]:
            batch_op.add_column(col)
    op.create_index('ix_sales_orders_order_num',  'sales_orders', ['order_num'])
    op.create_index('ix_sales_orders_open_order', 'sales_orders', ['open_order'])
    op.create_index('ix_sales_orders_order_date', 'sales_orders', ['order_date'])
    op.create_index('ix_sales_orders_need_by',    'sales_orders', ['need_by_date'])
    op.create_index('ix_sales_orders_customer',   'sales_orders', ['customer_id'])
    op.create_index('ix_sales_orders_job_num',    'sales_orders', ['job_num'])
    op.create_index('ix_sales_orders_part_num',   'sales_orders', ['part_num'])
    op.create_index('ix_sales_orders_prod_plnwk', 'sales_orders', ['prod_plnwk'])
    # ### end Alembic commands ###


def downgrade():
    op.drop_table('sales_orders')
    # ### end Alembic commands ###
    with op.batch_alter_table('sales_orders', schema=None) as batch_op:
        batch_op.drop_constraint('uq_so_num_line_rel', type_='unique')
        batch_op.create_unique_constraint('uq_so_num_line_rel_asm', ['order_num', 'order_line', 'rel_num', 'assembly_seq'])

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('sales_orders', schema=None) as batch_op:
        batch_op.drop_constraint('uq_so_num_line_rel_asm', type_='unique')
        batch_op.create_unique_constraint('uq_so_num_line_rel', ['order_num', 'order_line', 'rel_num'])

    # ### end Alembic commands ###
