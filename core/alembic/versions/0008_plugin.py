"""P6c: plugin registry table (builtin skills enable/disable state)."""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from medops_core.models import Base  # noqa: PLC0415

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from medops_core.models import Plugin  # noqa: PLC0415

    Plugin.__table__.drop(bind=op.get_bind())
