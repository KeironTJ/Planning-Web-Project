"""
Flask CLI commands for Epicor Kinetic BAQ sync operations.

Registered under the ``epicor`` group — requires an app context.

Usage (venv active, from planning_app/):
    flask epicor list
    flask epicor sync
    flask epicor sync stock
    flask epicor sync stock purchase_orders works_orders
    flask epicor inspect stock
    flask epicor inspect works_orders -p DateFrom=2026-07-01 -p DateTo=2026-07-01
"""

from __future__ import annotations

import click
from flask import current_app
from flask.cli import AppGroup

from app.core.epicor_client import KineticClient
from app.core.epicor_importers import REGISTRY, run_batch

epicor_cli = AppGroup("epicor", help="Epicor Kinetic BAQ sync commands.")


# ---------------------------------------------------------------------------
# flask epicor list
# ---------------------------------------------------------------------------

@epicor_cli.command("list")
def list_importers():
    """List all registered BAQ importers and their status."""
    click.echo("\nRegistered Epicor BAQ importers:\n")
    click.echo(f"  {'Key':<25} {'BAQ Name':<30} {'Status'}")
    click.echo(f"  {'-'*24} {'-'*29} {'-'*15}")

    for key, cls in REGISTRY.items():
        # Check if _sync_records is still the NotImplementedError stub
        try:
            # Instantiate is not possible without a client, so check source
            is_stub = (
                "NotImplementedError" in cls._sync_records.__doc__
                if cls._sync_records.__doc__
                else False
            )
        except Exception:
            is_stub = True
        status = click.style("NOT IMPLEMENTED", fg="yellow") if is_stub else click.style("Ready", fg="green")
        click.echo(f"  {key:<25} {cls.BAQ_NAME:<30} {status}")

    click.echo()


# ---------------------------------------------------------------------------
# flask epicor sync [baqs...]
# ---------------------------------------------------------------------------

@epicor_cli.command("sync")
@click.argument("baqs", nargs=-1)
def sync_command(baqs: tuple[str, ...]) -> None:
    """
    Pull BAQs from Epicor and write to the database.

    Pass one or more registry keys to run specific importers, or omit all
    arguments to run every registered importer in sequence.

    \b
    Examples:
        flask epicor sync
        flask epicor sync stock
        flask epicor sync stock purchase_orders works_orders
    """
    if baqs:
        invalid = [k for k in baqs if k not in REGISTRY]
        if invalid:
            raise click.BadParameter(
                f"Unknown importer key(s): {', '.join(invalid)}. "
                f"Run `flask epicor list` to see valid keys.",
                param_hint="baqs",
            )
        keys: list[str] | None = list(baqs)
    else:
        keys = None  # run all

    label = ", ".join(keys) if keys else "ALL"
    click.echo(f"\nStarting Epicor sync: {label}\n")

    with KineticClient.from_app(current_app._get_current_object()) as client:
        results = run_batch(client, keys=keys)

    click.echo("Results:")
    all_ok = True
    for key, result in results.items():
        if isinstance(result, Exception):
            click.echo(f"  {click.style('FAILED', fg='red')}  {key:<25} {result}")
            all_ok = False
        else:
            click.echo(
                f"  {click.style('OK', fg='green')}      {key:<25} "
                f"{result.row_count} fetched  /  {result.rows_inserted} inserted"
            )

    click.echo()
    if not all_ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# flask epicor inspect <baq_key>
# ---------------------------------------------------------------------------

@epicor_cli.command("inspect")
@click.argument("baq_key")
@click.option(
    "--params", "-p",
    multiple=True,
    metavar="KEY=VALUE",
    help="BAQ filter parameters.  Repeat for multiple: -p DateFrom=2026-07-01 -p DateTo=2026-07-01",
)
def inspect_command(baq_key: str, params: tuple[str, ...]) -> None:
    """
    Fetch one record from a BAQ and print its field names + sample values.

    Use this to discover the BAQ's fields before implementing _sync_records()
    and designing the target database model.

    \b
    Examples:
        flask epicor inspect stock
        flask epicor inspect works_orders -p DateFrom=2026-07-01 -p DateTo=2026-07-01
    """
    # Accept either a registry key (e.g. "stock") or a raw Epicor BAQ name
    # (e.g. "PlanningOutPut") — useful for inspecting unregistered BAQs.
    if baq_key in REGISTRY:
        importer_cls = REGISTRY[baq_key]
        baq_name = importer_cls.BAQ_NAME
        static_params = importer_cls.BAQ_PARAMS
    else:
        baq_name = baq_key          # treat as a raw BAQ name
        static_params = {}
        click.secho(
            f"Note: {baq_key!r} is not a registered importer key — "
            f"treating as a raw BAQ name.",
            fg="yellow",
        )

    baq_params: dict = {}
    for p in params:
        if "=" not in p:
            raise click.BadParameter(
                f"Expected KEY=VALUE, got {p!r}", param_hint="--params"
            )
        k, v = p.split("=", 1)
        baq_params[k] = v

    # Merge static BAQ_PARAMS (from importer) with any passed on the CLI
    merged = {**static_params, **baq_params}

    click.echo(f"\nInspecting BAQ: {baq_name}  (key: {baq_key})\n")

    try:
        with KineticClient.from_app(current_app._get_current_object()) as client:
            records = client.get_baq(baq_name, params=merged or None, page_size=1)
    except Exception as exc:
        raise click.ClickException(
            f"API call failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not records:
        click.secho("No records returned — BAQ returned 0 rows.", fg="yellow")
        click.echo(
            "If this BAQ needs date parameters, supply them with -p:\n"
            "  flask epicor inspect works_orders -p DateFrom=2026-07-01 -p DateTo=2026-07-01"
        )
        return

    sample = records[0]
    fields = list(sample.keys())

    click.echo(f"{len(fields)} fields available in {baq_name}:\n")
    click.echo(f"  {'Field name':<45} Sample value")
    click.echo(f"  {'-'*44} {'-'*30}")
    for field in fields:
        value = repr(sample[field])
        if len(value) > 50:
            value = value[:47] + "..."
        click.echo(f"  {field:<45} {value}")
    click.echo()
