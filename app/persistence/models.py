from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ActorRole(enum.StrEnum):
    CUSTOMER = "CUSTOMER"
    ADVISOR = "ADVISOR"
    SERVICE = "SERVICE"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Actor(Base):
    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint(
            "(role = 'CUSTOMER') = (customer_id IS NOT NULL)",
            name="ck_actors_customer_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[ActorRole] = mapped_column(Enum(ActorRole, name="actor_role"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    assigned_advisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actors.id")
    )
    source_system: Mapped[str] = mapped_column(String(32), default="demo")
    external_case_ref: Mapped[str | None] = mapped_column(String(64))
    workflow_state: Mapped[str] = mapped_column(String(32))
    wait_reason: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    input_revision: Mapped[int] = mapped_column(Integer, default=0)
    policy_version: Mapped[str] = mapped_column(String(32))
    declared_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    eligibility: Mapped[dict | None] = mapped_column(JSONB)
    simulation_result: Mapped[dict | None] = mapped_column(JSONB)
    last_failed_validation_fp: Mapped[str | None] = mapped_column(String(64))
    correction_rounds: Mapped[int] = mapped_column(Integer, default=0)
    ready_verdict: Mapped[dict | None] = mapped_column(JSONB)
    advisor_request: Mapped[dict | None] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tokens_reserved_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_reserved_out: Mapped[int] = mapped_column(Integer, default=0)
    token_allotments: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), unique=True
    )
    vehicle_ref: Mapped[str | None] = mapped_column(String(40))
    make: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(60))
    year: Mapped[int | None] = mapped_column(Integer)
    declared_owner_name: Mapped[str | None] = mapped_column(String(200))
    owned_by_customer: Mapped[bool | None] = mapped_column(Boolean)
    blocking_debt: Mapped[bool | None] = mapped_column(Boolean)
    has_second_key: Mapped[bool | None] = mapped_column(Boolean)
    confirmed_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(String(32))
    group_name: Mapped[str] = mapped_column(String(16))
    typed_payload: Mapped[dict] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    base_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    confirmed_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts()


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key", name="uq_operations_key"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    action: Mapped[str] = mapped_column(String(48))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    http_status: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class CaseEvent(Base):
    __tablename__ = "case_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    case_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(48))
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    delegated_for: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    correlation_id: Mapped[str] = mapped_column(String(64))
    safe_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _ts()


class CreditProfile(Base):
    __tablename__ = "credit_profiles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_ref: Mapped[str] = mapped_column(String(128))
    score: Mapped[int] = mapped_column(Integer)
    history_summary: Mapped[dict] = mapped_column(JSONB)
    conditions: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(48))
    band: Mapped[str | None] = mapped_column(String(8))
    annual_nominal_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    max_financed_principal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _ts()


class KeyQuote(Base):
    __tablename__ = "key_quotes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    vehicle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vehicles.id"))
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("operations.id"))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    quote_ref: Mapped[str] = mapped_column(String(128))
    provider_ref: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _ts()


class Simulation(Base):
    """Una fila por oferta."""

    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_profiles.id")
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("key_quotes.id")
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    key_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    financed_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    annual_nominal_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    term_months: Mapped[int] = mapped_column(Integer)
    regular_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    last_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    total_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    schedule: Mapped[list] = mapped_column(JSONB)
    display: Mapped[dict] = mapped_column(JSONB)
    display_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _ts()


class Selection(Base):
    __tablename__ = "selections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    offer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    displayed_offer_hash: Mapped[str] = mapped_column(String(64))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MockProviderLedger(Base):
    """Registro durable del proveedor simulado, en transacción separada de la del caso."""

    __tablename__ = "mock_provider_ledger"

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    effects: Mapped[int] = mapped_column(Integer, default=0)
    response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(String(24))
    slot: Mapped[str] = mapped_column(String(24))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(200))
    mime_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    active: Mapped[bool] = mapped_column(Boolean)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    created_at: Mapped[datetime] = _ts()


class DocumentExtraction(Base):
    """Inmutable: una corrección crea otra fila y marca la anterior como SUPERSEDED."""

    __tablename__ = "document_extractions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_sha256: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    extraction: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    human_override_actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actors.id")
    )
    created_at: Mapped[datetime] = _ts()


class Validation(Base):
    __tablename__ = "validations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    rule_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(8))
    reason_code: Mapped[str] = mapped_column(String(48))
    evidence_refs: Mapped[list] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _ts()


class HumanReview(Base):
    """Una sola fila OPEN por caso (índice parcial único). La resolución cierra la fila."""

    __tablename__ = "human_reviews"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id"))
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(48))
    resume_state: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    opened_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("actors.id"))
    opened_at: Mapped[datetime] = _ts()
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actors.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolution_payload: Mapped[dict | None] = mapped_column(JSONB)
