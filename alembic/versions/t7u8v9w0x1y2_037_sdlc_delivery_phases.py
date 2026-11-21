"""037 remap delivery phases to SDLC 05–14 and reseed checklists

Revision ID: t7u8v9w0x1y2
Revises: s6t7u8v9w0x1
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t7u8v9w0x1y2"
down_revision: Union[str, Sequence[str], None] = "s6t7u8v9w0x1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Old delivery 5–11 → new SDLC 5–14
PHASE_MAP = {
    5: 5,  # onboarding → requirement analysis
    6: 10,  # design_dev → development
    7: 11,  # qa → testing
    8: 11,  # uat → testing
    9: 12,  # deploy → deployment
    10: 13,  # closure → handover
    11: 14,  # maintenance → maintenance
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "projects" in tables:
        for old, new in PHASE_MAP.items():
            if old == new:
                continue
            bind.execute(
                sa.text("UPDATE projects SET delivery_phase = :new WHERE delivery_phase = :old"),
                {"old": old, "new": new},
            )
        # Clamp anything still outside 5–14
        bind.execute(sa.text("UPDATE projects SET delivery_phase = 5 WHERE delivery_phase < 5"))
        bind.execute(sa.text("UPDATE projects SET delivery_phase = 14 WHERE delivery_phase > 14"))

    if "engagements" in tables:
        for old, new in PHASE_MAP.items():
            if old == new:
                continue
            bind.execute(
                sa.text(
                    "UPDATE engagements SET current_stage = :new "
                    "WHERE current_stage = :old AND current_stage >= 5"
                ),
                {"old": old, "new": new},
            )
        bind.execute(
            sa.text(
                "UPDATE engagements SET current_stage = 14 "
                "WHERE current_stage > 14"
            )
        )

    if "project_phase_notes" in tables:
        for old, new in PHASE_MAP.items():
            if old == new:
                continue
            bind.execute(
                sa.text(
                    "UPDATE project_phase_notes SET stage_number = :new "
                    "WHERE stage_number = :old"
                ),
                {"old": old, "new": new},
            )

    # Drop old delivery checklist rows (stage >= 5). They are reseeded on next GET /delivery.
    if "project_checklist_items" in tables:
        bind.execute(sa.text("DELETE FROM project_checklist_items WHERE stage_number >= 5"))

    if "engagement_checklist_items" in tables:
        bind.execute(sa.text("DELETE FROM engagement_checklist_items WHERE stage_number >= 5"))
        # Reseed engagement delivery checklist items from catalog is left to app ensure_* on next load.
        # Sales items (2–4) remain intact.


def downgrade() -> None:
    # Irreversible data remap — no safe automatic downgrade.
    pass
