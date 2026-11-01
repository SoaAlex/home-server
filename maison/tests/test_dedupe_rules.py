"""Règles de déduplication (spec listing-deduplication) : compatibilité, vétos, texte, programme neuf,
photos génériques, décisions, plan de fusion sans chaînage."""
from app import db, dedupe
from tests.test_dedupe_db import add

WASQUEHAL, VDA, LILLE, ARMENTIERES = (50.6686, 3.1307), (50.6232, 3.1446), (50.6292, 3.0573), (50.6886, 2.8811)

AGENCY_TEXT = (
    "Au calme dans une rue résidentielle, cette maison semi individuelle des années trente offre au rez de chaussée "
    "un séjour lumineux ouvert sur une cuisine équipée, une véranda donnant sur le jardin exposé sud ouest et un "
    "bureau. A l'étage, trois chambres dont une suite parentale avec dressing, une salle de bains avec baignoire et "
    "un wc séparé. Grenier aménageable sur toute la surface. Garage avec porte motorisée et cave saine. Chauffage "
    "gaz par chaudière à condensation récente, double vitrage, toiture refaite. Proche écoles, commerces et tramway, "
    "accès rapide à la voie rapide. Une visite s'impose pour découvrir tout le potentiel de ce bien familial.")


def it(i=1, **kw):
    row = dict(id=i, source="bienici", price=300000, surface_m2=100, land_m2=None, rooms=5, bedrooms=3,
               city="Wambrechies", postal_code="59118", lat=None, lon=None, description="", title="Maison",
               enrichment={}, raw={}, merged_into=None, photos=[], photo_urls=[], url=f"https://x/{i}",
               status="active", agency=None, dedupe_sig=None)
    pos = kw.pop("pos", None)
    if pos:
        row.update(lat=pos[0], lon=pos[1])
    row.update(kw)
    return dedupe.item_of(row)


# ---------- 3.1 compatibilité et vétos ----------
def test_close_surfaces_are_compatible_and_far_ones_vetoed():
    assert dedupe.hard_veto(it(1, surface_m2=100), it(2, surface_m2=101)) is None
    assert dedupe.hard_veto(it(1, surface_m2=100), it(2, surface_m2=130)) == "surface"


def test_unknown_values_never_block():
    assert dedupe.hard_veto(it(1, surface_m2=None, bedrooms=None), it(2, surface_m2=150, bedrooms=6)) is None
    kind, _ = dedupe.decide(it(1, surface_m2=None), it(2, surface_m2=None, price=305000), photos=1)
    assert kind == "auto"                          # une seule caractéristique connue (prix) suffit avec 1 photo


def test_bedrooms_and_distance_vetoes():
    assert dedupe.hard_veto(it(1, bedrooms=3), it(2, bedrooms=5)) == "chambres"
    assert dedupe.hard_veto(it(1, pos=LILLE), it(2, pos=ARMENTIERES)) == "distance"


def test_neighbouring_communes_do_not_block_merge():
    a = it(1, city="Wasquehal", postal_code="59290", pos=WASQUEHAL, surface_m2=68, price=190000, agency="Deleu")
    b = it(2, city="Villeneuve-d'Ascq", postal_code="59650", pos=VDA, surface_m2=68, price=205000, agency="Deleu")
    assert dedupe.hard_veto(a, b) is None
    kind, ev = dedupe.decide(a, b, photos=5)
    assert kind == "auto" and ev["price"] == [190000, 205000]


# ---------- 3.2 texte et programme neuf ----------
def test_agency_copy_is_near_identical_text():
    copy = AGENCY_TEXT.replace("Une visite s'impose", "Visite vivement conseillée") + " Réf 1234."
    a, b = it(1, description=AGENCY_TEXT), it(2, description=copy, price=310000)
    assert dedupe.containment(a, b) >= dedupe.TEXT_CONTAINMENT
    kind, ev = dedupe.decide(a, b, photos=0)
    assert kind == "auto" and ev["rule"] == "texte quasi identique"


def test_short_email_card_text_is_not_compared():
    card = it(2, description="Maison 5 pièces 100 m² terrain 300 m² Wambrechies (59118) 300 000 €")
    assert dedupe.containment(it(1, description=AGENCY_TEXT), card) is None


def test_new_build_detection():
    assert dedupe.is_newbuild({"raw": {"newProperty": True}, "description": ""})
    for text in ("PROGRAMME NEUF MAISON - Résidence Les Jardins", "Maison en VEFA, frais de notaire réduits",
                 "Livraison prévue au 3e trimestre 2028", "Accession PSLA, maison T4"):
        assert dedupe.is_newbuild({"raw": {}, "title": text, "description": ""}), text
    assert not dedupe.is_newbuild({"raw": {"newProperty": False}, "title": "Maison 1930", "description": AGENCY_TEXT})


# ---------- 3.4 décisions ----------
def test_two_shared_photos_merge_automatically():
    a, b = it(1, source="netty", surface_m2=118), it(2, surface_m2=118)
    assert dedupe.decide(a, b, photos=3)[0] == "auto"


def test_single_thumbnail_card_merges_with_same_surface_and_close_price():
    card = it(2, source="seloger", surface_m2=118, price=500000, rooms=6, description="Maison 6 pièces")
    assert dedupe.decide(it(1, surface_m2=118, price=485000), card, photos=1)[0] == "auto"


def test_one_photo_with_surface_gap_is_only_suggested():
    kind, ev = dedupe.decide(it(1, surface_m2=97), it(2, surface_m2=90), photos=1)
    assert kind == "suggest" and "vérifier" in ev["rule"]


def test_new_build_lots_are_only_suggested():
    a = it(1, title="PROGRAMME NEUF MAISON", surface_m2=114, price=None)
    b = it(2, title="RESIDENCE GASTON BARATTE", surface_m2=115.68, raw={"newProperty": True}, price=None)
    kind, ev = dedupe.decide(a, b, photos=2)
    assert kind == "suggest" and "programme neuf" in ev["rule"]


def test_twins_without_shared_photo_are_merged():
    a = it(1, source="seloger", price=497000, surface_m2=118, land_m2=367, rooms=6, bedrooms=4, city="Bondues")
    b = it(2, source="seloger", price=497000, surface_m2=118, land_m2=367, rooms=6, bedrooms=4, city="Bondues")
    kind, ev = dedupe.decide(a, b, photos=0)
    assert kind == "auto" and ev["rule"] == "caractéristiques jumelles"
    assert ev["surface"] == [118, 118] and ev["price"] == [497000, 497000]


def test_twins_need_close_price_surface_and_land():
    a = it(1, price=372000, surface_m2=89, land_m2=125)
    assert dedupe.decide(a, it(2, price=372000, surface_m2=89, land_m2=254), photos=0)[0] == "none"   # terrains
    assert dedupe.decide(a, it(2, price=380000, surface_m2=89), photos=0)[0] == "none"                # prix +2 %
    assert dedupe.decide(a, it(2, price=372000, surface_m2=91), photos=0)[0] == "none"                # 2 m²
    assert dedupe.decide(a, it(2, price=373500, surface_m2=89.5), photos=0)[0] == "auto"


def test_new_build_twins_are_only_suggested():
    a = it(1, price=320000, surface_m2=90, title="Programme neuf : maison T4")
    kind, ev = dedupe.decide(a, it(2, price=320000, surface_m2=90), photos=0)
    assert kind == "suggest" and "programme neuf" in ev["rule"]


def test_rejected_pair_gives_nothing():
    assert dedupe.decide(it(1), it(2), photos=4, rejected=True)[0] == "none"


def test_unrelated_listings_give_nothing():
    assert dedupe.decide(it(1, price=300000), it(2, price=420000, surface_m2=104), photos=0)[0] == "none"


def test_plan_refuses_chaining_onto_incompatible_group():
    """A fusionnable avec B, mais C (déjà avec B) a un veto avec A : suggestion, pas de fusion."""
    a, b, c = it(1, surface_m2=100), it(2, surface_m2=104, merged_into=3), it(3, surface_m2=113)
    items = {1: a, 2: b, 3: c}
    members = {1: {1}, 3: {2, 3}}
    ops = dedupe.plan([(1, 2, "auto", {"photos": 2, "rule": "2 photos communes"})], items, members, set(), {})
    assert ops[0][0] == "suggest" and "veto dans le groupe" in ops[0][3]["rule"]


def test_plan_skips_groups_with_a_rejection_between_members():
    items = {1: it(1), 2: it(2, merged_into=3), 3: it(3)}
    ops = dedupe.plan([(1, 2, "auto", {"photos": 3, "rule": "3 photos communes"})], items,
                      {1: {1}, 3: {2, 3}}, {(1, 3)}, {(1, 3): "rejected"})
    assert ops == []


# ---------- 3.3 photos communes et génériques (en base) ----------
def test_agency_banner_shared_by_incompatible_listings_is_generic(dburl, tmp_path):
    with db.connect(dburl) as conn:
        v1 = add(conn, tmp_path, seeds=(11, 12, 999), price=75075, surface_m2=97, ext="v1")
        v2 = add(conn, tmp_path, seeds=(21, 22, 999), price=230900, surface_m2=90, ext="v2")
        v3 = add(conn, tmp_path, seeds=(31, 999), price=120000, surface_m2=60, ext="v3")
        dedupe.ensure_hashes(conn, base=tmp_path)
        res = dedupe.analyze(conn, changed_only=False)
    assert {(v1, 2), (v2, 2), (v3, 1)} <= res["generic"]
    assert res["ops"] == []                       # aucune fusion ni suggestion sur le seul bandeau


def test_cover_photo_shared_by_compatible_listings_is_not_generic(dburl, tmp_path):
    with db.connect(dburl) as conn:
        ids = [add(conn, tmp_path, seeds=(500, 501, 502), price=497000, surface_m2=118, ext="bi")]
        ids += [add(conn, tmp_path, seeds=(500,), source="seloger", price=497000, surface_m2=118, land_m2=367,
                    ext=f"sl{k}") for k in range(4)]
        dedupe.ensure_hashes(conn, base=tmp_path)
        res = dedupe.analyze(conn, changed_only=False)
    assert res["generic"] == set()
    merges = [op for op in res["ops"] if op[0] == "merge"]
    assert len(merges) == 4 and {x for op in merges for x in op[1:3]} == set(ids)


def test_single_shared_photo_counts_once_in_full_analysis(dburl, tmp_path):
    """Régression : en analyse complète, chaque paire de photos est vue dans les deux sens ; une photo commune
    comptait pour deux et déclenchait une fusion sans contrôle de la surface ni du prix."""
    with db.connect(dburl) as conn:
        a = add(conn, tmp_path, seeds=(300, 301, 302), price=300000, surface_m2=100, ext="a")
        b = add(conn, tmp_path, seeds=(300,), price=420000, surface_m2=108, ext="b", source="seloger")
        dedupe.ensure_hashes(conn, base=tmp_path)
        res = dedupe.analyze(conn, changed_only=False)
    assert [(op, x, y) for op, x, y, _ in res["ops"]] == [("suggest", min(a, b), max(a, b))]
    assert res["ops"][0][3]["photos"] == 1 and "à vérifier" in res["ops"][0][3]["rule"]


def test_two_precise_addresses_far_apart_are_different_houses():
    a = it(1, pos=(50.7000, 3.0500), geo_precision="exact")
    b = it(2, pos=(50.6905, 3.0450), geo_precision="exact")          # ≈ 1,1 km
    assert dedupe.address_conflict(a, b) and not dedupe.address_conflict(a, {**b, "precise": False})
    assert dedupe.hard_veto(a, b) is None                    # simple indice : jamais une photo générique
    twin = dict(price=372000, surface_m2=89)
    assert dedupe.decide(it(1, **twin, pos=(50.70, 3.05), geo_precision="exact"),
                         it(2, **twin, pos=(50.6905, 3.045), geo_precision="exact"))[0] == "none"
    assert dedupe.decide({**a, "surface": 100}, {**b, "surface": 100}, photos=1)[0] == "suggest"
    kind, ev = dedupe.decide({**a, "surface": 100}, {**b, "surface": 100}, photos=3)
    assert kind == "auto" and "adresse probable à revoir" in ev["rule"]       # les photos l'emportent


def test_twin_check_uses_values_completed_by_the_group(dburl):
    """Le terrain d'un bien fusionné vient parfois d'une autre source : il doit compter pour les jumeaux."""
    with db.connect(dburl) as conn:
        p = add(conn, price=372000, surface_m2=89, rooms=4, ext="p")
        m = add(conn, price=372000, surface_m2=89, land_m2=125, ext="m", source="seloger")
        dedupe.merge(conn, p, m)
        o = add(conn, price=375000, surface_m2=90, land_m2=254, rooms=5, ext="o", source="netty")
        res = dedupe.analyze(conn, ids=[o])
    assert res["items"][p]["land"] == 125
    assert res["ops"] == []                    # terrains 125 / 254 : pas des jumeaux


def test_twins_do_not_chain_into_incompatible_group():
    """A≈B et B≈C sont jumeaux, mais A et C n'ont pas le même terrain : une seule fusion (la plus proche)."""
    a = it(1, price=365000, surface_m2=90, land_m2=144)
    b = it(2, price=367000, surface_m2=91, land_m2=None)
    c = it(3, price=367000, surface_m2=91, land_m2=325)
    items = {1: a, 2: b, 3: c}
    decisions = [(x, y, *dedupe.decide(items[x], items[y])) for x, y in ((1, 2), (2, 3))]
    assert [d[2] for d in decisions] == ["auto", "auto"]
    ops = dedupe.plan(decisions, items, {1: {1}, 2: {2}, 3: {3}}, set(), {})
    assert [(op, x, y) for op, x, y, _ in ops] == [("merge", 2, 3), ("suggest", 1, 2)]
    assert "terrain" in ops[1][3]["rule"]


def test_repeated_email_cards_of_one_house_all_merge(dburl):
    """SeLoger change d'identifiant à chaque e-mail : N cartes identiques + l'annonce d'agence = un seul bien."""
    with db.connect(dburl) as conn:
        ids = [add(conn, price=379000, surface_m2=85, rooms=4, ext="netty", source="netty")]
        ids += [add(conn, price=378999 + k % 2, surface_m2=85 + k % 2, rooms=4, ext=f"card{k}", source="seloger")
                for k in range(6)]
        res = dedupe.analyze(conn, changed_only=False)
        dedupe.apply(conn, res)
        groups = {r["g"] for r in conn.execute("SELECT coalesce(merged_into, id) AS g FROM listing WHERE id = ANY(%s)",
                                               (ids,))}
    assert len(groups) == 1 and sum(op == "suggest" for op, *_ in res["ops"]) == 0
