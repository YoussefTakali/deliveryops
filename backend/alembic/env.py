import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app import models  # noqa: E402,F401  registers all tables on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The database URL always comes from app settings (DATABASE_URL env var), never from alembic.ini,
# so migrations run against whatever the application is actually configured for.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


# Skip generating an empty migration file when there is no schema change to record.
def process_revision_directives(context, revision, directives):
    if directives and directives[0].upgrade_ops.is_empty():
        directives[:] = []


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True, compare_server_default=True,
            process_revision_directives=process_revision_directives,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
