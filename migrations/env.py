from alembic import context
from app.db import database_url, make_engine
from app.domain.models import Base

config = context.config


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      compare_type=True, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = make_engine()
    with engine.connect() as connection:
        run(connection)
    engine.dispose()
