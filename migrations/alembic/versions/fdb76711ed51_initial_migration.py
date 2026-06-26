"""Initial migration

Revision ID: fdb76711ed51
Revises:
Create Date: 2026-05-27 12:21:39.011362

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'fdb76711ed51'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
