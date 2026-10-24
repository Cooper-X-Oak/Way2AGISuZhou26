"""Shared engine configuration for repositories, migrations and import."""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def database_url():
    return os.environ.get("APP_DB_URL", "sqlite:///" + (ROOT / "data/app.db").as_posix())


def make_engine(url=None):
    url = make_url(url or database_url())
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def migrate(engine):
    from alembic import command
    from alembic.config import Config
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
