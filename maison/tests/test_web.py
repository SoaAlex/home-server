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


def test_adjacency_tool_requires_token_and_writes_nothing(client, dburl, monkeypatch):
    from app import adjacency
    from tests.adjacency_helpers import building, house, plot, scene
    seen = []

    def fetch(c, point, address=None):
        seen.append((point, address))
        return scene([house(), building("R", 6, 0, 12, 9)], plot() + plot("PR", 6, 12))
    monkeypatch.setattr(adjacency, "fetch", fetch)
    assert client.get("/api/tools/adjacency?address=1+rue+de+l%27Exemple").status_code == 401
    assert seen == []
    with db.connect(dburl) as conn:
        before = conn.execute("SELECT id, adjacency, enrichment, score FROM listing ORDER BY id").fetchall()
    r = client.get("/api/tools/adjacency", params={"address": "1 rue de l'Exemple"}, headers={"X-Token": "tok"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert {"status", "type", "usable", "warnings", "target", "neighbours", "living_walls", "annex_links",
            "orientation", "input", "computed_at"} <= set(res) and "geometry" not in res
    assert res["type"] == "semi" and res["neighbours"][0]["side"] == "right"
    g = client.get("/api/tools/adjacency", params={"lon": -5.0, "lat": 46.0, "geometry": 1},
                   headers={"X-Token": "tok"}).json()
    assert g["geometry"]["type"] == "FeatureCollection" and seen[-1] == ((-5.0, 46.0), None)
    assert client.get("/api/tools/adjacency", headers={"X-Token": "tok"}).status_code == 400
    with db.connect(dburl) as conn:
        after = conn.execute("SELECT id, adjacency, enrichment, score FROM listing ORDER BY id").fetchall()
    assert after == before


def test_adjacency_tool_outage_is_a_502(client, monkeypatch):
    from app import adjacency

    def fetch(c, point, address=None):
        raise adjacency.FetchError("data.geopf.fr : HTTP 504")
    monkeypatch.setattr(adjacency, "fetch", fetch)
    r = client.get("/api/tools/adjacency", params={"lon": 3.1, "lat": 50.6}, headers={"X-Token": "tok"})
    assert r.status_code == 502 and "504" in r.json()["detail"]

    def unknown(c, point, address=None):
        raise adjacency.AddressNotFound("adresse introuvable dans la BAN (numéro inconnu)")
    monkeypatch.setattr(adjacency, "fetch", unknown)
    r = client.get("/api/tools/adjacency", params={"address": "Place de l'Exemple"}, headers={"X-Token": "tok"})
    assert r.status_code == 422 and "introuvable" in r.json()["detail"]


def _wms_client(monkeypatch, calls):
    """httpx simulé pour les routes web : le WMS de l'IGN renvoie une image grise unie."""
    import io
    import httpx
    from PIL import Image
    from app import web
    buf = io.BytesIO()
    Image.new("RGB", (700, 700), (128, 128, 128)).save(buf, "JPEG")
    real = httpx.Client

    def handler(req):
        calls.append(req)
        assert req.url.host == "data.geopf.fr" and req.url.params["LAYERS"] == "ORTHOIMAGERY.ORTHOPHOTOS"
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/jpeg"})
    monkeypatch.setattr(web.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))


def test_aerial_view_of_a_listing_is_drawn_and_cached(client, dburl, monkeypatch, tmp_path):
    import io
    from PIL import Image
    from app import adjacency
    from tests.adjacency_helpers import building, house, ll, plot, scene
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(adjacency, "fetch", lambda c, point, address=None: scene(
        [house(), building("R", 6, 0, 12, 9)], plot() + plot("PR", 6, 12)))
    lon, lat = ll(3, -2)
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, dict(source="bienici", external_id="aerial", url="https://e/aerial", city="Wambrechies",
                                   lat=lat, lon=lon, geo_precision="exact", address="1 rue de l'Exemple"))
        conn.execute("""UPDATE listing SET enrichment='{"address_confidence": "exact"}' WHERE id=%s""", (lid,))
        adjacency.process(conn, None)
    calls = []
    _wms_client(monkeypatch, calls)
    assert client.get(f"/api/tools/aerial?listing_id={lid}").status_code == 401
    r = client.get(f"/api/tools/aerial?listing_id={lid}", headers={"X-Token": "tok"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert img.size == (700, 700)
    r_, g_, b_ = img.getpixel((350, 350))                # vue centrée sur le bâtiment de l'annonce : bleu
    assert b_ > r_ + 40 and b_ > g_ + 25
    r_, g_, b_ = img.getpixel((410, 350))                # voisin accolé à 6 m à l'est : rouge
    assert r_ > b_ + 40 and r_ > g_ + 40
    lat0, lon0, lat1, lon1 = map(float, calls[0].url.params["BBOX"].split(","))
    assert lat0 < lat1 and lon0 < lon1 and 69 < adjacency._dist_m(lon0, lat0, lon0, lat1) < 71
    again = client.get(f"/api/tools/aerial?listing_id={lid}", headers={"X-Token": "tok"})
    assert again.content == r.content and len(calls) == 1          # second appel servi par le cache
    assert len(list((tmp_path / "aerial").glob(f"{lid}_*.jpg"))) == 1


def test_aerial_view_not_applicable_for_a_city_position(client, dburl, monkeypatch, tmp_path):
    from app import adjacency
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    calls = []
    _wms_client(monkeypatch, calls)
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, dict(source="seloger", external_id="city", url="https://e/city", city="Wambrechies",
                                   lat=50.68, lon=3.05, geo_precision="city"))
        adjacency.process(conn, None)
    r = client.get(f"/api/tools/aerial?listing_id={lid}", headers={"X-Token": "tok"})
    assert r.status_code == 404 and "non applicable" in r.json()["detail"] and calls == []
    assert client.get("/api/tools/aerial?listing_id=999999", headers={"X-Token": "tok"}).status_code == 404


def test_type_info_in_list_fiche_and_pending(client, dburl):
    from psycopg.types.json import Jsonb
    adj = {"v": 1, "status": "ok", "type": "terraced", "usable": True, "warnings": [], "input": {"confidence": "exact"},
           "neighbours": [{"kind": "living", "length_m": 9.0, "side": "left"}, {"kind": "living", "length_m": 8.0,
                                                                              "side": "right"}],
           "geometry": {"type": "FeatureCollection", "features": []}}
    with db.connect(dburl) as conn:
        lid = conn.execute("SELECT id FROM listing WHERE external_id='a'").fetchone()["id"]
        conn.execute("UPDATE listing SET adjacency=%s, property_type='detached', property_type_src='text_explicit', "
                     "description='Maison individuelle' WHERE id=%s", (Jsonb(adj), lid))
    item = next(x for x in client.get("/api/listings").json() if x["id"] == lid)
    assert item["type_info"]["type"] == "terraced" and item["type_info"]["source"] == "map"
    assert item["type_info"]["disagreement"] is True and "description" not in item
    assert item["adjacency"]["status"] == "ok" and "geometry" not in item["adjacency"]
    listing = next(v for v in item["type_info"]["verdicts"] if v["source"] == "listing")
    assert listing["detail"] == "« individuelle »" and listing["disagrees"]
    fiche = client.get(f"/api/listings/{lid}").json()
    assert fiche["adjacency"]["geometry"]["type"] == "FeatureCollection"
    assert fiche["type_info"]["summary"] == "Mitoyenne · carte (2 murs : 9 m, 8 m)"
    pend = client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()
    p = next(x for x in pend if x["id"] == lid)
    assert p["type_info"]["type"] == "terraced" and "geometry" not in p["adjacency"]


def test_user_type_correction_rescores_and_resists_the_agent(client, dburl):
    with db.connect(dburl) as conn:
        lid = conn.execute("SELECT id FROM listing WHERE external_id='a'").fetchone()["id"]
        conn.execute("UPDATE listing SET property_type='semi', property_type_src='structured' WHERE id=%s", (lid,))
    first = client.post(f"/api/listings/{lid}/property_type", json={"type": "semi"}).json()
    r = client.post(f"/api/listings/{lid}/property_type", json={"type": "link_detached"})
    assert r.status_code == 200
    body = r.json()
    assert body["type_info"]["type"] == "link_detached" and body["type_info"]["source"] == "user"
    assert body["score"] == first["score"] + 3 and body["score_details"]["type"] == 10
    ev = {"left": "house", "right": "house", "seen_on": ["street_view"], "photos": [], "notes": ""}
    client.post(f"/api/listings/{lid}/enrichment", headers={"X-Token": "tok"},
                json={"property_type": "terraced", "type_evidence": ev, "property_type_user": "detached"})
    fiche = client.get(f"/api/listings/{lid}").json()
    assert fiche["type_info"]["type"] == "link_detached" and fiche["enrichment"]["property_type_user"] == "link_detached"
    agent = next(v for v in fiche["type_info"]["verdicts"] if v["source"] == "agent")
    assert agent["type"] == "terraced" and agent["disagrees"]            # gardé comme preuve
    auto = client.post(f"/api/listings/{lid}/property_type", json={"type": None}).json()
    assert auto["type_info"]["type"] == "terraced" and auto["type_info"]["source"] == "agent"
    assert "property_type_user" not in client.get(f"/api/listings/{lid}").json()["enrichment"]
    assert client.post(f"/api/listings/{lid}/property_type", json={"type": "maison mitoyenne"}).status_code == 400
    assert client.post("/api/listings/999999/property_type", json={"type": "semi"}).status_code == 404


# ---------- file des vérifications de type (spec photo-adjacency-review) ----------
EV = {"left": "house", "right": "garage", "seen_on": ["street_view"], "photos": [1], "notes": "garage accolé à droite"}


def _row(dburl, lid):
    with db.connect(dburl) as conn:
        return conn.execute("SELECT enrichment, enrichment_status, enriched_at FROM listing WHERE id=%s",
                            (lid,)).fetchone()


def _enriched(dburl):
    with db.connect(dburl) as conn:
        lid = conn.execute("SELECT id FROM listing WHERE external_id='a'").fetchone()["id"]
        conn.execute("""UPDATE listing SET enrichment_status='done', enriched_at=now() - interval '3 days',
                        enrichment='{"summary_fr": "résumé", "property_type": "terraced"}' WHERE id=%s""", (lid,))
    return lid


def test_type_request_is_queued_apart_from_the_standard_queue(client, dburl):
    lid = _enriched(dburl)
    r = client.post(f"/api/listings/{lid}/type_request", json={"note": "le garage relie-t-il au voisin ?"})
    assert r.status_code == 200 and r.json()["type_request"]["reason"] == "user"
    assert client.get("/api/enrichment/type_checks").status_code == 401
    queue = client.get("/api/enrichment/type_checks", headers={"X-Token": "tok"}).json()
    assert [x["id"] for x in queue] == [lid] and queue[0]["type_info"]["type"] == "terraced"
    assert lid not in {x["id"] for x in client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()}
    assert _row(dburl, lid)["enrichment_status"] == "done"
    fiche = client.get(f"/api/listings/{lid}").json()
    assert fiche["enrichment"]["type_request"]["note"] == "le garage relie-t-il au voisin ?"


def test_type_check_verdict_moves_the_request_to_history_without_touching_status(client, dburl):
    lid = _enriched(dburl)
    client.post(f"/api/listings/{lid}/type_request", json={})
    before = _row(dburl, lid)
    r = client.post(f"/api/listings/{lid}/enrichment", headers={"X-Token": "tok"},
                    json={"_type_check": True, "property_type": "semi", "type_evidence": EV, "summary_fr": "écrasé ?"})
    assert r.status_code == 200, r.text
    after = _row(dburl, lid)
    enr = after["enrichment"]
    assert "type_request" not in enr and enr["property_type"] == "semi" and enr["type_evidence"] == EV
    assert enr["summary_fr"] == "résumé"                              # seuls les champs du type sont fusionnés
    [h] = enr["type_checks"]
    assert (h["reason"], h["verdict"], h["notes"], h["sides"]) == ("user", "semi", "garage accolé à droite",
                                                                   ["house", "garage"]) and h["done_at"]
    assert (after["enrichment_status"], after["enriched_at"]) == (before["enrichment_status"], before["enriched_at"])
    assert client.get("/api/enrichment/type_checks", headers={"X-Token": "tok"}).json() == []


def test_standard_enrichment_with_evidence_settles_a_pending_request(client, dburl):
    lid = _enriched(dburl)
    client.post(f"/api/listings/{lid}/type_request", json={})
    r = client.post(f"/api/listings/{lid}/enrichment", headers={"X-Token": "tok"},
                    json={"summary_fr": "nouveau", "property_type": "semi", "type_evidence": EV})
    assert r.status_code == 200
    enr = _row(dburl, lid)["enrichment"]
    assert "type_request" not in enr and enr["type_checks"][0]["verdict"] == "semi"
    client.post(f"/api/listings/{lid}/type_request", json={})
    client.post(f"/api/listings/{lid}/enrichment", headers={"X-Token": "tok"}, json={"summary_fr": "sans preuves"})
    assert "type_request" in _row(dburl, lid)["enrichment"]             # pas de preuves : la demande reste


def test_type_check_queue_order(client, dburl):
    with db.connect(dburl) as conn:
        ids = [conn.execute("SELECT id FROM listing WHERE external_id=%s", (e,)).fetchone()["id"] for e in ("a", "c")]
        conn.execute("UPDATE listing SET excluded_reason=NULL, score=CASE WHEN external_id='a' THEN 60 ELSE 80 END "
                     "WHERE id = ANY(%s)", (ids,))
    for lid in ids:
        client.post(f"/api/listings/{lid}/type_request", json={})
    queue = client.get("/api/enrichment/type_checks", headers={"X-Token": "tok"}).json()
    assert [x["id"] for x in queue] == [ids[1], ids[0]]                   # meilleur score d'abord


def test_contact_sheet_numbers_up_to_12_local_photos(client, dburl, monkeypatch, tmp_path):
    import io
    from PIL import Image
    from psycopg.types.json import Jsonb
    from tests.dedupe_helpers import jpeg, scene
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with db.connect(dburl) as conn:
        lid = conn.execute("SELECT id FROM listing WHERE external_id='a'").fetchone()["id"]
        rels = []
        for i in range(10):
            f = tmp_path / "photos" / str(lid) / f"{i:02d}.jpg"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(jpeg(scene(i + 1)))
            rels.append(f"{lid}/{i:02d}.jpg")
        conn.execute("UPDATE listing SET photos=%s WHERE id=%s", (Jsonb(rels), lid))
        other = conn.execute("SELECT id FROM listing WHERE external_id='c'").fetchone()["id"]
    assert client.get(f"/api/listings/{lid}/contact_sheet.jpg").status_code == 401
    r = client.get(f"/api/listings/{lid}/contact_sheet.jpg", headers={"X-Token": "tok"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert img.size == (960, 540)                                     # 10 vignettes : 4 colonnes, 3 rangées
    filled = [img.crop((x * 240 + 60, y * 180 + 60, x * 240 + 180, y * 180 + 150)).convert("L").getextrema()[1] > 0
              for y in range(3) for x in range(4)]
    assert filled == [True] * 10 + [False] * 2
    r_, g_, b_ = img.getpixel((4, 4))                                  # étiquette du numéro (jaune)
    assert r_ > 200 and g_ > 180 and b_ < 80
    with db.connect(dburl) as conn:
        conn.execute("UPDATE listing SET photos=%s WHERE id=%s", (Jsonb(rels + [f"{lid}/x{i}.jpg" for i in range(5)]), lid))
    assert Image.open(io.BytesIO(client.get(f"/api/listings/{lid}/contact_sheet.jpg",
                                            headers={"X-Token": "tok"}).content)).size == (960, 540)   # 12 au plus
    nophoto = client.get(f"/api/listings/{other}/contact_sheet.jpg", headers={"X-Token": "tok"})
    assert nophoto.status_code == 404 and "aucune photo locale" in nophoto.json()["detail"]


# ---------- auteurs des commentaires (change add-listing-comments) ----------
BASE_CRIT = {"price": {}, "preferred_cities": [], "other_cities": [], "commute": {}, "alert": {}}


def test_criteria_exposes_comment_authors(client, monkeypatch):
    monkeypatch.setattr(settings, "load_criteria", lambda *a: {**BASE_CRIT, "comment_authors": ["Alexandre", "Marie"]})
    assert client.get("/api/criteria").json()["comment_authors"] == ["Alexandre", "Marie"]


def test_criteria_without_comment_authors(client, monkeypatch):
    monkeypatch.setattr(settings, "load_criteria", lambda *a: dict(BASE_CRIT))
    assert client.get("/api/criteria").json()["comment_authors"] == []


# ---------- commentaires dans les files de l'agent (change add-listing-comments) ----------
def _comment(dburl, lid, body, author="Marie"):
    with db.connect(dburl) as conn:
        conn.execute("INSERT INTO listing_comment (listing_id, author, body) VALUES (%s, %s, %s)", (lid, author, body))


def test_pending_exposes_user_comments(client, dburl):
    pend = client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json()
    assert pend and all(x["user_comments"] == [] for x in pend)
    a = next(x["id"] for x in pend if x["url"] == "https://e/a")
    _comment(dburl, a, "La maison est au bout de l'impasse")
    entry = next(x for x in client.get("/api/enrichment/pending", headers={"X-Token": "tok"}).json() if x["id"] == a)
    assert [(c["author"], c["body"]) for c in entry["user_comments"]] == [("Marie", "La maison est au bout de l'impasse")]
    assert entry["user_comments"][0]["created_at"] and "id" not in entry["user_comments"][0]


def test_pending_comments_of_group_and_no_member(client, dburl):
    p, m = _merged_pair(dburl)
    _comment(dburl, m, "écrit sur la carte avant fusion")
    _comment(dburl, p, "écrit sur la principale", author="Alexandre")
    pend = {x["id"]: x for x in client.get("/api/enrichment/pending?limit=50", headers={"X-Token": "tok"}).json()}
    assert m not in pend and {c["body"] for c in pend[p]["user_comments"]} == {"écrit sur la carte avant fusion",
                                                                                 "écrit sur la principale"}


def test_type_checks_expose_user_comments(client, dburl):
    a = next(x["id"] for x in client.get("/api/listings").json() if x["url"] == "https://e/a")
    client.post(f"/api/listings/{a}/type_request", json={"note": "voir le garage"})
    _comment(dburl, a, "Garage accolé au voisin")
    q = client.get("/api/enrichment/type_checks", headers={"X-Token": "tok"}).json()
    assert [c["body"] for c in next(x for x in q if x["id"] == a)["user_comments"]] == ["Garage accolé au voisin"]
