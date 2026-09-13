"""medops-common: shared constants, utilities and schemas."""

__version__ = "0.1.0"

# JSONVariant: JSONB over PostgreSQL, JSON over SQLite
from sqlalchemy import JSON
from medops_common.constants import KEEP_SQLITE

JSONVariant = JSON().with_variant(JSON(), "sqlite") if KEEP_SQLITE else JSON()

