"""P6c-ext: Plugin.meta JSONVariant (imported plugin kind/config)."""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = bind.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'plugin' AND column_name = 'meta'"
    )).scalar()
    if cols is None:
        bind.execute(text(
            "ALTER TABLE plugin ADD COLUMN meta JSONB NOT NULL DEFAULT '{}'::jsonb"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE plugin DROP COLUMN IF EXISTS meta"))
