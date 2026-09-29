"""Documentos, extracciones y validaciones documentales.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("slot", sa.String(24), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("page_count", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("supersedes_id", UUID),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("actors.id"), nullable=False),
        _created(),
        sa.UniqueConstraint("case_id", "id", name="uq_documents_case_id"),
        sa.CheckConstraint(
            "kind IN ('IDENTITY', 'PAYSLIP', 'INCOME_STATEMENT', 'VEHICLE_OWNERSHIP')",
            name="ck_documents_kind",
        ),
        sa.CheckConstraint("size_bytes > 0 AND page_count > 0", name="ck_documents_size"),
    )
    op.create_index(
        "uq_documents_one_active_per_slot",
        "documents",
        ["case_id", "slot"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "document_extractions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("document_id", UUID, nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("extraction", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("supersedes_id", UUID),
        sa.Column("human_override_actor_id", UUID, sa.ForeignKey("actors.id")),
        _created(),
        sa.ForeignKeyConstraint(["case_id", "document_id"], ["documents.case_id", "documents.id"]),
        sa.CheckConstraint("status IN ('CURRENT', 'SUPERSEDED')", name="ck_extraction_status"),
    )
    op.create_index(
        "uq_extraction_current_per_document",
        "document_extractions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CURRENT'"),
    )

    op.create_table(
        "validations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("rule_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("reason_code", sa.String(48), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB, nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        _created(),
        sa.CheckConstraint("status IN ('PASS', 'FAIL', 'UNKNOWN')", name="ck_validation_status"),
    )
    op.create_index("ix_validations_case_active", "validations", ["case_id", "active"])
    op.add_column("cases", sa.Column("last_failed_validation_fp", sa.String(64)))


def downgrade() -> None:
    op.drop_column("cases", "last_failed_validation_fp")
    op.drop_table("validations")
    op.drop_table("document_extractions")
    op.drop_table("documents")
