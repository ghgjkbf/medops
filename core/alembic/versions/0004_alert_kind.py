"""P3-4: alert.kind column (fault | maintenance_due) for reminder engine.

Idempotent: 0002 creates tables from the live models (which already carry
`kind`), so on fresh databases the column exists before this migration runs.
Old databases get the column added here.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def _alert_has_kind() -> bool:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'alert' AND column_name = 'kind'"
        )
    ).fetchall()
    return bool(rows)


def upgrade() -> None:
    if not _alert_has_kind():
        op.add_column(
            "alert",
            sa.Column("kind", sa.String(32), nullable=False, server_default="fault"),
        )


def downgrade() -> None:
    if _alert_has_kind():
        op.drop_column("alert", "kind")
