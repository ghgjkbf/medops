"""P1-2: create the 11 core tables (design doc §6).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Import models so target_metadata in env.py picks up all tables when
# autogenerating, and keep this migration explicit (upgrade path below).


def upgrade() -> None:
    # Models are defined in medops_core.models; the 0002 migration creates
    # them via metadata create_all scoped to this revision for determinism.
    from medops_core.models import Base  # noqa: PLC0415

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from medops_core.models import Base  # noqa: PLC0415

    Base.metadata.drop_all(bind=op.get_bind())
