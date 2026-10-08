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


def test_user_status(client):
    lid = client.get("/api/listings").json()[0]["id"]
    assert client.post(f"/api/listings/{lid}/user", json={"user_status": "favorite"}).status_code == 200
    assert client.get(f"/api/listings/{lid}").json()["user_status"] == "favorite"
