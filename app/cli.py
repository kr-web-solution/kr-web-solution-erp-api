"""Explicit operator CLIs — never run from app lifespan.

Usage:
  python -m app.cli seed
  python -m app.cli bootstrap-admin
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


def _cmd_bootstrap_admin(_: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.core.db_target_guard import assert_migration_or_seed_allowed
    from app.scripts.seed import bootstrap_admin_cli_async

    assert_migration_or_seed_allowed(settings.DATABASE_URL, tool="bootstrap-admin")
    created = asyncio.run(bootstrap_admin_cli_async())
    print("bootstrap-admin:", "created" if created else "no-op (admin already present or missing SUPER_ADMIN_*)")
    return 0


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

    p_boot = sub.add_parser(
        "bootstrap-admin",
        help="Create SUPER_ADMIN only if no admin exists; never modifies other users",
    )
    p_boot.set_defaults(func=_cmd_bootstrap_admin)

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
