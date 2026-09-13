"""P7a/b: agent_state, agent_metrics, inspection_log tables."""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from medops_core.models import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for t in ("inspection_log", "agent_metrics", "agent_state"):
        op.drop_table(t)
