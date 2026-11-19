"""Attach Redberyl UPI screenshots as payment proofs on the 3 paid invoices.

Requires CONFIRM_REMOTE_DB=YES CONFIRM_DB_NAME=<db> --i-know-this-is-remote
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal, init_db  # noqa: E402
from app.core.db_target_guard import assert_migration_or_seed_allowed  # noqa: E402
from app.models import Invoice  # noqa: E402
from app.services.invoice_payment_proof import save_payment_proof  # noqa: E402

TEMP = ROOT / "temp doc"

# Map: invoice_number → (file, note with UTR)
ATTACHMENTS = [
    (
        "KRWS-DEV-2026-019",
        TEMP / "image.png",
        "UTR 628768355511 — website 1st Payment (14 Oct 2026)",
    ),
    (
        "KRWS-MIX-2026-001",
        TEMP / "image copy.png",
        "UTR 666121807956 — 2nd final payment website (22 Oct 2026)",
    ),
    (
        "KRWS-MNT-2026-021",
        TEMP / "image copy 2.png",
        "UTR 631836999501 — website maintainance (14 Nov 2026)",
    ),
]


def _load_prod_env() -> str:
    """Apply .env.production into os.environ + settings (DB + S3)."""
    text = (ROOT / ".env.production").read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ[key] = val
        if hasattr(settings, key):
            setattr(settings, key, val)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("No DATABASE_URL in .env.production")
    # Clear cached S3 client so new endpoint/keys apply
    try:
        from app.services import object_storage

        object_storage.client.cache_clear()
    except Exception:
        pass
    return url


async def main() -> None:
    url = _load_prod_env()
    assert_migration_or_seed_allowed(url, tool="attach-redberyl-proofs")

    await init_db()
    async with AsyncSessionLocal() as session:
        for number, path, note in ATTACHMENTS:
            if not path.is_file():
                print("MISSING", path)
                continue
            inv = (
                await session.execute(select(Invoice).where(Invoice.invoice_number == number))
            ).scalars().first()
            if not inv:
                print("NOT_FOUND", number)
                continue
            data = path.read_bytes()
            upload = UploadFile(filename=path.name, file=__import__("io").BytesIO(data))
            key, original, content_type, size = await save_payment_proof(inv.id, upload)
            inv.payment_proof_object_key = key
            inv.payment_proof_filename = original
            inv.payment_proof_content_type = content_type
            inv.payment_proof_uploaded_at = datetime.now(timezone.utc)
            inv.payment_proof_note = note
            if inv.status == "paid" and not inv.payment_verified_at:
                inv.payment_verified_at = inv.paid_at or datetime.now(timezone.utc)
            print(
                "attached",
                number,
                "→",
                original,
                f"({size} bytes)",
                "key=",
                key,
            )
        await session.commit()
    print("done")


if __name__ == "__main__":
    if "--i-know-this-is-remote" not in sys.argv:
        sys.argv.append("--i-know-this-is-remote")
    asyncio.run(main())
