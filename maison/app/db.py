from contextlib import contextmanager
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from app import settings

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@contextmanager
def connect(url: str | None = None):
    with psycopg.connect(url or settings.DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def init_schema(url: str | None = None, lock_timeout: str | None = None) -> bool:
    """Applique db/schema.sql (idempotent). Avec lock_timeout (web), abandonne au lieu d'attendre derrière
    une longue transaction du collecteur — attendre provoquait un deadlock ALTER TABLE ↔ upserts."""
    try:
        with connect(url) as conn:
            if lock_timeout:
                conn.execute(f"SET lock_timeout = '{lock_timeout}'")
            conn.execute(SCHEMA.read_text())
        return True
    except psycopg.errors.LockNotAvailable:
        return False
