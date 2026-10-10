from app import collector, db, ingest


def test_postprocess_geocodes_city_when_insee_known_but_no_position(dburl, monkeypatch):
    """Régression : une annonce avec INSEE mais sans lat/lon (agences, notaires) restait sans carte ni trajets."""
    crit = {"price": {"max": 550000}, "require": {"garage": True, "garden": True},
            "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": [],
            "zones": {"Wambrechies": {"insee": "59636", "postal_codes": ["59118"], "zone_ids": []}},
            "commute": {"destinations": {"a": {"lat": 50.6, "lon": 3.1}}}, "alert": {"min_score": 75}}
    monkeypatch.setattr(collector.geo, "geocode",
                        lambda c, q, **k: {"lat": 50.68, "lon": 3.05, "citycode": "59636", "label": q, "type": "municipality"})
    monkeypatch.setattr(collector.geo, "commute", lambda c, lat, lon, d: {"a": 12, "total_min": 12})
    monkeypatch.setattr(collector.settings, "DATABASE_URL", dburl)
    with db.connect(dburl) as conn:
        lid, _ = ingest.upsert(conn, dict(source="agences", external_id="t:1", url="https://x/1", title="Maison",
                                          price=300000, city="Wambrechies", insee="59636", postal_code="59118",
                                          has_garage=True, has_garden=True))
        collector.postprocess(conn, None, crit, lid)
        row = conn.execute("SELECT lat, geo_precision, commute FROM listing WHERE id=%s", (lid,)).fetchone()
    assert row["lat"] == 50.68 and row["geo_precision"] == "city" and row["commute"]["total_min"] == 12
