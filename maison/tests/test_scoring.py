from app.scoring import score_listing, fingerprint

CRIT = {
    "price": {"target": 450000, "max": 550000},
    "preferred_cities": ["Wambrechies", "Lambersart", "Hem"],
    "other_cities": ["Verlinghem"],
    "excluded_cities": ["Roubaix"],
    "require": {"garage": True, "garden": True},
}

BASE = dict(city="Wambrechies", postal_code="59118", price=420000, has_garage=True,
            has_garden=True, land_m2=450, property_type="detached", dpe="B", bedrooms=4,
            works="none", has_bathtub=True, enrichment={}, commute={"total_min": 28})


def test_excluded_city():
    assert score_listing({**BASE, "city": "Roubaix"}, CRIT)[2] == "ville exclue"


def test_out_of_zone():
    assert score_listing({**BASE, "city": "Dunkerque"}, CRIT)[2] == "hors zone"


def test_missing_city_is_pending_not_excluded():
    assert score_listing({**BASE, "city": None}, CRIT)[2] == "à compléter"


def test_no_garage_excluded_but_unknown_kept():
    assert score_listing({**BASE, "has_garage": False}, CRIT)[2] == "pas de garage"
    assert score_listing({**BASE, "has_garage": None}, CRIT)[2] is None


def test_price_cap():
    assert score_listing({**BASE, "price": 560000}, CRIT)[2] == "prix > max"


def test_enrichment_overrides_raw_fields():
    _, _, why = score_listing({**BASE, "has_garage": None, "enrichment": {"has_garage": False}}, CRIT)
    assert why == "pas de garage"


def test_ideal_listing_scores_high_and_ranks_city():
    s1, d, _ = score_listing(BASE, CRIT)
    s2, _, _ = score_listing({**BASE, "city": "Hem"}, CRIT)
    assert s1 == 91 and s1 > s2
    assert set(d) >= {"ville", "prix", "type", "jardin", "dpe", "travaux", "trajet"}


def test_cubic_and_long_garden_penalised():
    s1, _, _ = score_listing(BASE, CRIT)
    s2, _, _ = score_listing({**BASE, "enrichment": {"style": "cubic", "garden_shape": "long"}}, CRIT)
    assert s2 == s1 - 7


def test_viager_excluded():
    assert score_listing({**BASE, "title": "Vente à terme occupée sur 180 mois"}, CRIT)[2] == "viager / vente à terme"
    assert score_listing({**BASE, "description": "Viager occupé, bouquet de 50 000 €"}, CRIT)[2] == "viager / vente à terme"


def test_sold_excluded():
    assert score_listing({**BASE, "raw": {"sale_status": "Vendu"}}, CRIT)[2] == "vendu"
    assert score_listing({**BASE, "raw": {"sale_status": "Sous Offre"}}, CRIT)[2] is None


def test_fingerprint_same_house_two_sources():
    a = dict(city="Wambrechies", postal_code="59118", price=419000, surface_m2=121, land_m2=402)
    b = dict(city="WAMBRECHIES", postal_code="59118", price=420000, surface_m2=120.6, land_m2=400)
    assert fingerprint(a) == fingerprint(b)


def test_user_added_listing_kept_above_budget():
    over = {**BASE, "price": 577000}
    assert score_listing(over, CRIT)[2] == "prix > max"
    s, d, why = score_listing({**over, "enrichment": {"user_added": True}}, CRIT)
    assert why is None and d["prix"] == 0 and s > 0


def test_multi_source_listing_scored_on_lowest_active_price_and_filled_land():
    """Bien fusionné (cluster) : prix retenu = offre active la moins chère ; terrain complété par une autre source."""
    alone = {**BASE, "price": 455000, "land_m2": None}
    merged = {**alone, "cluster": {"price": {"value": 399000, "offer_id": 2}, "fill": {"land_m2": 420}}}
    s0, d0, _ = score_listing(alone, CRIT)
    s1, d1, _ = score_listing(merged, CRIT)
    assert (d0["prix"], d0["jardin"]) == (5, 1) and (d1["prix"], d1["jardin"]) == (15, 12) and s1 > s0


def test_filled_position_brings_its_commute():
    row = {**BASE, "commute": {"total_min": 55}, "geo_precision": "city", "lat": 50.68, "lon": 3.05,
           "cluster": {"fill": {"lat": 50.69, "lon": 3.04, "geo_precision": "exact", "commute": {"total_min": 25}}}}
    assert score_listing(row, CRIT)[1]["trajet"] == 8


def test_cluster_price_exclusion_uses_retained_price():
    row = {**BASE, "price": 600000, "cluster": {"price": {"value": 520000, "offer_id": 9}}}
    assert score_listing(row, CRIT)[2] is None


# ---------- type retenu (spec property-type-resolution) ----------
from app.scoring import resolve_type, type_info  # noqa: E402

EVIDENCE = {"left": "house", "right": "garage", "seen_on": ["listing_photos"], "photos": [0, 3], "notes": "garage à droite"}


def adj(typ, confidence="exact", usable=True, walls=(9.0, 8.0), links=(), warnings=()):
    return {"status": "ok", "type": typ, "usable": usable, "warnings": list(warnings), "input": {"confidence": confidence},
            "neighbours": [{"kind": "living", "length_m": x, "side": s} for x, s in zip(walls, ("left", "right"))]
            + [{"kind": "annex_link", "length_m": x, "side": "rear"} for x in links]}


def test_map_beats_old_agent_verdict_and_flags_the_disagreement():
    row = {**BASE, "property_type": None, "enrichment": {"property_type": "detached"}, "adjacency": adj("terraced")}
    t = type_info(row)
    assert (t["type"], t["source"], t["confidence"], t["disagreement"]) == ("terraced", "map", "high", True)
    assert t["summary"] == "Mitoyenne · carte (2 murs : 9 m, 8 m)"
    agent = next(v for v in t["verdicts"] if v["source"] == "agent")
    assert agent["disagrees"] and agent["detail"] == "ancien verdict, sans preuves"


def test_agent_with_evidence_beats_the_map():
    row = {**BASE, "enrichment": {"property_type": "link_detached", "type_evidence": EVIDENCE},
           "adjacency": adj("terraced")}
    t = type_info(row)
    assert (t["type"], t["source"], t["disagreement"]) == ("link_detached", "agent", True)
    assert next(v for v in t["verdicts"] if v["used"])["detail"] == \
        "gauche : maison · droite : garage · photos 0, 3 · photos de l'annonce"


def test_user_correction_wins_over_everything():
    row = {**BASE, "enrichment": {"property_type_user": "semi", "property_type": "detached", "type_evidence": EVIDENCE},
           "adjacency": adj("terraced")}
    t = resolve_type(row)
    assert (t["type"], t["source"], t["confidence"]) == ("semi", "user", "high")


def test_probable_map_alone_has_medium_confidence():
    row = {**BASE, "property_type": None, "adjacency": adj("semi", confidence="probable", walls=(8.0,))}
    assert resolve_type(row) == {"type": "semi", "label": "Semi-individuelle", "source": "map",
                                 "confidence": "medium", "disagreement": False}


def test_old_agent_verdict_beats_a_probable_map():
    row = {**BASE, "enrichment": {"property_type": "semi"}, "adjacency": adj("terraced", confidence="probable")}
    assert resolve_type(row)["source"] == "agent"


def test_weak_text_does_not_create_a_disagreement_but_explicit_text_does():
    weak = {**BASE, "property_type": "terraced", "property_type_src": "text_weak", "adjacency": adj("semi", walls=(8.0,)),
            "title": "Maison de ville", "description": ""}
    t = type_info(weak)
    assert (t["type"], t["disagreement"]) == ("semi", False)
    assert next(v for v in t["verdicts"] if v["source"] == "listing")["detail"] == "« maison de ville » (indice faible)"
    explicit = {**weak, "property_type": "detached", "property_type_src": "text_explicit", "title": "Maison individuelle"}
    assert type_info(explicit)["disagreement"] is True


def test_unusable_map_is_shown_but_not_used():
    row = {**BASE, "property_type": "detached", "property_type_src": "text_explicit",
           "adjacency": adj("terraced", usable=False, warnings=["multi_dwelling"])}
    t = type_info(row)
    assert (t["type"], t["source"], t["disagreement"]) == ("detached", "listing", False)
    m = next(v for v in t["verdicts"] if v["source"] == "map")
    assert not m["used"] and "plusieurs logements" in m["detail"] and "non utilisée" in m["detail"]


def test_unknown_is_not_a_verdict():
    row = {**BASE, "property_type": "unknown", "enrichment": {"property_type": "unknown"}}
    assert resolve_type(row)["type"] == "unknown" and resolve_type(row)["source"] is None
    assert score_listing(row, CRIT)[1]["type"] == 2


def test_link_detached_is_worth_3_more_points_than_semi():
    semi = score_listing({**BASE, "property_type": "semi"}, CRIT)
    link = score_listing({**BASE, "property_type": "link_detached"}, CRIT)
    assert link[1]["type"] - semi[1]["type"] == 3 and link[0] - semi[0] == 3
    assert [score_listing({**BASE, "property_type": t}, CRIT)[1]["type"]
            for t in ("detached", "link_detached", "semi", "terraced", "unknown")] == [12, 10, 7, 2, 2]


def test_agent_property_type_no_longer_overrides_the_column_directly():
    from app.scoring import effective
    row = {**BASE, "property_type": "terraced", "property_type_src": "structured",
           "enrichment": {"property_type": "Maison mitoyenne"}}              # prose : ignorée
    assert effective(row)["property_type"] == "terraced"
