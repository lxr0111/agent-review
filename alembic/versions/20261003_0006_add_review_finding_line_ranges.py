"""Add validated line ranges to review findings."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_0006"
down_revision: str | None = "20260907_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_findings", sa.Column("start_line", sa.Integer(), nullable=True))
    op.add_column("review_findings", sa.Column("end_line", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_review_finding_line_range_valid",
        "review_findings",
        "((start_line IS NULL AND end_line IS NULL) OR "
        "(start_line IS NOT NULL AND end_line IS NOT NULL "
        "AND start_line >= 1 AND end_line >= start_line))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_review_finding_line_range_valid",
        "review_findings",
        type_="check",
    )
    op.drop_column("review_findings", "end_line")
    op.drop_column("review_findings", "start_line")
