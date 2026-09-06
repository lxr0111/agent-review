"""Create structured review reports and findings."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260906_0004"
down_revision: str | None = "20260904_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_reports",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("review_task_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum(
                "NONE",
                "LOW",
                "MEDIUM",
                "HIGH",
                "CRITICAL",
                name="reviewrisklevel",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(255)),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("reviewed_file_count", sa.Integer(), nullable=False),
        sa.Column("skipped_file_count", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "reviewed_file_count >= 0 AND skipped_file_count >= 0",
            name="ck_review_report_file_counts_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_task_id", name="uq_review_reports_review_task_id"),
    )
    op.create_index(
        "ix_review_reports_risk_generated",
        "review_reports",
        ["risk_level", "generated_at"],
    )

    op.create_table(
        "review_findings",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("review_report_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_path_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "LOW",
                "MEDIUM",
                "HIGH",
                "CRITICAL",
                name="findingseverity",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "SECURITY",
                "CORRECTNESS",
                "RELIABILITY",
                "PERFORMANCE",
                "MAINTAINABILITY",
                "TESTING",
                name="findingcategory",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["review_report_id"],
            ["review_reports.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("position >= 0", name="ck_review_finding_position_non_negative"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_review_finding_confidence_range",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_report_id",
            "position",
            name="uq_review_finding_report_position",
        ),
    )
    op.create_index(
        "ix_review_findings_report_severity",
        "review_findings",
        ["review_report_id", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_findings_report_severity", table_name="review_findings")
    op.drop_table("review_findings")
    op.drop_index("ix_review_reports_risk_generated", table_name="review_reports")
    op.drop_table("review_reports")
