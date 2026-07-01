"""redesign stock model for epicor api

Revision ID: d89ae9b4e049
Revises: e143896d2c9d
Create Date: 2026-07-01 19:25:16.130926

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd89ae9b4e049'
down_revision = 'e143896d2c9d'
branch_labels = None
depends_on = None


def upgrade():
    # Full table drop-and-recreate — SQLite cannot ALTER columns or drop
    # unnamed constraints, so we rebuild the table from scratch.
    # Stock data is repopulated by `flask epicor sync stock` after migration.
    op.drop_table('stock')
    op.create_table(
        'stock',
        sa.Column('id',                        sa.Integer(),       nullable=False),
        sa.Column('part_num',                  sa.String(50),      nullable=False),
        sa.Column('part_description',          sa.String(200),     nullable=True),
        sa.Column('class_id',                  sa.String(50),      nullable=True),
        sa.Column('unit_of_measure',           sa.String(10),      nullable=True),
        sa.Column('plant',                     sa.String(20),      nullable=True),
        sa.Column('qty_on_hand',               sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_on_hand_stores',        sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_on_hand_prod_uk',       sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_on_hand_romania',       sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_on_hand_others',        sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_required',              sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_required_unreleased',   sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_open_po',               sa.Numeric(14, 3),  nullable=True),
        sa.Column('qty_inspection',            sa.Numeric(14, 3),  nullable=True),
        sa.Column('surplus_deficit',           sa.Numeric(14, 3),  nullable=True),
        sa.Column('surplus_deficit_unreleased',sa.Numeric(14, 3),  nullable=True),
        sa.Column('insufficient_stock',        sa.Boolean(),       nullable=True),
        sa.Column('imported_at',               sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('part_num', 'plant', name='uq_stock_part_plant'),
    )
    op.create_index('ix_stock_part_num',          'stock', ['part_num'])
    op.create_index('ix_stock_plant',             'stock', ['plant'])
    op.create_index('ix_stock_class_id',          'stock', ['class_id'])
    op.create_index('ix_stock_insufficient_stock','stock', ['insufficient_stock'])
    # ### end Alembic commands ###


def downgrade():
    # Restore the old table structure (data will be empty — reimport CSV if needed)
    op.drop_table('stock')
    op.create_table(
        'stock',
        sa.Column('id',           sa.Integer(),      nullable=False),
        sa.Column('site_id',      sa.Integer(),      nullable=True),
        sa.Column('product_code', sa.String(50),     nullable=False),
        sa.Column('description',  sa.String(200),    nullable=True),
        sa.Column('qty_on_hand',  sa.Numeric(12, 3), nullable=False),
        sa.Column('imported_at',  sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['site_id'], ['sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stock_site_id',      'stock', ['site_id'])
    op.create_index('ix_stock_product_code', 'stock', ['product_code'])
    # ### end Alembic commands ###
