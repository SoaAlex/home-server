from app import db
from app.ingest import upsert, mark_gone


def L(**kw):
    base = dict(source="bienici", external_id="x1", url="https://e/x1", price=400000,
                city="Wambrechies", surface_m2=120)
    return {**base, **kw}


def test_insert_then_price_change(dburl):
    with db.connect(dburl) as conn:
        id1, new1 = upsert(conn, L())
        id2, new2 = upsert(conn, L(price=390000))
        row = conn.execute("SELECT price, price_history FROM listing WHERE id=%s", (id1,)).fetchone()
    assert new1 and not new2 and id1 == id2
    assert row["price"] == 390000 and row["price_history"][0]["price"] == 400000


def test_partial_update_keeps_known_values(dburl):
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, L(dpe="C"))
        upsert(conn, L(dpe=None, price=None))
        row = conn.execute("SELECT price, dpe FROM listing WHERE id=%s", (lid,)).fetchone()
    assert row["dpe"] == "C" and row["price"] == 400000


def test_exact_position_not_overwritten_by_blurred(dburl):
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, L(lat=50.0, lon=3.0, geo_precision="approx"))
        conn.execute("UPDATE listing SET lat=50.5, lon=3.5, geo_precision='exact' WHERE id=%s", (lid,))
        upsert(conn, L(lat=50.1, lon=3.1, geo_precision="approx"))
        row = conn.execute("SELECT lat, lon, geo_precision FROM listing WHERE id=%s", (lid,)).fetchone()
    assert (row["lat"], row["lon"], row["geo_precision"]) == (50.5, 3.5, "exact")


def test_mark_gone(dburl):
    with db.connect(dburl) as conn:
        upsert(conn, L())
        conn.execute("UPDATE listing SET last_seen = now() - interval '2 hours'")
        n = mark_gone(conn, "bienici", seen_ids={"other"})
    assert n == 1
