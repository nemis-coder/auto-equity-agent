"""Perfil crediticio, cotización de llave, ofertas, selección y ledger de proveedores mock.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(14, 2)
RATE = sa.Numeric(8, 6)


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.add_column("cases", sa.Column("simulation_result", postgresql.JSONB))
    op.create_table(
        "credit_profiles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("operation_id", UUID, sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("provider_ref", sa.String(128), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("history_summary", postgresql.JSONB, nullable=False),
        sa.Column("conditions", postgresql.JSONB, nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(48), nullable=False),
        sa.Column("band", sa.String(8)),
        sa.Column("annual_nominal_rate", RATE),
        sa.Column("max_financed_principal", MONEY),
        sa.Column("obtained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        _created(),
        sa.CheckConstraint("status IN ('OK', 'REVIEW', 'SUPERSEDED')", name="ck_profile_status"),
        sa.CheckConstraint(
            "(outcome = 'OK') = (annual_nominal_rate IS NOT NULL "
            "AND max_financed_principal IS NOT NULL)",
            name="ck_profile_terms",
        ),
    )
    op.create_index("ix_credit_profiles_case", "credit_profiles", ["case_id", "obtained_at"])

    op.create_table(
        "key_quotes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("vehicle_id", UUID, sa.ForeignKey("vehicles.id"), nullable=False),
        sa.Column("operation_id", UUID, sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("quote_ref", sa.String(128), nullable=False),
        sa.Column("provider_ref", sa.String(128), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        _created(),
        sa.CheckConstraint("amount >= 0", name="ck_quote_amount"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_quote_dates"),
        sa.CheckConstraint(
            "status IN ('VALID', 'MALFORMED', 'SUPERSEDED')", name="ck_quote_status"
        ),
    )

    op.create_table(
        "simulations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("batch_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, sa.ForeignKey("credit_profiles.id"), nullable=False),
        sa.Column("quote_id", UUID, sa.ForeignKey("key_quotes.id")),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("cash_amount", MONEY, nullable=False),
        sa.Column("key_cost", MONEY, nullable=False),
        sa.Column("financed_principal", MONEY, nullable=False),
        sa.Column("annual_nominal_rate", RATE, nullable=False),
        sa.Column("term_months", sa.Integer, nullable=False),
        sa.Column("regular_payment", MONEY, nullable=False),
        sa.Column("last_payment", MONEY, nullable=False),
        sa.Column("total_payment", MONEY, nullable=False),
        sa.Column("schedule", postgresql.JSONB, nullable=False),
        sa.Column("display", postgresql.JSONB, nullable=False),
        sa.Column("display_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        _created(),
        sa.UniqueConstraint("case_id", "id", name="uq_simulations_case_id"),
        sa.CheckConstraint(
            "financed_principal = cash_amount + key_cost", name="ck_simulation_principal"
        ),
        # Un costo de llave positivo exige cotización; una cotización gratuita explícita es válida.
        sa.CheckConstraint("key_cost = 0 OR quote_id IS NOT NULL", name="ck_simulation_key"),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name="ck_simulation_status"),
    )

    op.create_table(
        "selections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("offer_id", UUID, nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("actors.id"), nullable=False),
        sa.Column("displayed_offer_hash", sa.String(64), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        # La oferta elegida debe pertenecer al mismo caso (TDD §6.1).
        sa.ForeignKeyConstraint(["case_id", "offer_id"], ["simulations.case_id", "simulations.id"]),
    )
    op.create_index(
        "uq_selection_one_active",
        "selections",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "mock_provider_ledger",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("effects", sa.Integer, nullable=False, server_default="0"),
        sa.Column("response", postgresql.JSONB),
        _created(),
        sa.UniqueConstraint("provider", "idempotency_key", name="uq_ledger_key"),
    )


def downgrade() -> None:
    op.drop_table("mock_provider_ledger")
    op.drop_table("selections")
    op.drop_table("simulations")
    op.drop_table("key_quotes")
    op.drop_table("credit_profiles")
    op.drop_column("cases", "simulation_result")
