"""Print a database's schema, read-only, so two databases can be compared before `alembic stamp`.

Run from the repo root: python print_schema.py
DATABASE_URL comes from the shell if it is set there, else from .env. The schema goes to stdout; the
host, database and Alembic revision go to stderr, so the output compares cleanly with
migrations/baseline_schema.txt, the schema that revision 0001 creates:

    DATABASE_URL='<url>' python print_schema.py | diff migrations/baseline_schema.txt -

No output from diff means the schemas are identical.
"""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


def print_table(inspector, table: str) -> None:
    """One table: columns sorted by name (a column added by ALTER TABLE sits last), then constraints and indexes."""
    print(f"table {table}")
    for column in sorted(inspector.get_columns(table), key=lambda c: c["name"]):
        # As PostgreSQL spells it, so "TIMESTAMP WITHOUT TIME ZONE" and "... WITH TIME ZONE" differ.
        sql_type = column["type"].compile(dialect=inspector.dialect)
        print(f"  column {column['name']}: {sql_type}, nullable={column['nullable']}, default={column['default']}")
    print(f"  primary key: {inspector.get_pk_constraint(table)['constrained_columns']}")
    for unique in sorted(inspector.get_unique_constraints(table), key=lambda u: u["name"]):
        print(f"  unique {unique['name']}: {unique['column_names']}")
    for index in sorted(inspector.get_indexes(table), key=lambda i: i["name"]):
        print(f"  index {index['name']}: {index['column_names']}, unique={index['unique']}")


def main() -> None:
    load_dotenv()  # never overrides a DATABASE_URL already set in the shell
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    parsed = urlparse(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        # First statement of the transaction: everything after it is read-only, so this script can't change anything.
        connection.execute(text("SET TRANSACTION READ ONLY"))
        inspector = inspect(connection)
        tables = inspector.get_table_names()
        revision = None
        if "alembic_version" in tables:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"host: {parsed.hostname}, database: {parsed.path.lstrip('/')}, alembic revision: {revision}",
              file=sys.stderr)
        for table in sorted(t for t in tables if t != "alembic_version"):
            print_table(inspector, table)
    engine.dispose()


if __name__ == "__main__":
    main()
