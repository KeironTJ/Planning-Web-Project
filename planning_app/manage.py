"""
CLI management commands for the Planning application.

Usage:
    python manage.py create-admin
    python manage.py seed-db
    python manage.py list-users
"""

import click
from app import create_app
from app.extensions import db
from app.auth.models import User, Role, Permission

app = create_app()


@app.cli.command("create-admin")
@click.option("--username", prompt=True, help="Admin username")
@click.option("--email", prompt=True, help="Admin email")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def create_admin(username, email, password):
    """Create an administrator account."""
    with app.app_context():
        admin_role = Role.query.filter_by(name="admin").first()
        if not admin_role:
            admin_role = Role(name="admin", description="Full system access")
            db.session.add(admin_role)

        if User.query.filter_by(email=email).first():
            click.echo(f"Error: User with email '{email}' already exists.")
            return

        user = User(username=username, email=email, is_active=True)
        user.set_password(password)
        user.roles.append(admin_role)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin user '{username}' created successfully.")


@app.cli.command("seed-db")
def seed_db():
    """Seed the database with default roles, permissions, and sample data."""
    with app.app_context():
        from app.auth.services import RoleService
        RoleService.seed_default_roles_and_permissions()
        click.echo("Database seeded with default roles and permissions.")


@app.cli.command("list-users")
def list_users():
    """List all users in the system."""
    with app.app_context():
        users = User.query.all()
        if not users:
            click.echo("No users found.")
            return
        click.echo(f"{'ID':<5} {'Username':<20} {'Email':<30} {'Active':<8} {'Roles'}")
        click.echo("-" * 80)
        for u in users:
            roles = ", ".join(r.name for r in u.roles)
            click.echo(f"{u.id:<5} {u.username:<20} {u.email:<30} {str(u.is_active):<8} {roles}")


@app.cli.command("create-tables")
def create_tables():
    """Create all database tables (use migrations in production)."""
    with app.app_context():
        db.create_all()
        click.echo("All tables created.")




@app.cli.command("seed-departments")
def seed_departments():
    """Seed the 18 confirmed production departments if they do not already exist."""
    from app.orders.models import Department
    depts = [
        "WOODMILL",
        "FURNITURE TIMBER",
        "CUTTING",
        "MACHINING",
        "FILLING",
        "UPHOLSTERY",
        "MATTRESS",
        "TACKING",
        "CURTAINS",
        "CURTAIN POLES",
        "BEDDING",
        "BLINDS (CTN SECTION)",
        "DESPATCH",
        "AFTER SALES",
        "BELFIELD TEXTILES",
        "TEK SEATING (CAB SEATS)",
        "DIVAN",
        "ENCAPSULATED SPRINGS",
        "GENERAL",
    ]
    with app.app_context():
        created = 0
        for name in depts:
            code = (
                name.upper()
                .replace(" ", "_")
                .replace("(", "")
                .replace(")", "")
            )
            if not Department.query.filter_by(name=name).first():
                dept = Department(code=code, name=name, is_active=True)
                db.session.add(dept)
                created += 1
        db.session.commit()
        click.echo(f"{created} department(s) created ({len(depts) - created} already existed).")


# ---------------------------------------------------------------------------
# CSV Import commands
# ---------------------------------------------------------------------------

@app.cli.command("import-oob")
@click.option("--file", "filepath", required=True, help="Path to OpenOrderBook_HIDE.csv")
def import_oob(filepath):
    """Import Open Order Book CSV (UPSERT — preserves planner fields)."""
    from app.orders.importers import OobImporter
    with app.app_context():
        click.echo(f"Importing OOB from {filepath} ...")
        batch = OobImporter.import_file(filepath, filename=filepath.split("/")[-1].split("\\")[-1])
        click.echo(
            f"Done: {batch.rows_inserted} inserted, {batch.rows_updated} updated, "
            f"{batch.rows_closed} closed. Status: {batch.status}"
        )
        if batch.error_message:
            click.echo(f"Error: {batch.error_message}", err=True)


@app.cli.command("import-csv")
@click.option("--type", "import_type", required=True,
              type=click.Choice(["stock", "open_po", "main_material", "as_material",
                                 "labour_plan", "smv", "production_flow"]),
              help="Type of CSV to import")
@click.option("--file", "filepath", required=True, help="Path to the CSV file")
def import_csv(import_type, filepath):
    """Import a full-replace CSV file (stock, POs, materials, labour plan, SMV, flows)."""
    from app.materials.importers import StockImporter, OpenPoImporter, MainMaterialImporter, AsMaterialImporter
    from app.capacity.importers import LabourPlanImporter
    from app.orders.importers import SmvImporter, ProductionFlowImporter

    importers = {
        "stock": StockImporter,
        "open_po": OpenPoImporter,
        "main_material": MainMaterialImporter,
        "as_material": AsMaterialImporter,
        "labour_plan": LabourPlanImporter,
        "smv": SmvImporter,
        "production_flow": ProductionFlowImporter,
    }

    with app.app_context():
        importer = importers[import_type]
        filename = filepath.split("/")[-1].split("\\")[-1]
        click.echo(f"Importing {import_type} from {filepath} ...")
        batch = importer.import_file(filepath, filename=filename)
        click.echo(
            f"Done: {batch.rows_inserted} inserted, {batch.rows_updated} updated. "
            f"Status: {batch.status}"
        )
        if batch.error_message:
            click.echo(f"Error: {batch.error_message}", err=True)


if __name__ == "__main__":
    app.cli()
