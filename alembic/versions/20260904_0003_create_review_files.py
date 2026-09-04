"""Create review file metadata."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260904_0003"
down_revision: str | None = "20260904_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_files",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("review_task_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("path_hash", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("previous_path", sa.Text()),
        sa.Column(
            "status",
            sa.Enum(
                "ADDED",
                "MODIFIED",
                "REMOVED",
                "RENAMED",
                "COPIED",
                "CHANGED",
                "UNCHANGED",
                name="filechangestatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("changes", sa.Integer(), nullable=False),
        sa.Column("patch_digest", sa.String(64)),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column(
            "skip_reason",
            sa.Enum(
                "REMOVED",
                "EXCLUDED_PATH",
                "MISSING_PATCH",
                "TOO_LARGE",
                name="fileskipreason",
                native_enum=False,
                length=30,
            ),
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="CASCADE"),
        sa.CheckConstraint("position >= 0", name="ck_review_file_position_non_negative"),
        sa.CheckConstraint(
            "additions >= 0 AND deletions >= 0 AND changes >= 0",
            name="ck_review_file_counts_non_negative",
        ),
        sa.CheckConstraint(
            "(selected = 1 AND skip_reason IS NULL) OR (selected = 0 AND skip_reason IS NOT NULL)",
            name="ck_review_file_selection_reason",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_task_id", "path_hash", name="uq_review_file_task_path"),
    )
    op.create_index(
        "ix_review_files_task_selected",
        "review_files",
        ["review_task_id", "selected"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_files_task_selected", table_name="review_files")
    op.drop_table("review_files")
