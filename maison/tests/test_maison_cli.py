"""CLI de l'agent (scripts/maison_cli.py) : entrée allégée de `maison pending`."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("maison_cli", Path(__file__).resolve().parent.parent / "scripts" / "maison_cli.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

COMMENT = {"author": "Marie", "body": "La maison est au bout de l'impasse", "created_at": "2026-11-08T10:00:00+00:00",
           "updated_at": None}


def test_slim_entry_keeps_user_comments():
    x = {"id": 1, "photo_urls": [f"https://e/{i}.jpg" for i in range(12)], "enrichment": {}, "user_comments": [COMMENT]}
    out = cli.slim_entry(x, "standard")
    assert out["user_comments"] == [COMMENT] and len(out["photo_urls"]) == 8


def test_slim_entry_without_comments_gives_empty_list():
    assert cli.slim_entry({"id": 2}, "types")["user_comments"] == []
