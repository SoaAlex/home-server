from app import db


def test_schema_creates_listing_table(dburl):
    with db.connect(dburl) as conn:
        row = conn.execute("SELECT count(*) AS n FROM listing").fetchone()
    assert row["n"] == 0


def test_schema_is_idempotent_and_has_dedupe_objects(dburl):
    assert db.init_schema(dburl) and db.init_schema(dburl)          # deux applications de suite
    with db.connect(dburl) as conn:
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='listing'").fetchall()}
        tables = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    assert {"merged_into", "merged_at", "merge_method", "cluster", "dedupe_sig"} <= cols
    assert {"listing_photo", "duplicate_pair"} <= tables


def test_schema_has_adjacency_objects(dburl):
    assert db.init_schema(dburl) and db.init_schema(dburl)
    with db.connect(dburl) as conn:
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='listing'").fetchall()}
        idx = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='listing'").fetchall()}
    assert {"adjacency", "property_type_src"} <= cols
    assert "listing_type_request_idx" in idx
