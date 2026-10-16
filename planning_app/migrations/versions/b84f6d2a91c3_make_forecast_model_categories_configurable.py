"""Replace fixed model classes with user-managed descriptive categories."""

from alembic import op
import sqlalchemy as sa

revision = "b84f6d2a91c3"
down_revision = "a7c31d9e2b60"
branch_labels = None
depends_on = None


LEGACY_CATEGORIES = (
    ("full_unit", "Full unit"),
    ("cover_only", "Cover-only"),
    ("scatter", "Scatter"),
    ("excluded", "Excluded"),
)


def upgrade():
    """Preserve existing classifications while moving to category records."""
    op.create_table(
        "forecast_model_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("name_key", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("legacy_value", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("legacy_value"),
        sa.UniqueConstraint("name_key"),
    )

    connection = op.get_bind()
    category_table = sa.table(
        "forecast_model_categories",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("name_key", sa.String),
        sa.column("description", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("legacy_value", sa.String),
    )
    for sort_order, (legacy_value, name) in enumerate(LEGACY_CATEGORIES):
        connection.execute(category_table.insert().values(
            name=name,
            name_key=name.casefold(),
            description=None,
            sort_order=sort_order,
            legacy_value=legacy_value,
        ))

    with op.batch_alter_table("forecast_model_classifications") as batch_op:
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))

    for legacy_value, name in LEGACY_CATEGORIES:
        connection.execute(
            sa.text(
                """
                UPDATE forecast_model_classifications
                SET category_id = (
                    SELECT id FROM forecast_model_categories
                    WHERE legacy_value = :legacy_value
                )
                WHERE category = :legacy_value
                """
            ),
            {"legacy_value": legacy_value},
        )

    with op.batch_alter_table("forecast_model_classifications") as batch_op:
        batch_op.drop_constraint("ck_forecast_model_category", type_="check")
        batch_op.drop_column("category")
        batch_op.alter_column(
            "category_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_forecast_model_classifications_category_id",
            "forecast_model_categories",
            ["category_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_forecast_model_classifications_category_id",
            ["category_id"],
            unique=False,
        )


def downgrade():
    """Restore legacy values only when every assigned class is representable."""
    connection = op.get_bind()
    unsupported = connection.execute(
        sa.text(
            """
            SELECT categories.name
            FROM forecast_model_classifications AS classifications
            JOIN forecast_model_categories AS categories
              ON categories.id = classifications.category_id
            WHERE categories.legacy_value IS NULL
            LIMIT 1
            """
        )
    ).first()
    if unsupported:
        raise RuntimeError(
            "Cannot restore fixed forecast classes while models use custom "
            f"category {unsupported.name!r}. Reassign those models first."
        )

    with op.batch_alter_table("forecast_model_classifications") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(length=20), nullable=True))

    connection.execute(
        sa.text(
            """
            UPDATE forecast_model_classifications
            SET category = (
                SELECT legacy_value FROM forecast_model_categories
                WHERE id = forecast_model_classifications.category_id
            )
            """
        )
    )

    with op.batch_alter_table("forecast_model_classifications") as batch_op:
        batch_op.drop_index("ix_forecast_model_classifications_category_id")
        batch_op.drop_constraint(
            "fk_forecast_model_classifications_category_id", type_="foreignkey"
        )
        batch_op.drop_column("category_id")
        batch_op.alter_column(
            "category",
            existing_type=sa.String(length=20),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_forecast_model_category",
            "category IN ('full_unit', 'cover_only', 'scatter', 'excluded')",
        )

    op.drop_table("forecast_model_categories")
