"""Point this process at the eval database, and refuse anything else."""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

EVAL_DATABASE_NAME = "compliance_eval"


def check_eval_url(url: str | None) -> None:
    """Stop unless the URL names the compliance_eval database and is not Neon (production)."""
    if not url:
        raise SystemExit("EVAL_DATABASE_URL is not set in .env")
    parsed = urlparse(url)
    if "neon.tech" in (parsed.hostname or ""):
        raise SystemExit("EVAL_DATABASE_URL points at Neon, which is production: refusing")
    if parsed.path.lstrip("/") != EVAL_DATABASE_NAME:
        raise SystemExit(f"EVAL_DATABASE_URL must name the database {EVAL_DATABASE_NAME}: refusing")


def use_eval_database() -> str:
    """Make DATABASE_URL the eval database for this process, and return its host.

    Must run before anything imports db.py, which builds its engine from DATABASE_URL on import.
    """
    if "db" in sys.modules:
        raise SystemExit("db.py was imported before use_eval_database(), so it would use the wrong database")
    load_dotenv()
    url = os.getenv("EVAL_DATABASE_URL")
    check_eval_url(url)
    os.environ["DATABASE_URL"] = url
    return urlparse(url).hostname
