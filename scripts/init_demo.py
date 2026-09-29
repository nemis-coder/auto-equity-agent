"""Crea clientes y actores sintéticos de demo de forma idempotente.

Los tokens se muestran una sola vez en DEMO_CREDENTIALS_PATH (modo 0600). En la base
solo se guarda su SHA-256. No crea casos ni estados finales.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.persistence.models import Actor, ActorRole, Customer  # noqa: E402
from app.security.auth import hash_token, new_token  # noqa: E402

CUSTOMERS = {
    "cliente-ana": {
        "full_name": "Ana Prueba López",
        "address": {
            "street": "Calle Demo",
            "external_number": "123",
            "internal_number": None,
            "neighborhood": "Colonia Ejemplo",
            "municipality": "Ciudad de México",
            "state": "CDMX",
            "postal_code": "00000",
            "country": "MX",
        },
    },
    "cliente-beto": {
        "full_name": "Beto Ejemplo Ruiz",
        "address": {
            "street": "Avenida Ficticia",
            "external_number": "45",
            "internal_number": "2B",
            "neighborhood": "Colonia Muestra",
            "municipality": "Guadalajara",
            "state": "Jalisco",
            "postal_code": "00001",
            "country": "MX",
        },
    },
}
STAFF = {"asesor-1": ActorRole.ADVISOR, "agente-servicio": ActorRole.SERVICE}


def _load(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(path: Path, creds: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "+psycopg")


def init_demo(database_url: str, credentials_path: Path, rotate: set[str]) -> dict[str, str]:
    creds = _load(credentials_path)
    engine = create_engine(_sync_url(database_url))
    created: dict[str, str] = {}
    with Session(engine) as session, session.begin():
        for alias, data in CUSTOMERS.items():
            actor = session.scalar(select(Actor).where(Actor.alias == alias))
            if actor is None:
                customer = Customer(full_name=data["full_name"], address=data["address"])
                session.add(customer)
                session.flush()
                token = new_token()
                session.add(
                    Actor(
                        alias=alias,
                        role=ActorRole.CUSTOMER,
                        customer_id=customer.id,
                        token_hash=hash_token(token),
                    )
                )
                created[alias] = token
            elif alias in rotate:
                token = new_token()
                actor.token_hash = hash_token(token)
                created[alias] = token
        for alias, role in STAFF.items():
            actor = session.scalar(select(Actor).where(Actor.alias == alias))
            if actor is None:
                token = new_token()
                session.add(Actor(alias=alias, role=role, token_hash=hash_token(token)))
                created[alias] = token
            elif alias in rotate:
                token = new_token()
                actor.token_hash = hash_token(token)
                created[alias] = token
    engine.dispose()
    if created:
        creds.update(created)
        _save(credentials_path, creds)
    missing = sorted((set(CUSTOMERS) | set(STAFF)) - set(creds))
    for alias in missing:
        print(f"Aviso: no hay token guardado para {alias}; usa --rotate {alias}.")
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate", action="append", default=[], help="Alias a regenerar.")
    args = parser.parse_args(argv)
    path = Path(os.environ.get("DEMO_CREDENTIALS_PATH", "/data/demo-credentials.json"))
    created = init_demo(os.environ["DATABASE_URL"], path, set(args.rotate))
    if created:
        print(f"Tokens nuevos para {', '.join(sorted(created))} guardados en {path}.")
    else:
        print("Actores de demo ya existentes; no se generaron tokens nuevos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
