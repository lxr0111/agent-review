"""Store review dispatch intent in the same transaction as the review task."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261112_0007"
down_revision: str | None = "20261003_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_dispatch_outbox",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("review_task_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "LEASED",
                "SENT",
                name="reviewdispatchstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_task_id", name="uq_review_dispatch_outbox_task"),
    )
    op.create_index(
        "ix_review_dispatch_outbox_status_available",
        "review_dispatch_outbox",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_review_dispatch_outbox_status_lease",
        "review_dispatch_outbox",
        ["status", "lease_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_dispatch_outbox_status_lease", table_name="review_dispatch_outbox")
    op.drop_index("ix_review_dispatch_outbox_status_available", table_name="review_dispatch_outbox")
    op.drop_table("review_dispatch_outbox")
