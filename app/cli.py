"""Explicit operator CLIs — never run from app lifespan.

Usage:
  python -m app.cli seed
  python -m app.cli bootstrap-admin
  python -m app.cli write-projects-import-template
  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx
  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx --apply
  python -m app.cli migrate upgrade head
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys


def _cmd_seed(_: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.seed import seed_database_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="seed")
    asyncio.run(seed_database_async())
    print("seed: OK")
    return 0


def _cmd_seed_invoice_demos(_: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.seed_invoice_demos import seed_invoice_demos_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="seed-invoice-demos")
    result = asyncio.run(seed_invoice_demos_async())
    print("seed-invoice-demos:", result)
    return 0


def _cmd_backfill_invoice_snapshots(args: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.backfill_invoice_snapshots import backfill_invoice_snapshots_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="backfill-invoice-snapshots")
    result = asyncio.run(backfill_invoice_snapshots_async(dry_run=bool(args.dry_run)))
    print("backfill-invoice-snapshots:", result)
    return 0 if result.get("ok") else 1


def _cmd_fix_gst_series_mismatches(args: argparse.Namespace) -> int:
    import json

    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.fix_gst_series_mismatches import fix_gst_series_mismatches_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="fix-gst-series-mismatches")
    dry_run = not bool(args.apply)
    result = asyncio.run(fix_gst_series_mismatches_async(dry_run=dry_run))
    print("fix-gst-series-mismatches:", json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _cmd_migrate_invoice_numbers(args: argparse.Namespace) -> int:
    import json

    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.migrate_invoice_numbers_gst_series import (
        migrate_invoice_numbers_gst_series_async,
    )

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="migrate-invoice-numbers")
    dry_run = not bool(args.apply)
    result = asyncio.run(
        migrate_invoice_numbers_gst_series_async(
            dry_run=dry_run,
            include_deleted=bool(args.include_deleted),
        )
    )
    # Compact plan for console
    plan = result.get("plan") or []
    preview = [
        f"{p['old']} → {p['new']} ({'GST' if p['gst'] else 'non-GST'}{', deleted' if p.get('deleted') else ''})"
        for p in plan
        if p["old"] != p["new"]
    ]
    print("migrate-invoice-numbers:", json.dumps({
        **{k: v for k, v in result.items() if k != "plan"},
        "changes_preview": preview[:40],
        "changes_preview_truncated": max(0, len(preview) - 40),
    }, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _cmd_bootstrap_admin(_: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.seed import bootstrap_admin_cli_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="bootstrap-admin")
    created = asyncio.run(bootstrap_admin_cli_async())
    print("bootstrap-admin:", "created" if created else "no-op (admin already present or missing SUPER_ADMIN_*)")
    return 0


def _cmd_write_projects_import_template(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.scripts.import_projects_from_excel import default_template_path, write_template

    out = Path(args.out) if args.out else default_template_path()
    path = write_template(out)
    print("write-projects-import-template:", path)
    return 0


def _cmd_import_projects_from_excel(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.import_projects_from_excel import import_projects_from_excel_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="import-projects-from-excel")
    dry_run = not bool(args.apply)
    result = asyncio.run(
        import_projects_from_excel_async(Path(args.file), dry_run=dry_run)
    )
    print("import-projects-from-excel:", json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _cmd_migrate(args: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="alembic")
    # Confirmation flag is for db_target_guard (sys.argv); do not forward to alembic.
    forwarded = [a for a in args.alembic_args if a != "--i-know-this-is-remote"]
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    alembic_args = ["python", "-m", "alembic", *forwarded]
    print("migrate:", " ".join(alembic_args))
    return subprocess.call(alembic_args, env=os.environ.copy())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="KR ERP operator CLIs")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Seed settings/templates/roles (local/dev; guarded)")
    p_seed.set_defaults(func=_cmd_seed)

    p_demo = sub.add_parser(
        "seed-invoice-demos",
        help="Seed one demo invoice per type (custom_dev, amc, hosting, …)",
    )
    p_demo.set_defaults(func=_cmd_seed_invoice_demos)

    p_backfill = sub.add_parser(
        "backfill-invoice-snapshots",
        help="Freeze document_snapshot for issued invoices that lack one (idempotent)",
    )
    p_backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates without writing",
    )
    p_backfill.set_defaults(func=_cmd_backfill_invoice_snapshots)

    p_fix_gst = sub.add_parser(
        "fix-gst-series-mismatches",
        help="Hard-delete+recreate draft tip-of-series invoices whose GST flag disagrees with number",
    )
    p_fix_gst.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    p_fix_gst.set_defaults(func=_cmd_fix_gst_series_mismatches)

    p_renum = sub.add_parser(
        "migrate-invoice-numbers",
        help="Renumber invoices onto GST / non-GST series (FY + optional -G-, start 1001)",
    )
    p_renum.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    p_renum.add_argument(
        "--include-deleted",
        action="store_true",
        help="Also renumber soft-deleted invoices into the live series (default: park collisions only)",
    )
    p_renum.set_defaults(func=_cmd_migrate_invoice_numbers)

    p_boot = sub.add_parser(
        "bootstrap-admin",
        help="Create SUPER_ADMIN only if no admin exists; never modifies other users",
    )
    p_boot.set_defaults(func=_cmd_bootstrap_admin)

    p_tpl = sub.add_parser(
        "write-projects-import-template",
        help="Write Excel template for bulk-importing completed projects",
    )
    p_tpl.add_argument(
        "--out",
        default=None,
        help="Output .xlsx path (default: templates/completed_projects_import.xlsx)",
    )
    p_tpl.set_defaults(func=_cmd_write_projects_import_template)

    p_imp = sub.add_parser(
        "import-projects-from-excel",
        help="Import clients + projects from the Excel template (dry-run unless --apply)",
    )
    p_imp.add_argument(
        "--file",
        required=True,
        help="Path to filled completed_projects_import.xlsx",
    )
    p_imp.add_argument(
        "--apply",
        action="store_true",
        help="Write to DB (default is dry-run preview only)",
    )
    p_imp.add_argument(
        "--i-know-this-is-remote",
        action="store_true",
        help="Required (with CONFIRM_DB_NAME + CONFIRM_REMOTE_DB=YES) when DATABASE_URL is not local",
    )
    p_imp.set_defaults(func=_cmd_import_projects_from_excel)

    p_mig = sub.add_parser(
        "migrate",
        help="Run alembic (one-off deploy). Requires db_target_guard confirmation for non-local DBs.",
    )
    p_mig.add_argument(
        "alembic_args",
        nargs=argparse.REMAINDER,
        help="Args passed to alembic, e.g. upgrade head",
    )
    p_mig.set_defaults(func=_cmd_migrate)

    args = parser.parse_args(argv)
    if args.command == "migrate" and not args.alembic_args:
        p_mig.error("provide alembic args, e.g. upgrade head")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
