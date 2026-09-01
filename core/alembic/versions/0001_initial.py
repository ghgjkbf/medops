"""initial (empty) — P1 starting point

Revision ID: 0001
Revises:
Create Date: 2026-09-01

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Placeholder migration: real tables arrive with P1-2 models.
    pass


def downgrade() -> None:
    pass
