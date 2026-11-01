import pytest
from fastapi.testclient import TestClient
from app import db, settings
from app.ingest import upsert


@pytest.fixture
def client(dburl, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "ENRICH_TOKEN", "tok")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    from app import web
    from app.web import app
    monkeypatch.setattr(web, "postprocess", lambda *a, **k: None)   # pas d'appels réseau
    with db.connect(dburl) as conn:
        upsert(conn, dict(source="bienici", external_id="a", url="https://e/a", price=400000,
                          city="Wambrechies", has_garage=True, has_garden=True))
        upsert(conn, dict(source="bienici", external_id="b", url="https://e/b", price=300000,
                          city="Roubaix"))
        upsert(conn, dict(source="leboncoin", external_id="c", url="https://lbc/c"))
        conn.execute("UPDATE listing SET score=80 WHERE external_id='a'")
        conn.execute("UPDATE listing SET excluded_reason='ville exclue', enrichment_status='skipped' WHERE external_id='b'")
        conn.execute("UPDATE listing SET excluded_reason='à compléter' WHERE external_id='c'")
    with TestClient(app) as c:
        yield c


def test_listings_hide_excluded(client):
    ids = [r["source"] + r["url"][-1] for r in client.get("/api/listings").json()]
    assert ids == ["bienicia", "leboncoinc"]
    assert len(client.get("/api/listings?show_excluded=1").json()) == 3


def test_pending_requires_token(client):
    assert client.get("/api/enrichment/pending").status_code == 401
    r = client.get("/api/enrichment/pending", headers={"X-Token": "tok"})
    assert r.status_code == 200 and {x["url"] for x in r.json()} == {"https://e/a", "https://lbc/c"}


def test_enrichment_completes_email_listing(client):
    lid = next(x["id"] for x in client.get("/api/listings").json() if x["source"] == "leboncoin")
    body = {"city": "Lambersart", "postal_code": "59130", "price": 420000, "has_garage": True,
            "has_garden": True, "style": "traditional", "summary_fr": "Belle maison"}
    r = client.post(f"/api/listings/{lid}/enrichment", json=body, headers={"X-Token": "tok"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["excluded_reason"] is None and out["score"] > 0 and out["enrichment_status"] == "done"
    full = client.get(f"/api/listings/{lid}").json()
    assert full["city"] == "Lambersart" and full["enrichment"]["style"] == "traditional"


def test_tools_require_token(client):
    assert client.get("/api/tools/ademe?qs=x").status_code == 401
    assert client.get("/api/tools/ban?q=x").status_code == 401


def test_user_address_locks_and_request_prioritised(client, monkeypatch):
    from app import web
    monkeypatch.setattr(web.geo, "geocode", lambda c, q, **k: {"lat": 50.7, "lon": 3.05, "label": "218 Rue du Château Valois 59118 Wambrechies", "citycode": "59636"})
    lst = client.get("/api/listings").json()
    a = next(x for x in lst if x["source"] == "bienici")["id"]
    c = next(x for x in lst if x["source"] == "leboncoin")["id"]
    # demande d'investigation : passe en tête de la file d'attente
    assert client.post(f"/api/listings/{c}/address_request", json={"note": "doute"}).status_code == 200
    first = client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()[0]
    assert first["id"] == c and first["enrichment"]["address_request"]["note"] == "doute"
    # correction manuelle : géocodée, exacte, verrouillée
    r = client.post(f"/api/listings/{a}/address", json={"address": "218 rue du Château Valois Wambrechies"})
    assert r.status_code == 200 and r.json()["address"].startswith("218 Rue du Château Valois")
    client.post(f"/api/listings/{a}/enrichment", headers={"X-Token": "tok"},
                json={"address": "1 rue Fausse", "lat": 1, "lon": 1, "address_confidence": "probable", "style": "modern"})
    full = client.get(f"/api/listings/{a}").json()
    assert full["address"].startswith("218") and full["enrichment"]["address_method"] == "user"
    assert full["enrichment"]["style"] == "modern" and full["geo_precision"] == "exact"


def test_reaction_like_dislike(client):
    lid = client.get("/api/listings").json()[0]["id"]
    assert client.post(f"/api/listings/{lid}/reaction", json={"reaction": "like"}).json()["reaction"] == "like"
    assert client.get(f"/api/listings/{lid}").json()["reaction"] == "like"
    assert client.post(f"/api/listings/{lid}/reaction", json={"reaction": "bof"}).status_code == 400
    client.post(f"/api/listings/{lid}/reaction", json={"reaction": "dislike"})
    pend = client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()
    assert lid not in [x["id"] for x in pend]          # pas de tokens dépensés sur une maison rejetée


def test_seen_clears_new_flag(client):
    lid = client.get("/api/listings").json()[0]["id"]
    assert client.get(f"/api/listings/{lid}").json()["seen_at"] is None
    first = client.post(f"/api/listings/{lid}/seen").json()["seen_at"]
    assert first and client.post(f"/api/listings/{lid}/seen").json()["seen_at"] == first   # idempotent


def test_user_status(client):
    lid = client.get("/api/listings").json()[0]["id"]
    assert client.post(f"/api/listings/{lid}/user", json={"user_status": "to_visit"}).status_code == 200
    assert client.get(f"/api/listings/{lid}").json()["user_status"] == "to_visit"


def test_pending_puts_recent_listings_first(client, dburl):
    with db.connect(dburl) as conn:   # « a » (score 80) devient ancienne ; « c » (sans score) est récente
        conn.execute("UPDATE listing SET first_seen = now() - interval '5 days' WHERE external_id='a'")
    pend = client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()
    assert [x["url"] for x in pend] == ["https://lbc/c", "https://e/a"]


def _merged_pair(dburl, enrich_principal=None):
    from app import dedupe
    with db.connect(dburl) as conn:
        p, _ = upsert(conn, dict(source="netty", external_id="p", url="https://e/p", price=400000, city="Wambrechies",
                                 surface_m2=100, has_garage=True, has_garden=True))
        m, _ = upsert(conn, dict(source="seloger", external_id="m", url="https://e/m", price=395000,
                                 city="Wambrechies", surface_m2=100))
        if enrich_principal:
            from psycopg.types.json import Jsonb
            conn.execute("UPDATE listing SET enrichment=%s, enrichment_status='done' WHERE id=%s",
                         (Jsonb(enrich_principal), p))
        assert dedupe.merge(conn, m, p, method="auto") == p
    return p, m


def test_pending_never_returns_merged_sources(client, dburl):
    p, m = _merged_pair(dburl)
    ids = {x["id"] for x in client.get("/api/enrichment/pending?limit=50", headers={"X-Token": "tok"}).json()}
    assert p in ids and m not in ids


def test_enrichment_posted_for_merged_source_is_kept_on_it(client, dburl):
    p, m = _merged_pair(dburl, enrich_principal={"summary_fr": "Analyse de la principale", "style": "traditional"})
    r = client.post(f"/api/listings/{m}/enrichment", json={"summary_fr": "Analyse tardive", "style": "modern",
                                                          "land_m2": 300}, headers={"X-Token": "tok"})
    assert r.status_code == 200, r.text
    with db.connect(dburl) as conn:
        rows = {x["id"]: x for x in conn.execute("SELECT id, enrichment, land_m2, cluster FROM listing "
                                                 "WHERE id = ANY(%s)", ([p, m],)).fetchall()}
    assert rows[p]["enrichment"]["summary_fr"] == "Analyse de la principale" and rows[p]["enrichment"]["style"] == "traditional"
    assert rows[m]["enrichment"]["summary_fr"] == "Analyse tardive" and float(rows[m]["land_m2"]) == 300
    assert rows[p]["cluster"]["fill"]["land_m2"] == 300          # groupe recalculé après le POST


# ---------- API des biens fusionnés (spec merged-listings) ----------
def test_list_returns_principals_with_offers(client, dburl):
    p, m = _merged_pair(dburl)
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert p in rows and m not in rows
    cl = rows[p]["cluster"]
    assert cl["price"] == {"value": 395000, "offer_id": m} and [o["id"] for o in cl["offers"]] == [m, p]
    assert rows[p]["dup_pending"] == 0


def test_principal_detail_has_members_and_member_detail_points_to_principal(client, dburl):
    p, m = _merged_pair(dburl)
    d = client.get(f"/api/listings/{p}").json()
    assert [x["id"] for x in d["members"]] == [m] and d["members"][0]["price"] == 395000 and d["suggestions"] == []
    assert client.get(f"/api/listings/{m}").json()["merged_into"] == p


def _suggested_pair(dburl):
    from app import dedupe
    with db.connect(dburl) as conn:
        a, _ = upsert(conn, dict(source="bienici", external_id="sa", url="https://e/sa", price=497000,
                                 city="Wambrechies", surface_m2=118))
        b, _ = upsert(conn, dict(source="seloger", external_id="sb", url="https://e/sb", price=497000,
                                 city="Wambrechies", surface_m2=118))
        dedupe.suggest(conn, a, b, {"photos": 0, "rule": "caractéristiques jumelles", "surface": [118, 118]})
    return a, b


def test_suggestion_listed_counted_and_filtered(client, dburl):
    a, b = _suggested_pair(dburl)
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert rows[a]["dup_pending"] == 1 and rows[b]["dup_pending"] == 1
    assert {x["id"] for x in client.get("/api/listings?dups=1").json()} == {a, b}
    s = client.get(f"/api/listings/{a}").json()["suggestions"]
    assert len(s) == 1 and s[0]["other_id"] == b and s[0]["evidence"]["rule"] == "caractéristiques jumelles"
    assert s[0]["other"]["price"] == 497000 and s[0]["other"]["source"] == "seloger"


def test_merge_route_merges_and_clears_suggestion(client, dburl):
    a, b = _suggested_pair(dburl)
    r = client.post(f"/api/listings/{a}/merge", json={"other_id": b})
    assert r.status_code == 200, r.text
    keep = r.json()["principal"]
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert keep == a and b not in rows and rows[a]["dup_pending"] == 0
    assert client.post(f"/api/listings/{a}/merge", json={"other_id": "x"}).status_code == 400


def test_not_duplicate_hides_suggestion_for_good(client, dburl):
    a, b = _suggested_pair(dburl)
    assert client.post(f"/api/listings/{b}/not_duplicate", json={"other_id": a}).status_code == 200
    assert client.get(f"/api/listings/{a}").json()["suggestions"] == []
    from app import dedupe
    with db.connect(dburl) as conn:
        assert dedupe.suggest(conn, a, b, {"photos": 1}) is False      # refus mémorisé : jamais reproposée


def test_split_route_restores_member_with_own_price(client, dburl):
    p, m = _merged_pair(dburl)
    r = client.post(f"/api/listings/{p}/split", json={"member_id": m})
    assert r.status_code == 200, r.text
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert m in rows and rows[m]["price"] == 395000 and rows[p]["cluster"] is None
    with db.connect(dburl) as conn:
        st = conn.execute("SELECT status FROM duplicate_pair WHERE id_a=%s AND id_b=%s", (min(p, m), max(p, m))).fetchone()
    assert st["status"] == "rejected"
    assert client.post(f"/api/listings/{p}/split", json={"member_id": m}).status_code == 404


def test_manual_merge_lifts_refusal(client, dburl):
    a, b = _suggested_pair(dburl)
    client.post(f"/api/listings/{a}/not_duplicate", json={"other_id": b})
    assert client.post(f"/api/listings/{b}/merge", json={"other_id": a}).status_code == 200
    with db.connect(dburl) as conn:
        st = conn.execute("SELECT status, decided_by FROM duplicate_pair WHERE id_a=%s AND id_b=%s",
                          (min(a, b), max(a, b))).fetchone()
    assert st == {"status": "merged", "decided_by": "user"}


def test_merge_two_designated_listings_and_unknown_id(client, dburl):
    with db.connect(dburl) as conn:
        a, _ = upsert(conn, dict(source="bienici", external_id="da", url="https://e/da", price=300000, city="Wambrechies"))
        b, _ = upsert(conn, dict(source="pap", external_id="db", url="https://e/db", price=320000, city="Wambrechies"))
    assert client.post(f"/api/listings/{a}/merge", json={"other_id": 999999}).status_code == 404
    r = client.post(f"/api/listings/{b}/merge", json={"other_id": a})
    assert r.status_code == 200 and {x["id"] for x in client.get("/api/listings").json()} & {a, b} == {r.json()["principal"]}
