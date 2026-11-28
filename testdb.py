"""The test database: the tests and `alembic -x db=test` use it, and refuse anything else.

The tests' twin of evals/eval_db.py. TEST_DATABASE_URL comes from the shell if it is set there, else .env.
"""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

TEST_DATABASE_NAME = "compliance_test"


def check_test_url(url: str) -> None:
    """Stop unless the URL names the compliance_test database and is not Neon (production).

    The name check also keeps the tests off the development database, compliance.
    """
    parsed = urlparse(url)
    if "neon.tech" in (parsed.hostname or ""):
        raise SystemExit("TEST_DATABASE_URL points at Neon, which is production: refusing")
    if parsed.path.lstrip("/") != TEST_DATABASE_NAME:
        raise SystemExit(f"TEST_DATABASE_URL must name the database {TEST_DATABASE_NAME}: refusing")


def use_test_database() -> str:
    """Make DATABASE_URL the test database for this process, and return its host.

    Must run before anything imports db.py, which builds its engine from DATABASE_URL on import.
    """
    if "db" in sys.modules:
        raise SystemExit("db.py was imported before use_test_database(), so it would use the wrong database")
    load_dotenv()
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("TEST_DATABASE_URL is not set in .env")
    check_test_url(url)
    os.environ["DATABASE_URL"] = url
    return urlparse(url).hostname
