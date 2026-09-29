"""Reconciliación entre PostgreSQL y el almacenamiento de documentos (TDD §6.9).

La carga escribe primero los bytes y después la fila: un fallo de la base puede dejar un objeto
huérfano, nunca un documento sin bytes. Este comando:

- lista objetos bajo `cases/` sin fila en `documents` (huérfanos) y los borra solo si se pide
  y si superan un periodo de gracia (una carga en curso aún no tiene su fila);
- reporta filas cuyo objeto falta (grave: se investiga, no se «repara» en silencio).

Usa tiempo técnico, no el reloj de negocio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.persistence.models import Document
from app.storage.interfaces import DocumentStore

PREFIX = "cases/"


@dataclass
class ReconcileReport:
    scanned: int = 0
    referenced: int = 0
    orphans: list[str] = field(default_factory=list)
    recent_unreferenced: list[str] = field(default_factory=list)
    missing_objects: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


async def reconcile(
    sessionmaker: async_sessionmaker[AsyncSession],
    store: DocumentStore,
    *,
    grace: timedelta = timedelta(hours=1),
    delete: bool = False,
    now: datetime | None = None,
    prefix: str = PREFIX,
) -> ReconcileReport:
    now = now or datetime.now(UTC)
    report = ReconcileReport()
    async with sessionmaker() as session:
        keys = set(
            await session.scalars(
                select(Document.storage_key).where(Document.storage_key.startswith(prefix))
            )
        )
    objects = await store.list_objects(prefix)
    report.scanned, stored = len(objects), {k for k, _ in objects}
    for key, modified in sorted(objects):
        if key in keys:
            report.referenced += 1
        elif now - modified >= grace:
            report.orphans.append(key)
        else:
            report.recent_unreferenced.append(key)
    report.missing_objects = sorted(keys - stored)
    if delete:
        for key in report.orphans:
            await store.delete(key)
            report.deleted.append(key)
    return report
