"""Calcul cartographique de la mitoyenneté (spec building-adjacency) : `analyse()` sur des scènes synthétiques,
puis sur les cas réels du spike (fixtures git-ignorées, ignorées si absentes)."""
import json
import os
import httpx
import pytest
from app import adjacency as adj
from tests.adjacency_helpers import building, house, ll, parcel, plot, scene


def run(buildings, parcels, **kw):
    return adj.analyse(scene(buildings, parcels, **kw))


def by_kind(res, kind):
    return [n for n in res["neighbours"] if n["kind"] == kind]


# ---------- murs communs ----------
def test_row_house_with_9m_and_8m_walls():
    res = run([house(), building("L", -6, 0, 0, 9), building("R", 6, 0, 12, 8)],
              plot() + plot("PL", -6, 0) + plot("PR", 6, 12))
    assert res["status"] == "ok" and res["type"] == "terraced" and res["usable"]
    walls = {n["side"]: n["length_m"] for n in by_kind(res, "living")}
    assert walls == {"left": 9.0, "right": 8.0}            # vu de la rue : voisin ouest à gauche
    assert res["living_walls"] == 2 and res["annex_links"] == 0
    assert res["target"]["method"] == "link" and res["target"]["parcel"] == "P0"
    assert res["orientation"] == {"facade_bearing": 180, "estimated": True}


def test_neighbour_across_a_2m_passage_is_not_attached():
    res = run([house(), building("R", 8, 0, 14, 9)], plot() + plot("PR", 6, 14))
    assert res["type"] == "detached" and res["neighbours"] == [] and res["nearest_m"] == 2.0


def test_corner_touch_of_1m_is_ignored():
    res = run([house(), building("C", 6, 8, 12, 17)], plot() + plot("PR", 6, 12))
    assert res["type"] == "detached" and res["neighbours"] == []


def test_neighbour_made_of_two_volumes_counts_once():
    t = building("T", 0, 0, 6, 12)
    res = run([t, building("R", 6, 0, 12, 8), building("R2", 6, 8, 12, 12, h=3.6)], plot() + plot("PR", 6, 12))
    assert res["type"] == "semi" and len(res["neighbours"]) == 1
    assert res["neighbours"][0]["parcel"] == "PR" and res["neighbours"][0]["length_m"] == 12.0


def test_own_garage_attached_to_the_house_is_part_of_the_property():
    res = run([house(), building("G", 6, 0, 9, 6, h=2.5, usage="Annexe")], plot(x1=9))
    assert res["type"] == "detached" and res["neighbours"] == [] and res["target"]["buildings"] == 2


# ---------- nature des volumes ----------
def test_neighbours_garage_against_the_house_is_an_annex_link():
    res = run([house(), building("G", 6, 0, 9, 6, h=2.5, usage="Annexe")], plot() + plot("PR", 6, 12))
    [n] = res["neighbours"]
    assert (n["kind"], n["ours"], n["theirs"], n["length_m"]) == ("annex_link", "main", "annex", 6.0)
    assert res["type"] == "link_detached"


def test_own_garage_against_the_neighbours_house_is_an_annex_link():
    res = run([house(), building("G", 6, 0, 9, 6, h=2.5, usage="Annexe"), building("R", 9, 0, 15, 9)],
              plot(x1=9) + plot("PR", 9, 15))
    [n] = res["neighbours"]
    assert (n["kind"], n["ours"], n["theirs"]) == ("annex_link", "annex", "main")
    assert res["type"] == "link_detached"


def test_shed_against_shed_is_ignored():
    res = run([house(), building("S1", 4, 25, 6, 27, h=2.2, legere=True),
               building("S2", 6, 25, 8, 27, h=2.2, legere=True)], plot() + plot("PR", 6, 12))
    assert res["type"] == "detached" and res["neighbours"] == []


def test_low_undifferentiated_volume_is_an_annex():
    assert adj._kind({"usage_1": "Indifférencié", "hauteur": 3.2}, 40) == "annex"
    assert adj._kind({"usage_1": "Indifférencié", "hauteur": 4.0}, 20) == "annex"
    assert adj._kind({"usage_1": "Indifférencié", "hauteur": 4.0}, 40) == "main"
    assert adj._kind({"usage_1": "Résidentiel", "hauteur": 2.8}, 60) == "annex"
    assert adj._kind({"usage_1": "Résidentiel", "hauteur": None}, 60) == "main"


# ---------- catégories ----------
@pytest.mark.parametrize("others, parcels, expected", [
    ([building("L", -6, 0, 0, 9), building("R", 6, 0, 12, 9)], plot("PL", -6, 0) + plot("PR", 6, 12), "terraced"),
    ([building("L", -6, 0, 0, 9), building("G", 6, 0, 9, 6, h=2.5, usage="Annexe")],
     plot("PL", -6, 0) + plot("PR", 6, 12), "semi"),                                # semi + garage du voisin
    ([building("G", 6, 0, 9, 6, h=2.5, usage="Annexe")], plot("PR", 6, 12), "link_detached"),
    ([], plot("PR", 6, 12), "detached"),
])
def test_four_categories(others, parcels, expected):
    assert run([house()] + others, plot() + parcels)["type"] == expected


# ---------- avertissements ----------
def test_building_with_three_dwellings_is_not_usable():
    res = run([house(logts=3), building("R", 6, 0, 12, 9)], plot() + plot("PR", 6, 12))
    assert "multi_dwelling" in res["warnings"] and not res["usable"] and res["type"] == "semi"


def test_huge_footprint_is_not_usable():
    res = run([building("T", 0, 0, 20, 20)], [parcel("P0", -1, -1, 21, 30)])
    assert "large_footprint" in res["warnings"] and not res["usable"] and res["type"] == "detached"


def test_no_building_found():
    res = run([], plot())
    assert res["status"] == "no_building" and res["type"] is None and not res["usable"]
    assert "no_building" in res["warnings"]


def test_fallback_on_the_parcel_without_address_link():
    res = run([house(), building("A", 1, 20, 3, 22, h=2.2, legere=True)], plot(), link=False)
    assert res["target"]["method"] == "parcel" and res["target"]["bdtopo_id"] == "T"
    assert "fallback_target" in res["warnings"] and res["usable"]


def test_fallback_on_the_nearest_building_without_parcel():
    res = run([house()], [], link=False)
    assert res["target"]["method"] == "nearest" and "fallback_target" in res["warnings"]


def test_probable_address_and_ban_mismatch_are_reported_but_not_blocking():
    res = run([house()], plot(), confidence="probable", warnings=["ban_mismatch"])
    assert {"probable_address", "ban_mismatch"} <= set(res["warnings"]) and res["usable"]


def test_demolished_buildings_are_ignored():
    res = run([house(), building("R", 6, 0, 12, 9, etat="Détruit")], plot() + plot("PR", 6, 12))
    assert res["type"] == "detached"


def test_geometry_is_rounded_geojson_with_roles():
    res = run([house(), building("R", 6, 0, 12, 9)], plot() + plot("PR", 6, 12))
    g = res["geometry"]
    roles = sorted(f["properties"]["role"] for f in g["features"])
    assert roles == ["neighbour_main", "parcel", "shared_wall", "target"]
    wall = next(f for f in g["features"] if f["properties"]["role"] == "shared_wall")
    assert wall["properties"]["kind"] == "living"

    def numbers(c):
        return [c] if isinstance(c, float) else [x for v in c for x in numbers(v)]
    values = [x for f in g["features"] for x in numbers(f["geometry"]["coordinates"])]
    assert values and all(round(x, 6) == x for x in values)
    assert len(json.dumps(g)) < 5000


# ---------- cas réels du spike (fixtures git-ignorées) ----------
FIX = "tests/fixtures/adjacency/"
REAL = {    # fixture : (type attendu, avertissements attendus, natures des contacts)
    "rangee": ("terraced", set(), ["living", "living"]),
    "semi": ("semi", set(), ["living"]),
    "isolee_garage": ("detached", set(), []),
    "garage_voisin": ("link_detached", set(), ["annex_link"]),
    "garage_propre_contre_voisin": ("semi", set(), ["annex_link", "living"]),   # mur habitable + notre annexe
    "abri_contre_abri": ("detached", set(), []),
    "plusieurs_logements": (None, {"multi_dwelling"}, None),
    "grande_emprise": (None, {"large_footprint"}, None),
    "repli_parcelle": (None, {"fallback_target"}, None),
    "repli_proximite": (None, {"fallback_target"}, None),
    "aucun_batiment": (None, {"no_building"}, None),
}


@pytest.mark.skipif(not os.path.isdir(FIX), reason="fixtures réelles absentes (git-ignorées)")
@pytest.mark.parametrize("name", sorted(REAL))
def test_real_cases(name):
    typ, warns, kinds = REAL[name]
    res = adj.analyse(json.load(open(f"{FIX}{name}.json", encoding="utf-8")))
    if typ:
        assert res["type"] == typ
    assert warns <= set(res["warnings"])
    if kinds is not None:
        assert sorted(n["kind"] for n in res["neighbours"]) == kinds
    if name == "rangee":
        assert sorted(n["side"] for n in res["neighbours"]) == ["left", "right"]
    if name in ("plusieurs_logements", "grande_emprise", "aucun_batiment"):
        assert not res["usable"]
    if name == "repli_parcelle":
        assert res["target"]["method"] == "parcel" and res["usable"]
    if name == "repli_proximite":
        assert res["target"]["method"] == "nearest" and res["usable"]
    if name == "isolee_garage":
        assert res["target"]["buildings"] >= 2           # maison + son garage, sur la même parcelle
    if name == "abri_contre_abri":
        assert res["nearest_m"] == 0                     # un contact existe, mais entre deux annexes
    if name == "garage_voisin":
        assert (res["neighbours"][0]["ours"], res["neighbours"][0]["theirs"]) == ("main", "annex")
    if name == "garage_propre_contre_voisin":
        link = next(n for n in res["neighbours"] if n["kind"] == "annex_link")
        assert (link["ours"], link["theirs"]) == ("annex", "main")


# ---------- récupération (réseau simulé) ----------
def geoplateforme(calls, fail=lambda layer, n: False):
    """Client simulé : BAN + WFS de la Géoplateforme servis à partir de la scène « rangée »."""
    lon, lat = ll(3, -2)
    layers = {
        adj.LAYER_LINKS: [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [ll(3, -2), ll(3, 1)]},
                           "properties": {"id_adr": "00000_0000_00001", "id_bat": "T", "idu": "P0", "nb_bat": 1}}],
        adj.LAYER_BUILDINGS: [house(), building("L", -6, 0, 0, 9), building("R", 6, 0, 12, 8)],
        adj.LAYER_PARCELS: plot() + plot("PL", -6, 0) + plot("PR", 6, 12),
    }

    def handler(req):
        calls.append(req)
        if req.url.host == "api-adresse.data.gouv.fr":
            return httpx.Response(200, json={"features": [{"geometry": {"type": "Point", "coordinates": [lon, lat]},
                                                           "properties": {"type": "housenumber", "score": 0.93,
                                                                          "id": "00000_0000_00001", "label": "1 rue"}}]})
        layer = req.url.params["TYPENAMES"]
        if fail(layer, sum(1 for c in calls if c.url.params.get("TYPENAMES") == layer)):
            return httpx.Response(504, text="Gateway Time-out")
        return httpx.Response(200, json={"type": "FeatureCollection", "features": layers[layer]})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=False)
def no_wait(monkeypatch):
    monkeypatch.setattr(adj, "RETRY_DELAYS_S", (0, 0))


def test_fetch_queries_only_ign_and_ban_with_lat_lon_bbox(no_wait):
    calls = []
    lon, lat = ll(3, -2)
    with geoplateforme(calls) as client:
        ctx = adj.fetch(client, (lon, lat), "1 rue de l'Exemple")
    assert {c.url.host for c in calls} == {"api-adresse.data.gouv.fr", "data.geopf.fr"}
    assert ctx["ban"]["id"] == "00000_0000_00001" and ctx["warnings"] == []
    link = next(c for c in calls if c.url.params.get("TYPENAMES") == adj.LAYER_LINKS)
    assert link.url.params["CQL_FILTER"] == "id_adr='00000_0000_00001'"
    bbox = next(c for c in calls if c.url.params.get("TYPENAMES") == adj.LAYER_BUILDINGS).url.params["BBOX"]
    lat0, lon0, lat1, lon1, crs = bbox.split(",")
    assert crs == "urn:ogc:def:crs:EPSG::4326"
    assert float(lat0) < lat < float(lat1) and float(lon0) < lon < float(lon1)      # ordre lat, lon (WFS 2.0)
    assert 110 < adj._dist_m(float(lon0), lat, float(lon1), lat) < 130             # ±60 m
    assert {c.headers["user-agent"] for c in calls} == {adj.USER_AGENT}               # identifié, ASCII
    assert adj.analyse(ctx)["type"] == "terraced"


def test_fetch_retries_a_504(no_wait):
    calls = []
    lon, lat = ll(3, -2)
    with geoplateforme(calls, fail=lambda layer, n: layer == adj.LAYER_BUILDINGS and n == 1) as client:
        res = adj.compute(client, lon, lat, "1 rue de l'Exemple", "exact")
    assert res["status"] == "ok" and res["type"] == "terraced"
    assert sum(1 for c in calls if c.url.params.get("TYPENAMES") == adj.LAYER_BUILDINGS) == 2


def test_definitive_failure_gives_status_error(no_wait):
    calls = []
    lon, lat = ll(3, -2)
    with geoplateforme(calls, fail=lambda layer, n: layer == adj.LAYER_PARCELS) as client:
        res = adj.compute(client, lon, lat, "1 rue de l'Exemple", "exact")
    assert res["status"] == "error" and "504" in res["error"] and res["tried_at"]
    assert res["input"] == {"address": "1 rue de l'Exemple", "lat": lat, "lon": lon, "confidence": "exact"}
    assert sum(1 for c in calls if c.url.params.get("TYPENAMES") == adj.LAYER_PARCELS) == 3   # 1 + 2 essais


def test_far_ban_result_is_a_mismatch(no_wait):
    calls = []
    lon, lat = ll(3, 60)                     # point stocké à ~60 m du numéro trouvé par la BAN
    with geoplateforme(calls) as client:
        ctx = adj.fetch(client, (lon, lat), "1 rue de l'Exemple")
    assert ctx["ban"] is None and ctx["warnings"] == ["ban_mismatch"]
    assert not any(c.url.params.get("TYPENAMES") == adj.LAYER_LINKS for c in calls)
