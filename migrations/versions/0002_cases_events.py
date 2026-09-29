"""Casos, vehículo, propuestas, operaciones idempotentes y auditoría append-only.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
STATES = (
    "VEHICLE_ELIGIBILITY",
    "PROFILING",
    "SIMULATION",
    "DOCUMENT_COLLECTION",
    "DOCUMENT_VALIDATION",
    "NEEDS_CORRECTION",
    "HUMAN_REVIEW",
    "READY_FOR_FINANCIAL",
    "REJECTED",
)
WAITS = (
    "NONE",
    "CUSTOMER_INPUT",
    "DOCUMENT_UPLOAD",
    "PROVIDER_RETRY",
    "PROVIDER_UNKNOWN",
    "MODEL_UNAVAILABLE",
    "BUDGET_EXCEEDED",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("customer_id", UUID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("assigned_advisor_id", UUID, sa.ForeignKey("actors.id")),
        sa.Column("source_system", sa.String(32), nullable=False, server_default="demo"),
        sa.Column("external_case_ref", sa.String(64)),
        sa.Column("workflow_state", sa.String(32), nullable=False),
        sa.Column("wait_reason", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("input_revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("declared_profile", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("eligibility", postgresql.JSONB),
        sa.Column("correction_rounds", sa.Integer, nullable=False, server_default="0"),
        _created(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in("workflow_state", STATES), name="ck_cases_state"),
        sa.CheckConstraint(_in("wait_reason", WAITS), name="ck_cases_wait"),
        sa.CheckConstraint("version >= 1 AND input_revision >= 0", name="ck_cases_counters"),
    )
    op.create_index("ix_cases_customer", "cases", ["customer_id"])
    op.create_index("ix_cases_advisor", "cases", ["assigned_advisor_id"])

    op.create_table(
        "vehicles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False, unique=True),
        sa.Column("vehicle_ref", sa.String(40)),
        sa.Column("make", sa.String(60)),
        sa.Column("model", sa.String(60)),
        sa.Column("year", sa.Integer),
        sa.Column("declared_owner_name", sa.String(200)),
        sa.Column("owned_by_customer", sa.Boolean),
        sa.Column("blocking_debt", sa.Boolean),
        sa.Column("has_second_key", sa.Boolean),
        sa.Column("confirmed_event_id", UUID),
    )

    op.create_table(
        "pending_actions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("group_name", sa.String(16), nullable=False),
        sa.Column("typed_payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_message_id", UUID),
        sa.Column("created_by", UUID, sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("base_version", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("confirmed_event_id", UUID),
        _created(),
        sa.CheckConstraint(
            _in("status", ("PENDING", "CONFIRMED", "SUPERSEDED")), name="ck_pending_status"
        ),
        sa.CheckConstraint(_in("group_name", ("vehicle", "profile")), name="ck_pending_group"),
    )
    # Una sola propuesta pendiente por caso.
    op.create_index(
        "uq_pending_one_per_case",
        "pending_actions",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "operations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("actor_id", UUID, sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id")),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("http_status", sa.Integer),
        sa.Column("result", postgresql.JSONB),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("provider_ref", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        _created(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_operations_key"),
        sa.CheckConstraint(
            _in(
                "status",
                ("PENDING", "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL", "UNKNOWN"),
            ),
            name="ck_operations_status",
        ),
    )

    op.create_table(
        "case_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("case_version", sa.Integer, nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("delegated_for", UUID),
        sa.Column("operation_id", UUID),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("safe_payload", postgresql.JSONB, nullable=False, server_default="{}"),
        _created(),
    )
    op.create_index("ix_case_events_case", "case_events", ["case_id", "case_version"])

    # Append-only incluso para el dueño de la tabla. La purga de demo (S9) debe activar
    # explícitamente app.allow_event_purge en su transacción.
    op.execute(
        """
        CREATE FUNCTION case_events_append_only() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND current_setting('app.allow_event_purge', true) = 'on' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'case_events es append-only (%)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_case_events_append_only
        BEFORE UPDATE OR DELETE ON case_events
        FOR EACH ROW EXECUTE FUNCTION case_events_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_case_events_no_truncate
        BEFORE TRUNCATE ON case_events
        FOR EACH STATEMENT EXECUTE FUNCTION case_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_case_events_no_truncate ON case_events")
    op.execute("DROP TRIGGER IF EXISTS trg_case_events_append_only ON case_events")
    op.execute("DROP FUNCTION IF EXISTS case_events_append_only()")
    op.drop_table("case_events")
    op.drop_table("operations")
    op.drop_table("pending_actions")
    op.drop_table("vehicles")
    op.drop_table("cases")
