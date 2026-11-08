import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True, pool_size=10, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SchemaOutOfDate(RuntimeError):
    pass


def _check_migrations() -> None:
    """Production safety net: refuse to serve traffic against a database whose schema doesn't
    match the code. The real fix is running `alembic upgrade head` as a deploy step, not here —
    this only turns a silent data-corrupting mismatch into a startup failure."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    if current != head:
        raise SchemaOutOfDate(
            f"Database schema (revision {current!r}) does not match the application (expects {head!r}). "
            "Run `alembic upgrade head` before starting the API."
        )


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    if settings.is_postgres:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")

    if settings.is_production:
        _check_migrations()
        return

    # Dev/test convenience: create tables directly from the models, no migration step needed.
    # Production always goes through Alembic (see docker-entrypoint.sh) so schema changes are
    # reviewed, ordered and reversible.
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """create_all() never alters an existing table, so a dev database created before a model gained
    a column would fail on every query touching it. Add such columns (dev/test only — production
    uses Alembic): with the model's scalar default when it has one, nullable otherwise."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=engine.dialect)}'
                default = column.default.arg if column.default is not None and column.default.is_scalar else None
                if isinstance(default, bool):
                    ddl += f" NOT NULL DEFAULT {'TRUE' if default else 'FALSE'}"
                elif isinstance(default, (int, float)):
                    ddl += f" NOT NULL DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " NOT NULL DEFAULT '" + default.replace("'", "''") + "'"
                conn.exec_driver_sql(ddl)
                log.warning("dev schema: added missing column %s.%s", table.name, column.name)
