# ============================================================================
# FILE:       backend/alembic/env.py
# PURPOSE:    Configure and execute Alembic database migrations.
#             Connects to PostgreSQL using settings.database_url and diffs
#             Base.metadata against the live database schema.
# HOW IT WORKS: Supports both offline mode (generates SQL script without DB)
#             and online mode (connects via async SQLAlchemy engine and runs DDL).
# USED BY / DEPENDS ON: alembic upgrade, alembic downgrade, app/config.py,
#             app/models/__init__.py.
# KEY CONCEPTS: Schema migrations, AsyncEngine run_sync bridge, autogenerate diff.
# ============================================================================

"""Alembic environment configuration for async SQLAlchemy migrations."""

# asyncio runs the async engine connection within a synchronous Alembic context.
import asyncio

# logging provides diagnostic log output matching alembic.ini configuration.
from logging.config import fileConfig

# op and context are Alembic's migration harness and operational interface.
from alembic import context

# pool and engine from SQLAlchemy.
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import application settings to retrieve the verified database URL.
from app.config import settings

# Import the declarative Base with all models registered onto Base.metadata.
from app.models import Base

# Alembic Config object holding values from alembic.ini.
config = context.config

# Percent signs are special in a ConfigParser value, so a password such as
# "p%40ss" has to be doubled before it is stored in the configuration.
def _escape_url(url: str) -> str:
    """Escape a connection URL for ConfigParser interpolation.

    Parameters:
        url (str): the raw database URL.

    Returns:
        str: the URL with every "%" doubled.
    """
    return url.replace("%", "%%")

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    # Set up Python loggers as declared in alembic.ini.
    fileConfig(config.config_file_name)

# target_metadata is what Alembic inspects for autogenerating migrations.
target_metadata = Base.metadata

# Inject the application's DATABASE_URL dynamically so no passwords live in alembic.ini.
# NOTE: the value is escaped for ConfigParser interpolation — a password that
# contains "%" would otherwise raise an InterpolationSyntaxError.
config.set_main_option("sqlalchemy.url", _escape_url(settings.database_url))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine, though an Engine
    is acceptable here as well. By skipping the Engine creation we don't even
    need a DBAPI to be available. Calls to context.execute() emit the given
    string to the script output.
    """
    # Retrieve the configured database URL.
    url = config.get_main_option("sqlalchemy.url")

    # Configure the Alembic context for SQL script generation.
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    # Run migrations within an offline transaction block.
    with context.begin_transaction():
        # Emit DDL statements to standard output / migration script.
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Helper callback that performs migration execution within a sync connection.

    Parameters:
        connection (Connection): Synchronous connection wrapper passed by run_sync.
    """
    # Configure context bound to the live active database connection.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    # Begin transaction and apply all pending migrations.
    with context.begin_transaction():
        # Execute migration upgrade or downgrade steps.
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an AsyncEngine and run migrations using run_sync.

    Alembic operates synchronously, so we create an async engine and use
    connection.run_sync() to bridge into synchronous migration methods.
    """
    # Build dictionary of configuration options from alembic.ini.
    configuration = config.get_section(config.config_ini_section, {})

    # Ensure sqlalchemy.url uses our application's database URL.
    configuration["sqlalchemy.url"] = settings.database_url

    # Create the async engine with NullPool so connections are discarded immediately.
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Open an async connection from the engine.
    async with connectable.connect() as connection:
        # Run the synchronous migration runner on the connection.
        await connection.run_sync(do_run_migrations)

    # Dispose of the engine resources cleanly.
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using asyncio.run.

    Selects online execution when a database connection is requested.
    """
    # Run the async migration runner within the event loop.
    asyncio.run(run_async_migrations())


# Determine whether Alembic was invoked in offline mode or online mode.
if context.is_offline_mode():
    # Execute offline SQL script generation.
    run_migrations_offline()
else:
    # Execute online live database migration.
    run_migrations_online()
