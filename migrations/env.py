"""Alembic environment — runs migrations against the DSN the deployment supplies.

Owns: opening the connection Alembic applies revisions through.
Must not own: the revisions themselves (`migrations/versions/`), or any
knowledge of the server's runtime objects.

Deliberately schema-less: there is no SQLAlchemy model metadata here and no
`--autogenerate` support. The server's schema is written by hand in the revision
files, because autogeneration compares against declarative models this codebase
does not have — the persistence layer is hand-written SQL by design, and a
half-true model definition maintained only to feed autogenerate is a second
source of truth that will drift from the statements actually executed.

Runs async, over the same `asyncpg` driver the server uses, so a DSN that works
for one works for the other and there is no second driver to keep installed.
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.engine import Connection
from sqlalchemy import pool

ENV_POSTGRES_DSN = "KFCHESS_POSTGRES_DSN"

# SQLAlchemy needs the driver named in the URL scheme; the server's asyncpg DSN
# does not carry one. Normalising here means one environment variable configures
# both rather than an operator having to keep two spellings of the same DSN in
# step.
_ASYNCPG_SCHEME = "postgresql+asyncpg://"
_PLAIN_SCHEMES = ("postgresql://", "postgres://")

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """The DSN to migrate, from the environment rather than from alembic.ini.

    A URL committed to the ini file is either wrong everywhere or right in one
    environment and carrying its password into version control.
    """
    dsn = os.environ.get(ENV_POSTGRES_DSN)
    if not dsn:
        raise RuntimeError(
            f"{ENV_POSTGRES_DSN} must be set to the database to migrate, "
            "e.g. postgresql://kfchess:***@postgres:5432/kfchess"
        )
    for scheme in _PLAIN_SCHEMES:
        if dsn.startswith(scheme):
            return _ASYNCPG_SCHEME + dsn[len(scheme):]
    return dsn


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of applying it.

    Used to review what a deploy will do to a production database before it does
    it, which is the only safe way to read a destructive migration.
    """
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _apply(connection: Connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply every pending revision, then close the engine.

    NullPool because this process applies migrations once and exits; a pool
    would keep connections open against a database the job is done with.
    """
    config.set_main_option("sqlalchemy.url", _database_url())
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with engine.connect() as connection:
        await connection.run_sync(_apply)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
