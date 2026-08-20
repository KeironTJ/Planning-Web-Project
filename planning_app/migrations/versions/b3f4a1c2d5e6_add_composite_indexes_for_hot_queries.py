"""add composite indexes for hot queries

Revision ID: b3f4a1c2d5e6
Revises: c7d8e9f0a1b2
Create Date: 2026-08-20

Composite indexes targeting the most frequent query patterns:

  works_orders:
    - (assembly_seq, job_complete) — every WIP list filters assembly_seq=0 + job_complete
    - (job_num, assembly_seq)      — next_op lookups from sales order lines

  sales_orders:
    - (open_order, need_by_date)   — order book + overdue report base filter
    - (order_num, order_line, rel_num) — dedup subquery GROUP BY key

  material_requirements:
    - (material_group, job_closed, issued_complete) — covers the netting base query
    - (material_code, material_group)               — per-material availability lookup
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b3f4a1c2d5e6'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None


def upgrade():
    # works_orders
    op.create_index(
        'ix_wo_asm_complete',
        'works_orders',
        ['assembly_seq', 'job_complete'],
    )
    op.create_index(
        'ix_wo_job_asm',
        'works_orders',
        ['job_num', 'assembly_seq'],
    )

    # sales_orders
    op.create_index(
        'ix_so_open_due',
        'sales_orders',
        ['open_order', 'need_by_date'],
    )
    op.create_index(
        'ix_so_identity',
        'sales_orders',
        ['order_num', 'order_line', 'rel_num'],
    )

    # material_requirements
    op.create_index(
        'ix_mr_group_status',
        'material_requirements',
        ['material_group', 'job_closed', 'issued_complete'],
    )
    op.create_index(
        'ix_mr_code_group',
        'material_requirements',
        ['material_code', 'material_group'],
    )


def downgrade():
    op.drop_index('ix_wo_asm_complete',  table_name='works_orders')
    op.drop_index('ix_wo_job_asm',       table_name='works_orders')
    op.drop_index('ix_so_open_due',      table_name='sales_orders')
    op.drop_index('ix_so_identity',      table_name='sales_orders')
    op.drop_index('ix_mr_group_status',  table_name='material_requirements')
    op.drop_index('ix_mr_code_group',    table_name='material_requirements')
