import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from app.sources import pap

FIX = "tests/fixtures/"
pytestmark = pytest.mark.skipif(not os.path.exists(FIX + "pap_list.html"), reason="fixtures privées absentes")


def read(name):
    return open(FIX + name, encoding="utf-8").read()


def test_parse_list_keeps_only_pap_ads():
    cards = pap.parse_list(read("pap_list.html"))
    assert len(cards) == 13                                     # 13 annonces PAP (les partenaires sont écartés)
    c = next(c for c in cards if c["id"] == "465502456")
    assert c["url"] == "https://www.pap.fr/annonces/maison-lille-59000-r465502456"
    assert c["cp"] == "59000" and c["surface_m2"] == 91 and c["rooms"] == 4 and c["bedrooms"] == 3
    assert c["dpe"] == "E" and c["description"].startswith("Maison de ville")
    assert all(x["url"].startswith("https://www.pap.fr/annonces/") for x in cards)


def test_next_page():
    assert pap.next_page(read("pap_list.html")) == "https://www.pap.fr/proximite/ventes-maisons-lille-59-g43627-2"
    assert pap.next_page("<html></html>") is None


def test_parse_detail():
    d = pap.parse_detail(read("pap_detail.html"))
    assert d["price"] == 275000 and d["surface_m2"] == 91 and d["rooms"] == 4 and d["bedrooms"] == 3
    assert d["city"] == "Lille" and d["postal_code"] == "59000"
    assert d["address"] == "Rue Victor Renard, 59000 Lille"
    assert d["dpe"] == "E" and d["land_m2"] == 120
    assert d["has_garage"] and d["has_garden"] and len(d["photo_urls"]) >= 8
    assert d["description"].startswith("Maison de ville sur 2 étages")
    assert d["property_type"] in ("terraced", "semi", "detached", "unknown")


class Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def json(self):
        return [{"id": 30163, "name": "Wambrechies (59118)"}]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """Sert la fixture liste pour toute page ville ; la fiche détail pour /annonces/."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def get(self, url, **kw):
        self.calls.append(url)
        if self.fail and "/annonce/" in url:
            return Resp(status=503)
        if "/annonces/" in url:
            return Resp(read("pap_detail.html"))
        return Resp(read("pap_list.html"))


def test_fetch_city_new_then_known(monkeypatch):
    monkeypatch.setattr(pap.time, "sleep", lambda s: None)
    monkeypatch.setattr(pap, "_gid_cache", {"Wambrechies": 30163})
    session = FakeSession()
    crit = {"zones": {"Wambrechies": {"postal_codes": ["59118"], "insee": "59636"}}}
    items = pap.fetch_city(session, "Wambrechies", crit["zones"]["Wambrechies"], {}, set())
    ids = {i["external_id"] for i in items}
    assert "pap:465502456" in ids and len(ids) == 13
    it = next(i for i in items if i["external_id"] == "pap:465502456")
    assert it["price"] == 275000 and it["raw"]["city"] == "Wambrechies" and it["raw"]["fetched_at"]
    # 2e passage : annonces connues et fraîches -> simples « vues », aucune fiche re-téléchargée
    known = {i["external_id"]: i["raw"] for i in items}
    session2 = FakeSession()
    items2 = pap.fetch_city(session2, "Wambrechies", crit["zones"]["Wambrechies"], known, set())
    assert all(i.get("price") is None for i in items2)          # stubs
    assert not any("/annonces/" in u for u in session2.calls)
    # 3e passage : fiche périmée -> re-téléchargée
    old = datetime.now(timezone.utc) - timedelta(days=4)
    stale = {k: {**v, "fetched_at": old.isoformat()} for k, v in known.items()}
    session3 = FakeSession()
    pap.fetch_city(session3, "Wambrechies", crit["zones"]["Wambrechies"], stale, set())
    assert sum("/annonces/" in u for u in session3.calls) == 13


def test_fetch_keeps_known_when_city_fails(monkeypatch):
    monkeypatch.setattr(pap.time, "sleep", lambda s: None)
    monkeypatch.setattr(pap, "_gid_cache", {"Wambrechies": 30163})
    monkeypatch.setattr(pap, "_session", lambda: FakeSession(fail=True))
    crit = {"zones": {"Wambrechies": {"postal_codes": ["59118"]}}}
    known = {"pap:1": {"city": "Wambrechies", "url": "https://www.pap.fr/annonces/x-r1"}}
    items = pap.fetch(None, crit, known)
    assert items == [dict(source="pap", external_id="pap:1", url="https://www.pap.fr/annonces/x-r1")]


def test_parse_list_filters_out_of_zone():
    cards = pap.parse_list(read("pap_list.html"), zone_slugs={"lille"}, zone_cps={"59000"})
    assert cards and all(c["cp"] == "59000" for c in cards)
    assert len(cards) < 13                                      # Lambersart/Marcq/La Madeleine écartées
    par_slug = pap.parse_list(read("pap_list.html"), zone_slugs={"lambersart"}, zone_cps={"99999"})
    assert par_slug and all("lambersart" in c["url"] for c in par_slug)   # repli par slug de ville
