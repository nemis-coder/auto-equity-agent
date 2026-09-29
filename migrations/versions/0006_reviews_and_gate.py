"""Revisión humana, dictamen del gate final y solicitud de corrección del asesor.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB

RESOLUTIONS = ("REQUEST_CORRECTION", "AMEND_EXTRACTION", "RECONCILE_OPERATION", "RESUME")


def upgrade() -> None:
    op.create_table(
        "human_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(48), nullable=False),
        sa.Column("resume_state", sa.String(32), nullable=False),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        sa.Column("opened_by", UUID, sa.ForeignKey("actors.id"), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_by", UUID, sa.ForeignKey("actors.id")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.String(32)),
        sa.Column("resolution_payload", JSONB),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_reviews_status"),
        sa.CheckConstraint(
            "(status = 'OPEN') = (resolution IS NULL AND resolved_at IS NULL)",
            name="ck_reviews_resolution_when_closed",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ("
            + ", ".join(f"'{r}'" for r in RESOLUTIONS)
            + ")",
            name="ck_reviews_resolution",
        ),
    )
    # Una sola revisión abierta por caso (TDD §6.8).
    op.create_index(
        "uq_human_reviews_open",
        "human_reviews",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index("ix_human_reviews_status", "human_reviews", ["status", "opened_at"])
    op.add_column("cases", sa.Column("ready_verdict", JSONB))
    op.add_column("cases", sa.Column("advisor_request", JSONB))
    # Casos que ya estaban en revisión antes de esta migración: se les crea su fila abierta
    # a partir del último evento HUMAN_REVIEW_REQUESTED, para que el asesor pueda resolverlos.
    op.execute(
        """
        INSERT INTO human_reviews
            (id, case_id, status, reason_code, resume_state, details, opened_by, opened_at)
        SELECT gen_random_uuid(), c.id, 'OPEN',
               coalesce(e.safe_payload->>'reason_code', 'LEGACY'),
               coalesce(e.safe_payload->>'resume_state', 'PROFILING'),
               '{"backfilled": true}'::jsonb, e.actor_id, e.created_at
        FROM cases c
        JOIN LATERAL (
            SELECT * FROM case_events ce
            WHERE ce.case_id = c.id AND ce.event_type = 'HUMAN_REVIEW_REQUESTED'
            ORDER BY ce.case_version DESC, ce.created_at DESC
            LIMIT 1
        ) e ON true
        WHERE c.workflow_state = 'HUMAN_REVIEW'
        """
    )


def downgrade() -> None:
    op.drop_column("cases", "advisor_request")
    op.drop_column("cases", "ready_verdict")
    op.drop_table("human_reviews")
