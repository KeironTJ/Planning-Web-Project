"""redesign material_requirements for epicor api

Revision ID: 8a7fce0bd5a8
Revises: 8cd035ed33b9
Create Date: 2026-07-01 21:40:42.938495

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8a7fce0bd5a8'
down_revision = '8cd035ed33b9'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the old table and create the new one — full schema redesign.
    # Data is repopulated by `flask epicor sync material_requirements`.
    op.drop_table('material_requirements_main')
    op.create_table(
        'material_requirements',
        sa.Column('id',                  sa.Integer(),       nullable=False),
        sa.Column('works_order',         sa.String(20),      nullable=True),
        sa.Column('job_released',        sa.Boolean(),       nullable=True),
        sa.Column('job_firm',            sa.Boolean(),       nullable=True),
        sa.Column('job_complete',        sa.Boolean(),       nullable=True),
        sa.Column('job_closed',          sa.Boolean(),       nullable=True),
        sa.Column('due_date',            sa.Date(),          nullable=True),
        sa.Column('finished_part_num',   sa.String(50),      nullable=True),
        sa.Column('finished_part_desc',  sa.String(200),     nullable=True),
        sa.Column('prod_qty',            sa.Numeric(14, 3),  nullable=True),
        sa.Column('plant',               sa.String(20),      nullable=True),
        sa.Column('prod_plnwk',          sa.String(20),      nullable=True),
        sa.Column('model',               sa.String(100),     nullable=True),
        sa.Column('size',                sa.String(50),      nullable=True),
        sa.Column('so_type',             sa.String(20),      nullable=True),
        sa.Column('so_number',           sa.String(20),      nullable=True),
        sa.Column('assembly_seq',        sa.Integer(),       nullable=True),
        sa.Column('assembly_desc',       sa.String(200),     nullable=True),
        sa.Column('mtl_seq',             sa.Integer(),       nullable=True),
        sa.Column('material_code',       sa.String(50),      nullable=True),
        sa.Column('material_description',sa.String(200),     nullable=True),
        sa.Column('backflush',           sa.Boolean(),       nullable=True),
        sa.Column('qty_per',             sa.Numeric(14, 4),  nullable=True),
        sa.Column('qty_for_order',       sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_issued',          sa.Numeric(14, 3),  nullable=True),
        sa.Column('issued_complete',     sa.Boolean(),       nullable=True),
        sa.Column('related_operation',   sa.Integer(),       nullable=True),
        sa.Column('warehouse_code',      sa.String(10),      nullable=True),
        sa.Column('class_id',            sa.String(50),      nullable=True),
        sa.Column('imported_at',         sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('works_order', 'assembly_seq', 'mtl_seq', name='uq_mat_req'),
    )
    op.create_index('ix_mat_req_works_order',   'material_requirements', ['works_order'])
    op.create_index('ix_mat_req_due_date',      'material_requirements', ['due_date'])
    op.create_index('ix_mat_req_material_code', 'material_requirements', ['material_code'])
    op.create_index('ix_mat_req_so_number',     'material_requirements', ['so_number'])
    op.create_index('ix_mat_req_job_closed',    'material_requirements', ['job_closed'])
    op.create_index('ix_mat_req_plant',         'material_requirements', ['plant'])
    # ### end Alembic commands ###


def downgrade():
    op.drop_table('material_requirements')
    op.create_table(
        'material_requirements_main',
        sa.Column('id',                   sa.Integer(),     nullable=False),
        sa.Column('site_id',              sa.Integer(),     nullable=True),
        sa.Column('so_number',            sa.String(20),    nullable=True),
        sa.Column('works_order',          sa.String(50),    nullable=True),
        sa.Column('due_date',             sa.Date(),        nullable=True),
        sa.Column('material_code',        sa.String(50),    nullable=True),
        sa.Column('material_description', sa.String(200),   nullable=True),
        sa.Column('qty_for_order',        sa.Numeric(12,3), nullable=True),
        sa.Column('qty_issued',           sa.Numeric(12,3), nullable=True),
        sa.Column('complete',             sa.String(1),     nullable=True),
        sa.Column('imported_at',          sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    # ### end Alembic commands ###
    op.create_table('material_requirements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('works_order', sa.String(length=20), nullable=True),
    sa.Column('job_released', sa.Boolean(), nullable=True),
    sa.Column('job_firm', sa.Boolean(), nullable=True),
    sa.Column('job_complete', sa.Boolean(), nullable=True),
    sa.Column('job_closed', sa.Boolean(), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('finished_part_num', sa.String(length=50), nullable=True),
    sa.Column('finished_part_desc', sa.String(length=200), nullable=True),
    sa.Column('prod_qty', sa.Numeric(precision=14, scale=3), nullable=True),
    sa.Column('plant', sa.String(length=20), nullable=True),
    sa.Column('prod_plnwk', sa.String(length=20), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('size', sa.String(length=50), nullable=True),
    sa.Column('so_type', sa.String(length=20), nullable=True),
    sa.Column('so_number', sa.String(length=20), nullable=True),
    sa.Column('assembly_seq', sa.Integer(), nullable=True),
    sa.Column('assembly_desc', sa.String(length=200), nullable=True),
    sa.Column('mtl_seq', sa.Integer(), nullable=True),
    sa.Column('material_code', sa.String(length=50), nullable=True),
    sa.Column('material_description', sa.String(length=200), nullable=True),
    sa.Column('backflush', sa.Boolean(), nullable=True),
    sa.Column('qty_per', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('qty_for_order', sa.Numeric(precision=14, scale=3), nullable=True),
    sa.Column('qty_issued', sa.Numeric(precision=14, scale=3), nullable=True),
    sa.Column('issued_complete', sa.Boolean(), nullable=True),
    sa.Column('related_operation', sa.Integer(), nullable=True),
    sa.Column('warehouse_code', sa.String(length=10), nullable=True),
    sa.Column('class_id', sa.String(length=50), nullable=True),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('works_order', 'assembly_seq', 'mtl_seq', name='uq_mat_req')
    )
    with op.batch_alter_table('material_requirements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_material_requirements_class_id'), ['class_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_due_date'), ['due_date'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_finished_part_num'), ['finished_part_num'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_issued_complete'), ['issued_complete'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_job_closed'), ['job_closed'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_job_complete'), ['job_complete'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_material_code'), ['material_code'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_plant'), ['plant'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_so_number'), ['so_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_material_requirements_works_order'), ['works_order'], unique=False)

    with op.batch_alter_table('material_requirements_main', schema=None) as batch_op:
        batch_op.drop_index('ix_material_requirements_main_customer_id')
        batch_op.drop_index('ix_material_requirements_main_department')
        batch_op.drop_index('ix_material_requirements_main_due_date')
        batch_op.drop_index('ix_material_requirements_main_material_code')
        batch_op.drop_index('ix_material_requirements_main_site_id')
        batch_op.drop_index('ix_material_requirements_main_so_number')
        batch_op.drop_index('ix_material_requirements_main_works_order')

    op.drop_table('material_requirements_main')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('material_requirements_main',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('site_id', sa.INTEGER(), nullable=True),
    sa.Column('customer_id', sa.VARCHAR(length=30), nullable=True),
    sa.Column('batch_id', sa.VARCHAR(length=30), nullable=True),
    sa.Column('so_number', sa.VARCHAR(length=20), nullable=True),
    sa.Column('works_order', sa.VARCHAR(length=50), nullable=True),
    sa.Column('load_date', sa.DATE(), nullable=True),
    sa.Column('due_date', sa.DATE(), nullable=True),
    sa.Column('department', sa.VARCHAR(length=100), nullable=True),
    sa.Column('material_code', sa.VARCHAR(length=50), nullable=True),
    sa.Column('material_description', sa.VARCHAR(length=200), nullable=True),
    sa.Column('qty_required_per_set', sa.NUMERIC(precision=12, scale=3), nullable=True),
    sa.Column('qty_for_order', sa.NUMERIC(precision=12, scale=3), nullable=True),
    sa.Column('qty_issued', sa.NUMERIC(precision=12, scale=3), nullable=True),
    sa.Column('product_group', sa.VARCHAR(length=30), nullable=True),
    sa.Column('product_group_desc', sa.VARCHAR(length=100), nullable=True),
    sa.Column('complete', sa.VARCHAR(length=1), nullable=True),
    sa.Column('imported_at', sa.DATETIME(), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('material_requirements_main', schema=None) as batch_op:
        batch_op.create_index('ix_material_requirements_main_works_order', ['works_order'], unique=False)
        batch_op.create_index('ix_material_requirements_main_so_number', ['so_number'], unique=False)
        batch_op.create_index('ix_material_requirements_main_site_id', ['site_id'], unique=False)
        batch_op.create_index('ix_material_requirements_main_material_code', ['material_code'], unique=False)
        batch_op.create_index('ix_material_requirements_main_due_date', ['due_date'], unique=False)
        batch_op.create_index('ix_material_requirements_main_department', ['department'], unique=False)
        batch_op.create_index('ix_material_requirements_main_customer_id', ['customer_id'], unique=False)

    with op.batch_alter_table('material_requirements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_material_requirements_works_order'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_so_number'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_plant'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_material_code'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_job_complete'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_job_closed'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_issued_complete'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_finished_part_num'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_due_date'))
        batch_op.drop_index(batch_op.f('ix_material_requirements_class_id'))

    op.drop_table('material_requirements')
    # ### end Alembic commands ###
