"""Vue d'un bien multi-sources (spec merged-listings) : offres, prix retenu, historique, champs complétés,
photos sans doublon, statut ; et prise en compte par le score."""
from datetime import datetime, timedelta, timezone

from app import dedupe, scoring
from app.scoring import effective, merged_columns

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def row(i, source="bienici", price=300000, status="active", seen_days=0, first_days=10, **kw):
    r = dict(id=i, source=source, url=f"https://{source}/{i}", title=f"Maison {i}", price=price, status=status,
             agency=None, enrichment={}, enrichment_status="pending", price_history=[], photos=[], photo_urls=[],
             lat=None, lon=None, geo_precision=None, address=None, commute=None,
             first_seen=NOW - timedelta(days=first_days), last_seen=NOW - timedelta(days=seen_days))
    r.update(kw)
    return r


def cluster(p, *members, hashes=None):
    return dedupe.build_cluster(p, list(members), hashes or {}, NOW)


def test_two_active_prices_keep_lowest_and_list_cheapest_first():
    c = cluster(row(1, price=241500, source="seloger"), row(2, price=235000))
    assert c["price"] == {"value": 235000, "offer_id": 2}
    assert [o["price"] for o in c["offers"]] == [235000, 241500]
    assert {o["source"] for o in c["offers"]} == {"bienici", "seloger"} and c["offers"][1]["principal"]


def test_cheapest_offer_gone_switches_retained_price():
    c = cluster(row(1, price=241500), row(2, price=235000, status="gone"))
    assert c["price"]["value"] == 241500
    assert [o["status"] for o in c["offers"]] == ["active", "gone"] and c["active"]


def test_old_email_card_is_not_confirmed_and_does_not_set_price():
    c = cluster(row(1, price=245000), row(2, source="seloger", price=230000, seen_days=50))
    assert c["price"]["value"] == 245000
    assert next(o for o in c["offers"] if o["id"] == 2)["status"] == "stale"


def test_no_active_offer_falls_back_to_lowest_of_all():
    c = cluster(row(1, price=250000, status="gone"), row(2, price=240000, status="gone"))
    assert c["price"]["value"] == 240000 and not c["active"]


def test_agent_price_correction_counts():
    c = cluster(row(1, price=260000, enrichment={"price": 239000}), row(2, price=245000))
    assert c["price"] == {"value": 239000, "offer_id": 1}


def test_principal_gone_member_active_stays_visible():
    c = cluster(row(1, status="gone"), row(2, source="netty"))
    assert c["active"] and c["offers"][0]["id"] == 2


def test_empty_fields_completed_from_other_sources():
    p = row(1, land_m2=None, dpe=None, bedrooms=4)
    c = cluster(p, row(2, source="seloger", land_m2=367, dpe="D", bedrooms=5))
    assert c["fill"]["land_m2"] == 367 and c["fill"]["dpe"] == "D" and "bedrooms" not in c["fill"]
    full = {**p, "cluster": c}
    assert merged_columns(full)["land_m2"] == 367 and effective(full)["bedrooms"] == 4


def test_user_validated_address_is_kept():
    p = row(1, address="1 Rue de l'Exemple 59118 Wambrechies", lat=50.70, lon=3.05, geo_precision="exact",
            enrichment={"address_method": "user", "address_confidence": "exact"})
    c = cluster(p, row(2, address="2 Rue Fictive 59118 Wambrechies", lat=50.69, lon=3.04,
                       geo_precision="exact"))
    out = merged_columns({**p, "cluster": c})
    assert out["address"].startswith("1 Rue") and out["lat"] == 50.70


def test_precise_position_replaces_city_centroid():
    p = row(1, lat=50.68, lon=3.05, geo_precision="city", commute={"total_min": 40})
    c = cluster(p, row(2, source="netty", lat=50.69, lon=3.04, geo_precision="exact", address="4 rue X",
                       commute={"total_min": 25}))
    out = merged_columns({**p, "cluster": c})
    assert (out["lat"], out["geo_precision"], out["address"], out["commute"]["total_min"]) == (50.69, "exact", "4 rue X", 25)


def test_merged_history_shows_both_cards_and_drop():
    first = row(1, source="seloger", price=241500, first_days=10, seen_days=10)
    second = row(2, source="seloger", price=239500, first_days=3, seen_days=3)
    c = cluster(first, second)
    assert [(h["price"], h["source"]) for h in c["price_history"]] == [(241500, "seloger"), (239500, "seloger")]
    assert c["price_drop"]


def test_price_change_inside_one_offer_is_in_history():
    r = row(1, price=235000, first_days=20, price_history=[{"date": (NOW - timedelta(days=5)).isoformat(), "price": 249000}])
    c = cluster(r, row(2, source="seloger", price=249000, first_days=15))
    assert [h["price"] for h in c["price_history"]] == [249000, 235000] and c["price_drop"]   # 249 000 non répété


def test_same_price_on_every_source_gives_single_history_point():
    c = cluster(row(1, price=497000, first_days=20), row(2, price=497000, first_days=10), row(3, source="seloger", price=497000))
    assert [(h["price"], h["source"]) for h in c["price_history"]] == [(497000, "bienici")] and not c["price_drop"]


def test_concurrent_prices_without_later_drop_are_not_a_drop():
    c = cluster(row(1, price=235000, first_days=20), row(2, source="seloger", price=241500, first_days=5))
    assert not c["price_drop"]


def test_photos_merged_without_visual_duplicates():
    h1, h2, h3 = (0x0F0F, 0xF0F0), (0xFFFF0000, 0xAAAA), (0x1234567890ABCDEF, 0x55AA)
    p = row(1, photos=["1/00.jpg", "1/01.jpg"])
    m = row(2, source="seloger", photos=["2/00.jpg", "2/01.jpg"])
    hashes = {1: {"1/00.jpg": h1, "1/01.jpg": h2}, 2: {"2/00.jpg": (h1[0] ^ 0b11, h1[1]), "2/01.jpg": h3}}
    c = cluster(p, m, hashes=hashes)
    assert c["photos"] == ["/photos/1/00.jpg", "/photos/1/01.jpg", "/photos/2/01.jpg"]


def test_principal_without_local_photos_keeps_remote_urls():
    c = cluster(row(1, photo_urls=["https://p/1.jpg"]), row(2))
    assert c["photos"] == ["https://p/1.jpg"]


def test_single_listing_has_no_cluster():
    assert dedupe.build_cluster(row(1), [], {}, NOW) is None


# ---------- 4.4 score sur les valeurs fusionnées ----------
CRIT = {"price": {"max": 550000, "target": 450000}, "require": {"garage": True, "garden": True},
        "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": []}


def scored(r):
    return scoring.score_listing(r, CRIT)


def test_score_uses_lowest_active_price_and_filled_land():
    p = row(1, price=455000, city="Wambrechies", land_m2=None, has_garage=True, has_garden=True)
    alone = scored(p)
    c = cluster(p, row(2, source="seloger", price=399000, land_m2=420, city="Wambrechies"))
    merged = scored({**p, "cluster": c})
    assert alone[1]["prix"] == 5 and merged[1]["prix"] == 15
    assert alone[1]["jardin"] == 1 and merged[1]["jardin"] == 12


def test_cluster_price_beats_principal_agent_price_only_when_lower_offer_exists():
    p = row(1, price=500000, enrichment={"price": 480000}, city="Wambrechies")
    c = cluster(p, row(2, source="seloger", price=490000))
    assert effective({**p, "cluster": c})["price"] == 480000
