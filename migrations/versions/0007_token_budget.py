"""Presupuesto de tokens por caso (TDD §5.4, supuesto S10).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

COLUMNS = ("tokens_in", "tokens_out", "tokens_reserved_in", "tokens_reserved_out")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("cases", sa.Column(name, sa.Integer, nullable=False, server_default="0"))
    op.add_column(
        "cases", sa.Column("token_allotments", sa.Integer, nullable=False, server_default="1")
    )
    op.create_check_constraint(
        "ck_cases_token_counters",
        "cases",
        " AND ".join(f"{c} >= 0" for c in COLUMNS) + " AND token_allotments >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_cases_token_counters", "cases")
    for name in (*COLUMNS, "token_allotments"):
        op.drop_column("cases", name)
