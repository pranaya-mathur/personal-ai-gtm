from sqlalchemy import create_engine

import app.models  # noqa: F401  (register tables)
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported")
run_migrations_online()
