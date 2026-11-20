"""Alembic's entry point: runs the migrations against DATABASE_URL, or against the eval database.

    alembic upgrade head               DATABASE_URL (a value set in the shell wins over .env)
    alembic -x db=eval upgrade head    the local compliance_eval database, checked by evals/eval_db.py
    alembic upgrade 0001:head --sql    print the SQL instead of running it; connects to nothing
"""
import logging
import os
from logging.config import fileConfig
from urllib.parse import urlparse

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

from evals.eval_db import use_eval_database

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

if context.get_x_argument(as_dictionary=True).get("db") == "eval":
    use_eval_database()  # DATABASE_URL becomes the checked eval database; must run before db.py is imported
load_dotenv()  # never overrides a DATABASE_URL already set in the shell

from db import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Write the SQL to stdout instead of running it. Needs no database and no URL."""
    context.configure(dialect_name="postgresql", target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against DATABASE_URL, in one transaction: if one fails, none of them is applied."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    parsed = urlparse(url)
    logger.info(f"database: {parsed.hostname}/{parsed.path.lstrip('/')}")
    # Built from the URL directly. Passing it through Alembic's config would break on the "%" of a
    # percent-encoded password, which ConfigParser reads as interpolation.
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
