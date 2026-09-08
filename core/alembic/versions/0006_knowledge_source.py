"""P5c: knowledge_source table for external knowledge source bindings.

Idempotent (0002-style create_all from live models).

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from medops_core.models import Base  # noqa: PLC0415

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from medops_core.models import KnowledgeSource  # noqa: PLC0415

    KnowledgeSource.__table__.drop(bind=op.get_bind())
