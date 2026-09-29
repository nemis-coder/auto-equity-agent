from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from app.persistence.models import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
