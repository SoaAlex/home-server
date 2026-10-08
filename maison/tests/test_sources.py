import json
import os
import pytest
import yaml
from app.sources import bienici, notaires, maes

# Fixtures réelles et critères personnels : présents seulement sur le NAS (ignorés par git)
pytestmark = pytest.mark.skipif(
    not (os.path.exists("tests/fixtures/bienici.json") and os.path.exists("config/criteria.yaml")),
    reason="fixtures/critères privés absents (git-ignorés)")

CRIT = yaml.safe_load(open("config/criteria.yaml", encoding="utf-8")) if os.path.exists("config/criteria.yaml") else {}


def test_bienici_parse_fixture():
    items = bienici.parse(json.load(open("tests/fixtures/bienici.json")))
    assert len(items) == 20
    it = items[0]
    assert it["source"] == "bienici" and it["external_id"]
    assert it["url"].startswith("https://www.bienici.com/annonce/")
    assert isinstance(it["price"], int) and it["city"]
    assert it["lat"] and it["geo_precision"] in ("approx", "exact")
    assert all(u.startswith("http") for u in it["photo_urls"])


def test_notaires_filters_to_our_zones():
    payload = json.load(open("tests/fixtures/notaires.json"))
    raw = payload["annonceResumeDto"]
    insee = {str(v["insee"]) for v in CRIT["zones"].values() if v.get("insee")}
    cps = {cp for v in CRIT["zones"].values() for cp in v["postal_codes"]}
    expected = [a for a in raw if a["inseeCommune"] in insee or a["codePostal"] in cps]
    items = notaires.parse(payload, CRIT)
    assert len(items) == len(expected)
    for it in items:
        assert it["source"] == "notaires" and it["url"].startswith("https://")


def test_notaires_keeps_zone_listing():
    payload = {"annonceResumeDto": [dict(annonceId=1, inseeCommune="59636", codePostal="59118",
                                         communeNom="Wambrechies", prixTotal=400000,
                                         urlDetailAnnonceFr="https://x/1", descriptionFr="garage et jardin")]}
    items = notaires.parse(payload, CRIT)
    assert items[0]["city"] == "Wambrechies" and items[0]["has_garage"] is True


def test_maes_detail_reference_listing():
    d = maes.parse_detail(open("tests/fixtures/maes_detail.html").read())
    assert d["price"] == 384000
    assert d["city"] == "Wambrechies"
    assert d["surface_m2"] == 82 and d["bedrooms"] == 3 and d["rooms"] == 4
    assert d["has_garage"] is True and d["has_garden"] is True
    assert d["dpe_value"] == 56 and d["dpe"] == "B" and d["dpe_date"] == "2026-07-07"
    assert d["year_built"] == 2019 and d["works"] == "none"
    assert d["sale_status"] == "Sous Offre"
    assert len(d["photo_urls"]) >= 10 and len(set(d["photo_urls"])) == len(d["photo_urls"])
