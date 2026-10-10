import os
import pytest
import yaml
from app.sources import agences

FIX = "tests/fixtures/"
pytestmark = pytest.mark.skipif(
    not (os.path.exists(FIX + "refimmo_list.html") and os.path.exists("config/criteria.yaml")),
    reason="fixtures/critères privés absents (git-ignorés)")
CRIT = yaml.safe_load(open("config/criteria.yaml", encoding="utf-8")) if os.path.exists("config/criteria.yaml") else {}


def read(name):
    return open(FIX + name, encoding="utf-8").read()


def test_refimmo_list():
    cards = agences.refimmo_parse_list(read("refimmo_list.html"))
    assert len(cards) == 6
    assert cards[0] == {"id": "31", "ref": "3777-REFERENCE", "kind": "", "city": "Saint-André-lez-Lille", "price": 11902}
    assert any(c["kind"] == "Maison" and c["city"] == "Wambrechies" for c in cards)


def test_refimmo_detail_house():
    d = agences.refimmo_parse_detail(read("refimmo_detail.html"))      # id=24 : Wambrechies 364 000 €
    assert d["surface_m2"] == 118 and d["bedrooms"] == 3
    assert d["dpe"] == "C" and d["ges"] == "B"
    assert d["has_bathtub"] is True and d["has_garden"] is True
    assert len(d["photo_urls"]) >= 10 and all("Negociateurs" not in u for u in d["photo_urls"])
    assert d["description"].startswith("NOUVEAUTE")


def test_croix_list_and_detail():
    cards = agences.croix_parse_list(read("croix_list.html"))
    house = next(c for c in cards if c["ref"] == "1212")
    assert house["kind"].startswith("v:m") and house["price"] == 560000 and house["city"] == "Croix"
    assert house["cp"] == "59170" and house["bedrooms"] == 5 and house["published"] == "2026-08-19"
    assert sum(c["kind"].startswith("v:m") for c in cards) == 1          # le reste = locations
    d = agences.croix_parse_detail(read("croix_detail.html"))
    assert d["land_m2"] == 325 and d["year_built"] == 1930 and d["has_garage"] and d["has_garden"]
    assert len(d["photo_urls"]) == 9 and d["photo_urls"][0].startswith(agences.CROIX_BASE)


def test_dreossi_list_and_detail():
    cards = agences.dreossi_parse_list(read("dreossi_list.html"))
    assert len(cards) == 9 and all(c["slug"] for c in cards)
    active = [c for c in cards if not agences.SOLD.search(c["text"]) and "MAISON" in c["text"].upper()]
    assert active == []                                                # tout est vendu au moment de la capture
    d = agences.dreossi_parse_detail(read("dreossi_detail.html"))
    assert d["surface_m2"] == 103 and d["dpe"] == "E" and d["ges"] == "E" and d["price"] is None
    assert d["property_type"] == "terraced" and len(d["photo_urls"]) >= 5


class Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Client:
    """Simule les 3 sites à partir des fixtures ; la page 2 de Référence renvoie la page 1 (fin de liste)."""
    def __init__(self, down=()):
        self.down, self.calls = set(down), []

    def get(self, url, params=None, **_):
        self.calls.append(url)
        if any(d in url for d in self.down):
            return Resp(status=503)
        if url.endswith("/ventes"):
            return Resp(read("refimmo_list.html"))
        if "detail-vente" in url:
            return Resp(read("refimmo_detail.html"))
        if url.endswith("/properties"):
            return Resp(read("dreossi_list.html"))
        return Resp("")

    def post(self, url, data=None, **_):
        self.calls.append(url)
        if any(d in url for d in self.down):
            return Resp(status=503)
        return Resp(read("croix_list.html") if "listbien" in url else read("croix_detail.html"))


def test_fetch_combined(monkeypatch):
    monkeypatch.setattr(agences.time, "sleep", lambda s: None)
    crit = {**CRIT, "price": {**CRIT["price"], "max": 600000}}          # pour garder la maison de Croix à 560 k€
    items = agences.fetch(Client(), crit)
    ids = {i["external_id"] for i in items}
    assert "croix:1212" in ids and any(i.startswith("refimmo:") for i in ids)
    assert all(i["source"] == "agences" and i["url"] for i in items)
    croix = next(i for i in items if i["external_id"] == "croix:1212")
    assert croix["city"] == "Croix" and croix["price"] == 560000 and croix["raw"] == {"price": 560000, "ref": "1212"}
    # 2e passage, rien n'a changé : aucune fiche re-téléchargée
    known = {i["external_id"]: {**i["raw"], "url": i["url"]} for i in items}
    c2 = Client()
    agences.fetch(c2, crit, known=known)
    assert not any("detail-vente" in u or "getbien" in u for u in c2.calls)


def test_fetch_site_down_keeps_known(monkeypatch):
    monkeypatch.setattr(agences.time, "sleep", lambda s: None)
    known = {"croix:1": {"price": 1, "url": "http://x/1"}}
    items = agences.fetch(Client(down=["immobiliere-de-croix"]), CRIT, known=known)
    assert {"source": "agences", "external_id": "croix:1", "url": "http://x/1"} in items
