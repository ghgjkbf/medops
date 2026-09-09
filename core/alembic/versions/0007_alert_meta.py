"""P6a: Alert.meta JSONVariant (remediation attachment).

Idempotent add-column (existing rows backfill '{}').

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = bind.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'alert' AND column_name = 'meta'"
    )).scalar()
    if cols is None:
        bind.execute(text("ALTER TABLE alert ADD COLUMN meta JSONB NOT NULL DEFAULT '{}'::jsonb"))
        bind.execute(text("COMMENT ON COLUMN alert.meta IS 'P6a remediation attachment'"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE alert DROP COLUMN IF EXISTS meta"))
