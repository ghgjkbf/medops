"""P5a: butler_audit table for the butler agent's operation audit trail.

Idempotent: 0002-style create_all from live models — fresh databases
already carry the table, old databases get it here.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from medops_core.models import Base  # noqa: PLC0415

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from medops_core.models import ButlerAudit  # noqa: PLC0415

    ButlerAudit.__table__.drop(bind=op.get_bind())
