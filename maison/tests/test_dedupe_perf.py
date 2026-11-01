"""Coût du traitement continu (spec listing-deduplication : « Coût borné du traitement continu »)."""
import random
import time

from app import db, dedupe


def test_new_listing_of_ten_photos_compared_to_12000_hashes_in_under_5s(dburl):
    rnd = random.Random(1)
    with db.connect(dburl) as conn:
        conn.execute("""INSERT INTO listing (source, external_id, url, price, surface_m2, rooms, bedrooms, city,
                        postal_code, lat, lon, geo_precision, description, dedupe_sig)
                        SELECT 'bienici', 'x' || g, 'https://x/' || g, 200000 + g * 150, 70 + g % 90, 4 + g % 3,
                               2 + g % 3, 'Wambrechies', '59118', 50.60 + (g % 100) / 1000.0,
                               3.00 + (g % 70) / 1000.0, 'city', repeat('maison familiale lumineuse ', 5), NULL
                        FROM generate_series(1, 1500) g""")
        ids = [r["id"] for r in conn.execute("SELECT id FROM listing ORDER BY id")]
        rows = [(lid, k, f"{lid}/{k:02d}.jpg", dedupe.to_db(rnd.getrandbits(64)), dedupe.to_db(rnd.getrandbits(64)), 40.0)
                for lid in ids for k in range(8)]
        with conn.cursor() as cur:
            with cur.copy("COPY listing_photo (listing_id, idx, path, phash, dhash, std) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row(r)
        items = dedupe.load_items(conn)                 # état stable : signatures à jour (analyse déjà passée)
        for i, sig in dedupe.signatures(items, dedupe.load_hashes(conn, list(items))).items():
            conn.execute("UPDATE listing SET dedupe_sig=%s WHERE id=%s", (sig, i))
        new = conn.execute("""INSERT INTO listing (source, external_id, url, price, surface_m2, city, lat, lon)
                              VALUES ('seloger', 'new', 'https://n', 300000, 100, 'Wambrechies', 50.65, 3.03)
                              RETURNING id""").fetchone()["id"]
        for k in range(10):
            dedupe.store_hash(conn, new, k, f"{new}/{k:02d}.jpg", (rnd.getrandbits(64), rnd.getrandbits(64), 40.0))
        assert conn.execute("SELECT count(*) AS n FROM listing_photo").fetchone()["n"] == 12010
        t0 = time.monotonic()
        res = dedupe.analyze(conn, changed_only=True)
        stats = dedupe.apply(conn, res)
        elapsed = time.monotonic() - t0
    assert res["changed"] == [new] and stats["compared"] == 1
    assert elapsed < 5, elapsed
    print(f"comparaison incrémentale : {elapsed:.2f} s")
