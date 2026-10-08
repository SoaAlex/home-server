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


def test_send_discord_payload():
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(204)
    alerts.send_discord(client_for(h), "https://discord/webhook", row())
    e = seen["body"]["embeds"][0]
    assert e["title"].startswith("🏡 80/100") and e["image"]["url"] == "https://p/1.jpg"
    assert any(f["value"] == "420 000 €" for f in e["fields"])
