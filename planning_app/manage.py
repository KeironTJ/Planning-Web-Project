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


@app.cli.command("create-user")
@click.option("--username", prompt=True, help="Username")
@click.option("--email", prompt=True, help="Email address")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--role", "role_name", prompt=True, default="viewer",
              help="Role to assign (e.g. planner, viewer, production_manager)")
@click.option("--first-name", default="", help="First name (optional)")
@click.option("--last-name", default="", help="Last name (optional)")
def create_user(username, email, password, role_name, first_name, last_name):
    """Create a new application user and assign a role."""
    with app.app_context():
        if User.query.filter_by(username=username).first():
            click.echo(f"Error: Username '{username}' already exists.")
            return
        if User.query.filter_by(email=email).first():
            click.echo(f"Error: Email '{email}' already exists.")
            return
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            available = [r.name for r in Role.query.order_by(Role.name).all()]
            click.echo(f"Error: Role '{role_name}' not found. Available: {', '.join(available)}")
            return
        user = User(
            username=username,
            email=email,
            first_name=first_name or None,
            last_name=last_name or None,
            is_active=True,
        )
        user.set_password(password)
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        click.echo(f"User '{username}' created with role '{role_name}'.")


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


@app.cli.command("seed-sites")
@click.option("--name", prompt=True, help="Full site name (e.g. 'Warehouse North')")
@click.option("--code", prompt=True, help="Short unique code (e.g. 'WHN')")
@click.option("--description", default="", help="Optional description")
def seed_sites(name, code, description):
    """Create a new site."""
    from app.admin.models import Site
    with app.app_context():
        code = code.upper().strip()
        if Site.query.filter_by(code=code).first():
            click.echo(f"Error: Site with code '{code}' already exists.")
            return
        site = Site(name=name.strip(), code=code, description=description or None, is_active=True)
        db.session.add(site)
        db.session.commit()
        click.echo(f"Site '{name}' ({code}) created with id={site.id}.")


@app.cli.command("seed-departments")
@click.option("--site", "site_code", required=True, prompt=True, help="Site code to add departments to")
def seed_departments(site_code):
    """Seed generic production departments for a site (edit list as needed)."""
    from app.sales.orders.models import Department
    from app.admin.models import Site
    depts = [
        "PRODUCTION",
        "ASSEMBLY",
        "QUALITY",
        "DESPATCH",
        "WAREHOUSE",
        "GENERAL",
    ]
    with app.app_context():
        site = Site.query.filter_by(code=site_code.upper()).first()
        if not site:
            click.echo(f"Error: Site '{site_code}' not found. Run seed-sites first.")
            return
        created = 0
        for name in depts:
            code = name.upper().replace(" ", "_")
            if not Department.query.filter_by(site_id=site.id, name=name).first():
                dept = Department(site_id=site.id, code=code, name=name, is_active=True)
                db.session.add(dept)
                created += 1
        db.session.commit()
        click.echo(f"{created} department(s) created for site '{site.name}'")
        click.echo("Tip: update department names and add/remove entries in Admin → Departments.")


@app.cli.command("clear-oob")
def clear_oob():
    """Delete all OOB sales order lines and their operations."""
    from app.sales.orders.models import SalesOrderLine, WorksOrderOperation
    with app.app_context():
        ops = WorksOrderOperation.query.delete()
        lines = SalesOrderLine.query.delete()
        db.session.commit()
        click.echo(f"Deleted {lines} sales order line(s) and {ops} operation(s).")


# ---------------------------------------------------------------------------
# CSV Import commands
# ---------------------------------------------------------------------------

@app.cli.command("import-oob")
@click.option("--file", "filepath", required=True, help="Path to OpenOrderBook_HIDE.csv")
def import_oob(filepath):
    """Import Open Order Book CSV (UPSERT — preserves planner fields)."""
    from app.sales.orders.importers import OobImporter
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
              type=click.Choice(["sales", "coois", "stock", "open_po", "main_material",
                                 "labour_plan", "oob"]),
              help="Type of CSV to import")
@click.option("--file", "filepath", required=True, help="Path to the CSV file")
def import_csv(import_type, filepath):
    """Import a CSV file. Use sales/coois/stock/open_po/main_material for daily Epicor exports."""
    from app.purchasing.materials.importers import StockImporter, OpenPoImporter, MainMaterialImporter
    from app.planning.capacity.importers import LabourPlanImporter
    from app.sales.orders.importers import OobImporter, SalesImporter, CooisImporter

    importers = {
        "sales":           SalesImporter,
        "coois":           CooisImporter,
        "oob":             OobImporter,
        "stock":           StockImporter,
        "open_po":         OpenPoImporter,
        "main_material":   MainMaterialImporter,
        "labour_plan":     LabourPlanImporter,
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


@app.cli.command("backfill-despatch-dates")
def backfill_despatch_dates():
    """
    One-off backfill: stamp despatch_completed_date / order_completed_date on
    SalesOrderLines where all known-dept ops are already in a terminal state
    (COMPLETED or CLOSED) but the milestone dates were never recorded.

    Safe to run multiple times — only updates rows where the date is NULL.
    """
    from datetime import date
    from app.sales.orders.models import SalesOrderLine, WorksOrderOperation

    _terminal = {WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED}
    today = date.today()

    with app.app_context():
        # Find SOLs missing at least one milestone date
        candidates = SalesOrderLine.query.filter(
            db.or_(
                SalesOrderLine.despatch_completed_date.is_(None),
                SalesOrderLine.order_completed_date.is_(None),
            )
        ).all()

        updated = 0
        for sol in candidates:
            all_ops = (
                WorksOrderOperation.query
                .filter_by(so_number=sol.so_number, line_number=sol.line_number)
                .filter(WorksOrderOperation.department_id.isnot(None))
                .all()
            )
            if not all_ops:
                continue
            if not all(op.status in _terminal for op in all_ops):
                continue
            changed = False
            if sol.despatch_completed_date is None:
                despatch_op = next(
                    (op for op in all_ops
                     if op.work_centre_name.strip().upper() == "DESPATCH"),
                    None,
                )
                if despatch_op:
                    sol.despatch_completed_date = despatch_op.completed_date or today
                    changed = True
            if sol.order_completed_date is None:
                sol.order_completed_date = today
                changed = True
            if changed:
                updated += 1

        db.session.commit()
        click.echo(f"Backfill complete: {updated} SalesOrderLine(s) updated.")


@app.cli.command("backfill-production-ready-date")
def backfill_production_ready_date():
    """
    One-off backfill: stamp production_ready_date on SalesOrderLines where all
    non-DESPATCH known-dept ops are already in a terminal state (COMPLETED or CLOSED)
    but production_ready_date was never recorded.

    Safe to run multiple times — only updates rows where the date is NULL.
    """
    from datetime import date
    from app.sales.orders.models import SalesOrderLine, WorksOrderOperation

    _terminal = {WorksOrderOperation.STATUS_COMPLETED, WorksOrderOperation.STATUS_CLOSED}
    today = date.today()

    with app.app_context():
        candidates = SalesOrderLine.query.filter(
            SalesOrderLine.production_ready_date.is_(None)
        ).all()

        updated = 0
        for sol in candidates:
            all_ops = (
                WorksOrderOperation.query
                .filter_by(so_number=sol.so_number, line_number=sol.line_number)
                .filter(WorksOrderOperation.department_id.isnot(None))
                .all()
            )
            prod_ops = [
                op for op in all_ops
                if op.work_centre_name.strip().upper() != "DESPATCH"
            ]
            if not prod_ops:
                continue
            if not all(op.status in _terminal for op in prod_ops):
                continue
            sol.production_ready_date = today
            updated += 1

        db.session.commit()
        click.echo(f"Backfill complete: {updated} SalesOrderLine(s) updated.")


if __name__ == "__main__":
    app.cli()
