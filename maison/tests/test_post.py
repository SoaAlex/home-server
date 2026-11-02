import json
import httpx
from app import geo, photos, alerts

CRIT = {"alert": {"min_score": 75, "max_price": 450000, "require_confirmed_garage_garden": True}}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_photos_download(tmp_path):
    c = client_for(lambda req: httpx.Response(200, content=b"abc"))
    assert photos.download(c, 1, ["http://x/a.jpg", "http://x/b.jpg"], tmp_path) == ["1/00.jpg", "1/01.jpg"]
    assert (tmp_path / "1" / "00.jpg").read_bytes() == b"abc"


def test_osrm_minutes():
    c = client_for(lambda req: httpx.Response(200, json={"routes": [{"duration": 600}]}))
    assert geo.osrm_minutes(c, 3.0, 50.0, 3.1, 50.1) == 10


def test_ademe_unique_and_ambiguous():
    one = {"results": [{"adresse_ban": "7 Allée X 59118 Wambrechies", "_geopoint": "50.68,3.06"}]}
    c = client_for(lambda req: httpx.Response(200, json=one))
    a = geo.ademe_address(c, "59636", "B", 82, "2026-07-07", 56)
    assert a["address"].startswith("7 Allée") and a["lat"] == 50.68
    two = {"results": one["results"] + [{"adresse_ban": "9 rue Y", "_geopoint": "50.6,3.0"}]}
    c = client_for(lambda req: httpx.Response(200, json=two))
    assert geo.ademe_address(c, "59636", "B", 82, "2026-07-07", None) is None
    assert geo.ademe_address(c, "59636", "B", 82, None, None) is None


def row(**kw):
    base = dict(id=7, url="https://e/7", score=80, price=420000, has_garage=True, has_garden=True,
                title="Maison", city="Wambrechies", enrichment={}, photo_urls=["https://p/1.jpg"],
                commute={"alexandre": 20, "marie": 10, "total_min": 30})
    return {**base, **kw}


def test_should_alert():
    assert alerts.should_alert(row(), CRIT)
    assert not alerts.should_alert(row(has_garage=None), CRIT)
    assert not alerts.should_alert(row(score=60), CRIT)
    assert not alerts.should_alert(row(price=480000), CRIT)
    assert not alerts.should_alert(row(alerted_at="2026-01-01"), CRIT)
    assert not alerts.should_alert(row(reaction="dislike"), CRIT)


def test_send_discord_payload():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(204)
    alerts.send_discord(client_for(h), "https://discord/webhook", row())
    e = seen["body"]["embeds"][0]
    assert e["title"].startswith("🏡 80/100") and e["image"]["url"] == "https://p/1.jpg"
    assert any(f["value"] == "420 000 €" for f in e["fields"])


CLUSTER = {"price": {"value": 235000, "offer_id": 2}, "offers": [
    {"id": 2, "source": "bienici", "url": "https://www.bienici.com/annonce/b", "price": 235000, "status": "active"},
    {"id": 1, "source": "seloger", "url": "https://www.seloger.com/annonce/s", "price": 241500, "status": "active"},
    {"id": 3, "source": "seloger", "url": "https://www.seloger.com/annonce/old", "price": 230000, "status": "stale"}]}


def test_embed_of_multi_source_listing():
    row = {"id": 1, "url": "https://www.seloger.com/annonce/s", "score": 80, "title": "Maison", "price": 241500,
           "city": "Croix", "cluster": CLUSTER}
    e = alerts.build_embed(row, {"commute": {"destinations": {}}})
    f = {x["name"]: x["value"] for x in e["fields"]}
    assert f["Prix"] == "235 000 €" and e["url"] == "https://www.bienici.com/annonce/b"
    assert f["Sources (3)"].startswith("Bien'ici 235 000 €") and "(non confirmée)" in f["Sources (3)"]
    assert e["description"].endswith("/#/l/1)")


def test_members_never_alert():
    crit = {"alert": {"min_score": 75, "max_price": 450000, "require_confirmed_garage_garden": False}}
    row = {"id": 5, "score": 90, "price": 300000, "status": "active", "merged_into": 4}
    assert not alerts.should_alert(row, crit)
    assert alerts.should_alert({**row, "merged_into": None}, crit)


def test_embed_shows_retained_type_and_its_source():
    adj = {"status": "ok", "type": "link_detached", "usable": True, "warnings": [], "input": {"confidence": "exact"},
           "neighbours": [{"kind": "annex_link", "length_m": 6.0, "side": "right"}]}
    e = alerts.build_embed(row(property_type="semi", property_type_src="text_weak", adjacency=adj),
                           {"commute": {"destinations": {}}})
    f = {x["name"]: x["value"] for x in e["fields"]}
    assert f["Type"] == "Accolée par le garage (carte)"
    assert {x["name"]: x["value"] for x in alerts.build_embed(row(), {})["fields"]}["Type"] == "Inconnu"


# ---------- contrôle des champs du type envoyés par l'agent (spec photo-adjacency-review) ----------
import pytest  # noqa: E402
from app.scoring import check_type_fields, type_from_sides  # noqa: E402

EV = {"left": "house", "right": "free", "seen_on": ["listing_photos"], "photos": [0, 2], "notes": "pignon droit vitré"}


@pytest.fixture
def api(dburl, monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, settings, web
    from app.ingest import upsert
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "ENRICH_TOKEN", "tok")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(web, "postprocess", lambda *a, **k: None)
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, dict(source="bienici", external_id="t", url="https://e/t", price=300000,
                                   city="Wambrechies", has_garage=True, has_garden=True))
        conn.execute("""UPDATE listing SET enrichment='{"summary_fr": "avant"}' WHERE id=%s""", (lid,))
    with TestClient(web.app) as c:
        c.lid, c.dburl = lid, dburl
        yield c


def _enrichment(api):
    from app import db
    with db.connect(api.dburl) as conn:
        return conn.execute("SELECT enrichment, enrichment_status FROM listing WHERE id=%s", (api.lid,)).fetchone()


def test_prose_property_type_is_refused_and_nothing_is_written(api):
    before = _enrichment(api)
    r = api.post(f"/api/listings/{api.lid}/enrichment", headers={"X-Token": "tok"},
                 json={"property_type": "Maison mitoyenne", "summary_fr": "après"})
    assert r.status_code == 422 and "detached | link_detached | semi | terraced" in r.json()["detail"]
    assert _enrichment(api) == before


def test_valid_output_is_recorded_and_retained(api):
    r = api.post(f"/api/listings/{api.lid}/enrichment", headers={"X-Token": "tok"},
                 json={"property_type": "semi", "type_evidence": EV})
    assert r.status_code == 200, r.text
    enr = _enrichment(api)["enrichment"]
    assert enr["property_type"] == "semi" and enr["type_evidence"] == EV
    t = api.get(f"/api/listings/{api.lid}").json()["type_info"]
    assert (t["type"], t["source"], t["confidence"]) == ("semi", "agent", "high")


def test_terraced_with_a_free_side_is_refused_citing_semi(api):
    r = api.post(f"/api/listings/{api.lid}/enrichment", headers={"X-Token": "tok"},
                 json={"property_type": "terraced", "type_evidence": EV})
    assert r.status_code == 422 and "'semi'" in r.json()["detail"]


def test_an_unknown_side_is_accepted(api):
    r = api.post(f"/api/listings/{api.lid}/enrichment", headers={"X-Token": "tok"},
                 json={"property_type": "semi", "type_evidence": {**EV, "right": "unknown"}})
    assert r.status_code == 200


def test_evidence_schema():
    assert check_type_fields({}) is None and check_type_fields({"property_type": None}) is None
    assert "unknown" in check_type_fields({"property_type": "unknown"})       # ne pas envoyer de type
    assert "clés inconnues" in check_type_fields({"type_evidence": {**EV, "verdict": "x"}})
    assert "left" in check_type_fields({"type_evidence": {**EV, "left": "mur"}})
    assert "seen_on" in check_type_fields({"type_evidence": {**EV, "seen_on": ["google"]}})
    assert "photos" in check_type_fields({"type_evidence": {**EV, "photos": ["3"]}})
    assert "300" in check_type_fields({"type_evidence": {**EV, "notes": "x" * 301}})
    assert [type_from_sides(*s) for s in (("house", "house"), ("house", "garage"), ("garage", "free"),
                                          ("annex", "garage"), ("free", "free"), ("house", "unknown"))] == \
        ["terraced", "semi", "link_detached", "link_detached", "detached", None]
