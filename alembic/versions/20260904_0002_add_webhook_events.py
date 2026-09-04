"""Add GitHub webhook events and review task metadata."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260904_0002"
down_revision: str | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_tasks", sa.Column("github_repository_id", sa.BigInteger()))
    op.add_column("review_tasks", sa.Column("installation_id", sa.BigInteger()))
    op.add_column("review_tasks", sa.Column("base_sha", sa.String(64)))
    op.add_column("review_tasks", sa.Column("delivery_id", sa.String(100)))
    op.create_index("ix_review_tasks_delivery_id", "review_tasks", ["delivery_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("delivery_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100)),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACCEPTED",
                "IGNORED",
                name="webhookeventstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("review_task_id", sa.Uuid(native_uuid=False)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_event_delivery"),
    )
    op.create_index("ix_webhook_events_review_task_id", "webhook_events", ["review_task_id"])
    op.create_index(
        "ix_webhook_events_type_created", "webhook_events", ["event_type", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_type_created", table_name="webhook_events")
    op.drop_index("ix_webhook_events_review_task_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_review_tasks_delivery_id", table_name="review_tasks")
    op.drop_column("review_tasks", "delivery_id")
    op.drop_column("review_tasks", "base_sha")
    op.drop_column("review_tasks", "installation_id")
    op.drop_column("review_tasks", "github_repository_id")
