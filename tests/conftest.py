"""What the tests run against: the test database, or no database at all; never the development database or Neon.

pytest loads this file before it imports any test file, so DATABASE_URL is set here before db.py, which
builds its engine from DATABASE_URL when it is first imported, is imported by anything else.
"""
import os
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from testdb import check_test_url

load_dotenv()  # never overrides a value set in the shell
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if TEST_DATABASE_URL:
    check_test_url(TEST_DATABASE_URL)  # stops on Neon, or on any database but compliance_test
elif os.getenv("CI"):  # GitHub Actions sets CI=true
    raise SystemExit("TEST_DATABASE_URL is not set: on CI the database tests must run, not be skipped")
# Every test, not only the database tests, runs against the test database or against a host that can't exist
# (.invalid never resolves): a test that reaches the database by mistake fails instead of writing somewhere real.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or "postgresql://no-test-database.invalid/none"

import db  # noqa: E402  (only now: it reads DATABASE_URL when it is imported)

ALEMBIC_INI = Path(__file__).parent.parent / "alembic.ini"
TABLES = ["documents", "document_chunks", "audit_log"]


@pytest.fixture(scope="session")
def test_database() -> None:
    """Checked once per run: the test database answers, is at the latest migration, and is empty.

    Without TEST_DATABASE_URL, every test that needs it is skipped, and pytest's summary counts them.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set, so the database tests don't run (CLAUDE.md, Run locally)")
    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    try:
        connection = db.engine.connect()
    except OperationalError as e:
        pytest.exit(f"can't connect to compliance_test: {e.orig}")
    with connection:
        try:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except ProgrammingError:  # no alembic_version table: no migration has run on it
            revision = None
        if revision != head:
            pytest.exit(f"compliance_test is at revision {revision}, not {head}: "
                        "run `alembic -x db=test upgrade head`")
        counts = {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar() for table in TABLES}
    if any(counts.values()):
        pytest.exit(f"compliance_test should be empty but holds rows {counts}; the tests never delete "
                    "anything, so remove them by hand")


@pytest.fixture
def database(test_database, monkeypatch):
    """A connection to the test database. Everything the test writes, commits included, is undone afterwards.

    The test runs inside one transaction, rolled back at the end, and every session db.py opens joins it:
    a commit then only commits a savepoint inside it, and a rollback (after a duplicate insert, say)
    only undoes back to that savepoint. So no test ever deletes anything, and the database stays empty.
    """
    connection = db.engine.connect()
    transaction = connection.begin()
    monkeypatch.setattr(db, "Session", sessionmaker(bind=connection, join_transaction_mode="create_savepoint"))
    yield connection
    transaction.rollback()
    connection.close()
