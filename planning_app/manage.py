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

if __name__ == "__main__":
    app.cli()
