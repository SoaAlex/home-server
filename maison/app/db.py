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


def init_schema(url: str | None = None) -> None:
    with connect(url) as conn:
        conn.execute(SCHEMA.read_text())
