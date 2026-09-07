"""Create review publication tracking."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0005"
down_revision: str | None = "20260906_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_publications",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("review_report_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("GITHUB", name="reviewpublicationprovider", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PUBLISHED",
                "FAILED",
                name="reviewpublicationstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("external_review_id", sa.BigInteger()),
        sa.Column("external_review_url", sa.Text()),
        sa.Column("external_state", sa.String(50)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["review_report_id"],
            ["review_reports.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_review_publication_attempt_count_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_report_id",
            "provider",
            name="uq_review_publication_report_provider",
        ),
        sa.UniqueConstraint(
            "provider",
            "external_review_id",
            name="uq_review_publication_provider_external_id",
        ),
    )
    op.create_index(
        "ix_review_publications_status_updated",
        "review_publications",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_publications_status_updated", table_name="review_publications")
    op.drop_table("review_publications")
