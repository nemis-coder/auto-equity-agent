from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import Actor, ActorRole

TOKEN_BYTES = 32


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Identity:
    actor_id: uuid.UUID
    alias: str
    role: ActorRole
    customer_id: uuid.UUID | None


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def resolve_identity(session: AsyncSession, token: str) -> Identity | None:
    digest = hash_token(token)
    actor = await session.scalar(
        select(Actor).where(Actor.token_hash == digest, Actor.active.is_(True))
    )
    if actor is None or not hmac.compare_digest(actor.token_hash, digest):
        return None
    return Identity(
        actor_id=actor.id, alias=actor.alias, role=actor.role, customer_id=actor.customer_id
    )
