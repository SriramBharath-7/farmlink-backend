"""make markets multi state safe

Revision ID: 7473351d2ca7
Revises: bed375a28175
Create Date: 2026-09-15 23:20:57.242886

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7473351d2ca7'
down_revision: Union[str, Sequence[str], None] = 'bed375a28175'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make market identity safe for multi-state ingestion."""

    # Missing state must never silently become Maharashtra.
    op.alter_column(
        "markets",
        "state",
        existing_type=sa.String(length=100),
        server_default="Unknown",
        existing_nullable=False,
    )

    # Market identity must include state.
    op.drop_constraint(
        "uq_market_name_district",
        "markets",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_market_name_district_state",
        "markets",
        ["name", "district", "state"],
    )


def downgrade() -> None:
    """Restore the previous Maharashtra-only market identity."""

    op.drop_constraint(
        "uq_market_name_district_state",
        "markets",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_market_name_district",
        "markets",
        ["name", "district"],
    )

    op.alter_column(
        "markets",
        "state",
        existing_type=sa.String(length=100),
        server_default="Maharashtra",
        existing_nullable=False,
    )