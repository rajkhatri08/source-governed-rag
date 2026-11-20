from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from db import is_unique_violation


class PostgresError(Exception):
    """What psycopg2 attaches to a failed statement: the SQLSTATE code and the table Postgres names."""

    def __init__(self, pgcode: str, table_name: str):
        super().__init__(pgcode)
        self.pgcode = pgcode
        self.diag = SimpleNamespace(table_name=table_name)


def integrity_error(pgcode: str, table_name: str) -> IntegrityError:
    return IntegrityError("INSERT INTO documents ...", {}, PostgresError(pgcode, table_name))


def test_a_duplicate_is_recognised_by_its_sqlstate_and_table_not_by_a_constraint_name():
    assert is_unique_violation(integrity_error("23505", "documents"), "documents")


def test_a_duplicate_in_another_table_or_a_different_violation_is_not_a_duplicate_document():
    assert not is_unique_violation(integrity_error("23505", "audit_log"), "documents")
    assert not is_unique_violation(integrity_error("23502", "documents"), "documents")  # 23502: a NOT NULL violation
