from __future__ import annotations

import uuid

from pydantic import BaseModel


class MeResponse(BaseModel):
    actor_id: uuid.UUID
    alias: str
    role: str
    permissions: list[str]


class ReadyResponse(BaseModel):
    status: str
    checks: dict[str, str]
    clock: dict[str, str] | None = None
