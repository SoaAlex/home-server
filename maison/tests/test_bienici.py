from app import db
from app.ingest import upsert
from app.sources import bienici


def ad(**kw):
    """Annonce Bien'ici minimale (forme réelle de realEstateAds.json, valeurs fictives)."""
    base = dict(id="test-1", title="Maison", description="Maison avec garage et jardin", price=300000,
                surfaceArea=100, roomsQuantity=4, bedroomsQuantity=3, city="Hem", postalCode="59510",
                blurInfo={"position": {"lat": 50.65, "lon": 3.19}, "type": "point"},
                photos=[{"url_photo": "https://photos.example/1.jpg"}],
                publicationDate="2026-10-26T10:00:00.000Z")
    return {**base, **kw}


def parse_one(**kw):
    return bienici.parse({"realEstateAds": [ad(**kw)]})[0]


def test_scalar_values_are_unchanged():
    it = parse_one(surfaceArea=104.5, landSurfaceArea=312, roomsQuantity=5, bedroomsQuantity=3)
    assert (it["surface_m2"], it["land_m2"], it["rooms"], it["bedrooms"]) == (104.5, 312, 5, 3)


def test_new_build_ranges_become_their_lowest_value():
    # Programmes neufs : Bien'ici publie des fourchettes ; un tableau dans une colonne numérique
    # faisait échouer l'INSERT et donc toute la source (cycles Bien'ici en échec dès le 26/10/2026).
    it = parse_one(surfaceArea=[84.32, 87.51], landSurfaceArea=[200, 350], roomsQuantity=[3, 4],
                   bedroomsQuantity=[2, 3], price=[397000, 399000], newProperty=True)
    assert (it["surface_m2"], it["land_m2"], it["rooms"], it["bedrooms"]) == (84.32, 200, 3, 2)


def test_empty_or_non_numeric_lists_give_none():
    it = parse_one(surfaceArea=[], landSurfaceArea=[None, "n/a"])
    assert it["surface_m2"] is None and it["land_m2"] is None


def test_new_build_range_is_ingested(dburl):
    item = parse_one(surfaceArea=[84.32, 87.51], price=[397000, 399000])
    with db.connect(dburl) as conn:
        lid, inserted = upsert(conn, item)
        row = conn.execute("SELECT surface_m2 FROM listing WHERE id=%s", (lid,)).fetchone()
    assert inserted and float(row["surface_m2"]) == 84.32


def test_new_property_flag_is_kept_in_raw():
    # Sert à la déduplication : deux lots d'un programme neuf partagent les mêmes rendus.
    assert parse_one(newProperty=True)["raw"]["newProperty"] is True
    assert parse_one(newProperty=False)["raw"]["newProperty"] is False
    assert parse_one()["raw"]["newProperty"] is None


def test_fixture_listings_keep_new_property():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "fixtures" / "bienici.json").read_text())
    assert all(it["raw"]["newProperty"] is False for it in bienici.parse(data))
