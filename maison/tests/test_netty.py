import json
import os
import pytest
import yaml
from app.sources import netty

pytestmark = pytest.mark.skipif(
    not (os.path.exists("tests/fixtures/netty_products.json") and os.path.exists("config/criteria.yaml")),
    reason="fixtures/critères privés absents (git-ignorés)")

CRIT = yaml.safe_load(open("config/criteria.yaml", encoding="utf-8")) if os.path.exists("config/criteria.yaml") else {}
SITE = {"name": "Les Nouvelles Pépites", "url": "https://www.lesnouvellespepitesimmo.fr", "username": "lesnouvelles"}


def products():
    return json.load(open("tests/fixtures/netty_products.json"))["data"]["prodId"]


def test_config_lists_agencies():
    names = [a["name"] for a in CRIT["netty_agencies"]]
    assert "Les Nouvelles Pépites" in names and all(a["url"].startswith("https://") for a in CRIT["netty_agencies"])


def test_parse_product_marcq_house():
    it = netty.parse_product(products()["VM1226"], SITE)
    assert it["source"] == "netty" and it["external_id"] == "lesnouvelles:VM1226"
    assert it["url"] == "https://www.lesnouvellespepitesimmo.fr/vente/maison-6-pieces-marcq-en-baroeul-59700,VM1226"
    assert it["price"] == 449500 and it["city"] == "Marcq-en-Baroeul" and it["postal_code"] == "59700"
    assert it["has_garden"] is True and it["has_garage"] is True and it["bedrooms"] == 3
    assert it["geo_precision"] == "city" and 50 < it["lat"] < 51
    assert it["photo_urls"] and all(u.startswith("https://img.netty.immo/") for u in it["photo_urls"])
    assert "<br" not in it["description"] and it["published_at"].startswith("20")


def test_type_house_codes():
    a = netty.parse_product(products()["VM1170"], SITE)
    b = netty.parse_product(products()["VM1079"], SITE)
    assert (a["property_type"], a["property_type_src"]) == ("detached", "structured")    # 01 individuelle
    assert (b["property_type"], b["property_type_src"]) == ("terraced", "structured")    # 03 mitoyenne 2 côtés


def test_type_without_code_comes_from_text():
    p = {**products()["VM1170"], "type_house": None}
    it = netty.parse_product(p, SITE)
    assert it["property_type_src"] in ("text_explicit", "text_weak", None)
    assert it["property_type_src"] != "structured"


def test_keep_filters_zone_price_and_sold():
    cps, p = netty.zone_postcodes(CRIT), products()
    kept = {k for k, v in p.items() if netty.keep(v, cps, 550000)}
    assert "VM1226" in kept                 # Marcq 449 500 €
    assert "VM1210" not in kept             # Tressin : hors zone et 665 000 €
    assert "VM856" not in kept              # Mouvaux : ville exclue (hors zones)
    assert not netty.keep({**p["VM1226"], "transact": 3}, cps, 550000)   # vendu


def test_parse_detail_dpe():
    d = netty.parse_detail(open("tests/fixtures/netty_detail.html").read())
    assert d == {"dpe": "C", "ges": "C"}


class FakeResp:
    def __init__(self, data=None, text="", status=200):
        self._d, self.text, self.status_code = data, text, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    def __init__(self, fail=False):
        self.fail, self.gets = fail, []

    def post(self, url, json=None, headers=None):
        if self.fail:
            return FakeResp(status=503)
        payload = globals()["json"].load(open("tests/fixtures/netty_products.json"))
        return FakeResp(payload)

    def get(self, url, **_):
        self.gets.append(url)
        return FakeResp(text=open("tests/fixtures/netty_detail.html").read())


def test_fetch_dedups_groups_and_skips_unchanged_details(monkeypatch):
    monkeypatch.setattr(netty.time, "sleep", lambda s: None)
    crit = {**CRIT, "netty_agencies": [{**SITE, "group": "g"}, {**SITE, "name": "Autre site", "group": "g"}]}
    c = FakeClient()
    items = netty.fetch(c, crit)
    ids = [i["external_id"] for i in items]
    assert len(ids) == len(set(ids)) and "g:VM1226" in ids
    assert len(c.gets) == len(ids)                                   # 1 détail par annonce, pas par site
    assert next(i for i in items if i["external_id"] == "g:VM1226")["dpe"] == "C"
    known = {i["external_id"]: i["raw"] for i in items}
    c2 = FakeClient()
    netty.fetch(c2, crit, known=known)
    assert c2.gets == []                                             # inchangées : pas de re-téléchargement


def test_fetch_failed_site_keeps_known_alive():
    crit = {**CRIT, "netty_agencies": [SITE]}
    items = netty.fetch(FakeClient(fail=True), crit, known={"lesnouvelles:VM1": {"url": "https://x/1"}})
    assert items == [{"source": "netty", "external_id": "lesnouvelles:VM1", "url": "https://x/1"}]
