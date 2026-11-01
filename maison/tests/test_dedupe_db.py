"""Déduplication en base : empreintes des fichiers, fusion, séparation, refus, vue du groupe."""
import os

from app import db, dedupe
from app.ingest import upsert
from tests.dedupe_helpers import jpeg, scene


def put_photos(base, lid, seeds):
    d = base / str(lid)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, s in enumerate(seeds):
        (d / f"{i:02d}.jpg").write_bytes(jpeg(scene(s)))
        paths.append(f"{lid}/{i:02d}.jpg")
    return paths


def add(conn, base=None, seeds=(), **kw):
    ext = kw.pop("ext", str(len(seeds)) + str(kw.get("price")))
    item = {"source": "bienici", "external_id": ext, "url": f"https://x/{ext}", "city": "Wambrechies",
            "lat": 50.68, "lon": 3.05, "geo_precision": "city", **kw}
    lid, _ = upsert(conn, item)
    if base is not None and seeds:
        from psycopg.types.json import Jsonb
        conn.execute("UPDATE listing SET photos=%s WHERE id=%s", (Jsonb(put_photos(base, lid, seeds)), lid))
    return lid


def test_ensure_hashes_decodes_each_file_once_and_rehashes_changed_file(dburl, tmp_path):
    calls = []

    def spy(data):
        calls.append(len(data))
        return dedupe.image_hashes(data)

    with db.connect(dburl) as conn:
        lid = add(conn, tmp_path, seeds=(1, 2, 3), price=300000)
        assert dedupe.ensure_hashes(conn, [lid], base=tmp_path, decode=spy) == 3
        assert dedupe.ensure_hashes(conn, [lid], base=tmp_path, decode=spy) == 0
        assert len(calls) == 3                                   # second appel : aucun décodage
        f = tmp_path / str(lid) / "01.jpg"
        f.write_bytes(jpeg(scene(42)))
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 5))
        assert dedupe.ensure_hashes(conn, [lid], base=tmp_path, decode=spy) == 1
        row = conn.execute("SELECT phash FROM listing_photo WHERE listing_id=%s AND idx=1", (lid,)).fetchone()
        assert dedupe.from_db(row["phash"]) == dedupe.image_hashes(jpeg(scene(42)))[0]
        # photo retirée de la liste : sa ligne d'empreinte disparaît
        conn.execute("UPDATE listing SET photos=%s::jsonb WHERE id=%s", (f'["{lid}/00.jpg"]', lid))
        dedupe.ensure_hashes(conn, [lid], base=tmp_path, decode=spy)
        n = conn.execute("SELECT count(*) AS n FROM listing_photo WHERE listing_id=%s", (lid,)).fetchone()["n"]
    assert n == 1


def test_ensure_hashes_marks_uniform_images_generic(dburl, tmp_path):
    from tests.dedupe_helpers import uniform
    with db.connect(dburl) as conn:
        lid = add(conn, tmp_path, seeds=(1,), price=310000)
        (tmp_path / str(lid) / "01.jpg").write_bytes(jpeg(uniform()))
        conn.execute("UPDATE listing SET photos=%s::jsonb WHERE id=%s", (f'["{lid}/00.jpg", "{lid}/01.jpg"]', lid))
        dedupe.ensure_hashes(conn, [lid], base=tmp_path)
        rows = conn.execute("SELECT idx, generic FROM listing_photo WHERE listing_id=%s ORDER BY idx", (lid,)).fetchall()
    assert [r["generic"] for r in rows] == [False, True]


# ---------- 4.1 fusion, séparation, refus ----------
def group(conn, pid):
    return sorted(dedupe.group_ids(conn, pid))


def test_merging_two_groups_repoints_all_members_without_chain(dburl):
    with db.connect(dburl) as conn:
        p1, m1, p2, m2 = (add(conn, price=300000 + k, ext=f"g{k}") for k in range(4))
        dedupe.merge(conn, p1, m1)
        dedupe.merge(conn, p2, m2)
        keep = dedupe.merge(conn, m1, m2)
        rows = conn.execute("SELECT id, merged_into FROM listing ORDER BY id").fetchall()
        members = group(conn, keep)
    assert members == sorted([p1, m1, p2, m2])
    assert all(r["merged_into"] in (None, keep) for r in rows)          # groupes plats
    assert sum(r["merged_into"] is None for r in rows) == 1


def test_joining_listing_never_changes_existing_principal(dburl):
    with db.connect(dburl) as conn:
        bi = add(conn, price=300000, ext="bi")
        nt = add(conn, price=300000, ext="nt", source="netty")
        conn.execute("UPDATE listing SET enrichment_status='done' WHERE id=%s", (bi,))
        sl = add(conn, price=301000, ext="sl", source="seloger")
        assert dedupe.merge(conn, sl, bi, method="auto") == bi            # Bien'ici enrichie > carte e-mail
        assert dedupe.merge(conn, nt, bi, method="auto") == bi            # Netty rejoint : principale inchangée


def test_user_validated_address_wins_principal_choice(dburl):
    with db.connect(dburl) as conn:
        nt = add(conn, price=300000, ext="nt", source="netty")
        sl = add(conn, price=300000, ext="sl", source="seloger")
        conn.execute("""UPDATE listing SET enrichment='{"address_method": "user"}' WHERE id=%s""", (sl,))
        assert dedupe.merge(conn, nt, sl) == sl


def test_rejection_blocks_automatic_merge_and_manual_merge_lifts_it(dburl, tmp_path):
    with db.connect(dburl) as conn:
        a = add(conn, tmp_path, seeds=(1, 2, 3), price=300000, surface_m2=100, ext="a")
        b = add(conn, tmp_path, seeds=(1, 2, 4), price=300000, surface_m2=100, ext="b", source="netty")
        dedupe.ensure_hashes(conn, base=tmp_path)
        dedupe.reject(conn, a, b)
        assert dedupe.analyze(conn, changed_only=False)["ops"] == []
        dedupe.merge(conn, a, b, method="user")
        st = conn.execute("SELECT status, decided_by FROM duplicate_pair WHERE id_a=%s AND id_b=%s",
                          (min(a, b), max(a, b))).fetchone()
    assert st == {"status": "merged", "decided_by": "user"}


def test_analyze_downgrades_chaining_onto_incompatible_group(dburl, tmp_path):
    with db.connect(dburl) as conn:
        c = add(conn, tmp_path, seeds=(70,), price=300000, surface_m2=113, ext="c")
        b = add(conn, tmp_path, seeds=(80, 81), price=300000, surface_m2=104, ext="b")
        dedupe.merge(conn, b, c, method="user")
        a = add(conn, tmp_path, seeds=(80, 81, 82), price=300000, surface_m2=100, ext="a")
        dedupe.ensure_hashes(conn, base=tmp_path)
        res = dedupe.analyze(conn, ids=[a])
        stats = dedupe.apply(conn, res)
        pend = conn.execute("SELECT id_a, id_b, status, evidence FROM duplicate_pair WHERE status='suggested'").fetchall()
        merged_into = conn.execute("SELECT merged_into FROM listing WHERE id=%s", (a,)).fetchone()["merged_into"]
    assert stats["merged"] == 0 and merged_into is None
    assert len(pend) == 1 and "veto dans le groupe" in pend[0]["evidence"]["rule"]


def test_split_restores_member_and_remembers_refusal(dburl, tmp_path):
    with db.connect(dburl) as conn:
        a = add(conn, tmp_path, seeds=(1, 2), price=300000, surface_m2=100, ext="a")
        b = add(conn, tmp_path, seeds=(1, 2), price=295000, surface_m2=100, ext="b", source="seloger")
        dedupe.ensure_hashes(conn, base=tmp_path)
        dedupe.apply(conn, dedupe.analyze(conn, changed_only=False))
        assert conn.execute("SELECT merged_into FROM listing WHERE id=%s", (b,)).fetchone()["merged_into"] == a
        dedupe.split(conn, a, b)
        row = conn.execute("SELECT merged_into, price FROM listing WHERE id=%s", (b,)).fetchone()
        cl = conn.execute("SELECT cluster FROM listing WHERE id=%s", (a,)).fetchone()["cluster"]
        again = dedupe.analyze(conn, changed_only=False)
    assert row == {"merged_into": None, "price": 295000} and cl is None
    assert again["ops"] == []                       # ni refusion ni nouvelle suggestion


# ---------- 4.2 données utilisateur ----------
def test_user_data_folded_on_principal_and_member_keeps_its_own(dburl):
    with db.connect(dburl) as conn:
        p = add(conn, price=300000, ext="p", source="netty")
        m1 = add(conn, price=300000, ext="m1", source="seloger")
        m2 = add(conn, price=300000, ext="m2", source="leboncoin")
        conn.execute("UPDATE listing SET reaction='dislike', user_note='trop bruyant', seen_at='2026-10-01' WHERE id=%s", (p,))
        conn.execute("UPDATE listing SET reaction='like', user_status='to_visit', user_note='belle cuisine', "
                     "seen_at='2026-09-15', alerted_at='2026-09-20' WHERE id=%s", (m1,))
        conn.execute("UPDATE listing SET reaction='dislike', alerted_at='2026-10-05' WHERE id=%s", (m2,))
        keep = dedupe.merge(conn, p, m1)
        keep = dedupe.merge(conn, keep, m2)
        pr = conn.execute("SELECT reaction, user_status, user_note, seen_at::date::text AS s, "
                          "alerted_at::date::text AS a FROM listing WHERE id=%s", (keep,)).fetchone()
        mm = conn.execute("SELECT reaction, user_note FROM listing WHERE id=%s", (m1,)).fetchone()
    assert keep == p                                     # données utilisateur des deux côtés → source Netty
    assert pr == {"reaction": "like", "user_status": "to_visit", "user_note": "trop bruyant\nbelle cuisine",
                  "s": "2026-09-15", "a": "2026-09-20"}                  # 👍 > 👎, dates les plus anciennes
    assert mm == {"reaction": "like", "user_note": "belle cuisine"}       # la secondaire garde ses valeurs


def test_rejected_house_coming_back_stays_disliked_and_not_new(dburl):
    with db.connect(dburl) as conn:
        old = add(conn, price=300000, ext="old")
        conn.execute("UPDATE listing SET reaction='dislike', seen_at=now() - interval '20 days', "
                     "first_seen=now() - interval '30 days' WHERE id=%s", (old,))
        new = add(conn, price=300000, ext="new", source="seloger")
        keep = dedupe.merge(conn, new, old, method="auto")
        r = conn.execute("SELECT reaction, seen_at IS NOT NULL AS seen FROM listing WHERE id=%s", (keep,)).fetchone()
    assert keep == old and r == {"reaction": "dislike", "seen": True}


def test_refresh_clusters_materialises_group_view(dburl, tmp_path):
    with db.connect(dburl) as conn:
        a = add(conn, tmp_path, seeds=(5, 6), price=241500, surface_m2=85, ext="a")
        b = add(conn, tmp_path, seeds=(5,), price=235000, surface_m2=85, ext="b", source="seloger", land_m2=179)
        dedupe.ensure_hashes(conn, base=tmp_path)
        keep = dedupe.merge(conn, a, b, method="auto")
        cl = conn.execute("SELECT cluster FROM listing WHERE id=%s", (keep,)).fetchone()["cluster"]
        assert dedupe.refresh_clusters(conn) == 1
        member_cl = conn.execute("SELECT cluster FROM listing WHERE id=%s", (b,)).fetchone()["cluster"]
    assert keep == a and member_cl is None
    assert cl["price"] == {"value": 235000, "offer_id": b} and cl["members"] == [b]
    assert cl["photos"] == [f"/photos/{a}/00.jpg", f"/photos/{a}/01.jpg"]     # vignette identique non répétée
    assert cl["fill"]["land_m2"] == 179 and cl["active"] and cl["sources"] == ["bienici", "seloger"]
