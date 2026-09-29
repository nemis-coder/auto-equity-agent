"""Mensajes de la conversación. No son fuente de aprobación.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("actors.id")),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("reply_to", UUID),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('customer', 'assistant')", name="ck_messages_role"),
        sa.CheckConstraint("char_length(content) <= 4000", name="ck_messages_length"),
    )
    op.create_index("ix_messages_case", "messages", ["case_id", "created_at"])
    op.create_index(
        "uq_messages_one_reply",
        "messages",
        ["reply_to"],
        unique=True,
        postgresql_where=sa.text("reply_to IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("messages")
