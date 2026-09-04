"""Create review tasks."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "STALE",
                name="reviewstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("current_stage", sa.String(50), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository", "pull_request_number", "head_sha", name="uq_review_task_identity"
        ),
    )
    op.create_index("ix_review_tasks_status_created", "review_tasks", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_review_tasks_status_created", table_name="review_tasks")
    op.drop_table("review_tasks")
