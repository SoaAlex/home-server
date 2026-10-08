from app import db


def test_schema_creates_listing_table(dburl):
    with db.connect(dburl) as conn:
        row = conn.execute("SELECT count(*) AS n FROM listing").fetchone()
    assert row["n"] == 0
