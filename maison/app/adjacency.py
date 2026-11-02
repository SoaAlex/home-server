"""Mitoyenneté calculée à partir des données ouvertes de l'IGN (change OpenSpec improve-adjacency-detection).

- `fetch()` interroge la BAN et le WFS de la Géoplateforme (BD TOPO, BAN PLUS, Parcellaire Express) ;
- `analyse()` est pure : elle travaille sur ce contexte (entités GeoJSON) dans une projection locale en mètres ;
- `process()` est l'étape du collecteur : annonces à adresse précise, résultat dans `listing.adjacency`.
Règles et seuils : design D1-D5 (seuils issus du spike, recalibrés sur le rapport du rattrapage).
"""
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import shapely
from psycopg.types.json import Jsonb
from shapely.geometry import Point, shape
from shapely.ops import nearest_points, unary_union

from app.scoring import merged_columns

log = logging.getLogger("adjacency")

V = 1
WFS_URL = "https://data.geopf.fr/wfs/ows"
BAN_URL = "https://api-adresse.data.gouv.fr/search/"
LAYER_BUILDINGS = "BDTOPO_V3:batiment"
LAYER_PARCELS = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS:parcelle"
LAYER_LINKS = "BAN-PLUS:lien_adresse_bati"
TIMEOUT_S = 20
USER_AGENT = "maison/1.0 (recherche immobiliere privee; donnees ouvertes IGN et BAN)"   # ASCII seulement
RETRY_DELAYS_S = (2, 5)            # deux nouvelles tentatives espacées
HALF_BOX_M = 60                    # demi-côté de la boîte de requête autour du point
COUNT = 1000
TOL_M = 0.5                        # tolérance de digitalisation : deux murs à moins de 0,5 m se touchent
MIN_CONTACT_M = 2.0                # longueur minimale d'un mur commun
NEAREST_M = 6.0                    # repli : bâtiment en dur le plus proche, à moins de 6 m
NEAREST_MIN_AREA_M2 = 30
PARCEL_NEAR_M = 8.0                # point d'adresse sur la voirie : parcelle la plus proche, à moins de 8 m
LARGE_FOOTPRINT_M2 = 300
ANNEX_MAX_AREA_M2 = 15             # volume « annexe » (D3)
ANNEX_MAX_HEIGHT_M = 3.0
UNDIFF_MAX_HEIGHT_M = 3.5
UNDIFF_MAX_AREA_M2 = 25
BAN_MIN_SCORE = 0.5
BAN_MAX_DIST_M = 30
FACADE_MIN_M = 1.5                 # point d'adresse trop proche du centre du bâtiment : façade inconnue
RETRY_ERROR_AFTER = timedelta(hours=6)
LIMIT_PER_CYCLE = 40
BLOCKING = ("multi_dwelling", "large_footprint", "no_building")
TYPES = ("detached", "link_detached", "semi", "terraced")
BUILDING_KEYS = ("cleabs", "nature", "usage_1", "usage_2", "construction_legere", "etat_de_l_objet", "hauteur",
                 "nombre_de_logements", "nombre_d_etages", "date_d_apparition", "identifiants_rnb")
PARCEL_KEYS = ("idu", "contenance")
LINK_KEYS = ("id_adr", "id_bat", "idu")


class FetchError(Exception):
    pass


class AddressNotFound(FetchError):
    """Adresse sans numéro reconnu par la BAN, et aucun point fourni."""


# ---------- récupération (réseau) ----------
def request(client, url: str, params: dict, parse=None, follow_redirects: bool = False):
    """GET identifié, délai de 20 s, deux nouvelles tentatives espacées ; `parse(réponse)` peut lever
    ValueError (réponse inexploitable, retentée). Lève FetchError après le dernier essai."""
    last = "?"
    for delay in (0, *RETRY_DELAYS_S):
        if delay:
            time.sleep(delay)
        try:
            r = client.get(url, params=params, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT},
                           follow_redirects=follow_redirects)
        except httpx.HTTPError as e:
            last = type(e).__name__
            continue
        if r.status_code == 200:
            try:
                return parse(r) if parse else r
            except ValueError as e:
                last = f"réponse inexploitable ({e})"[:120]
                continue
        last = f"HTTP {r.status_code}"
        if r.status_code < 500 and r.status_code != 429:
            break                      # requête refusée : inutile d'insister
    raise FetchError(f"{httpx.URL(url).host} : {last}")


def _get_json(client, url: str, params: dict) -> dict:
    return request(client, url, params, parse=lambda r: r.json())


def _slim(feature: dict, keys) -> dict:
    p = feature.get("properties") or {}
    return {"type": "Feature", "geometry": feature.get("geometry"), "properties": {k: p.get(k) for k in keys}}


def _bbox(lon: float, lat: float, half_m: float) -> tuple:
    kx, ky = _scale(lat)
    return lon - half_m / kx, lat - half_m / ky, lon + half_m / kx, lat + half_m / ky


def _wfs(client, layer: str, bbox=None, cql: str | None = None) -> list[dict]:
    p = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": layer,
         "OUTPUTFORMAT": "application/json", "COUNT": COUNT}
    if bbox:            # WFS 2.0 en EPSG:4326 : axes dans l'ordre lat, lon
        lon0, lat0, lon1, lat1 = bbox
        p["BBOX"] = f"{lat0:.7f},{lon0:.7f},{lat1:.7f},{lon1:.7f},urn:ogc:def:crs:EPSG::4326"
    if cql:
        p["CQL_FILTER"] = cql
    return _get_json(client, WFS_URL, p).get("features") or []


def fetch(client, point: tuple[float, float] | None, address: str | None = None) -> dict:
    """Contexte du calcul autour d'un point (lon, lat) et/ou d'une adresse. Lève FetchError (réseau)."""
    lon, lat = point if point else (None, None)
    ctx = {"lon": lon, "lat": lat, "address": address, "ban": None, "warnings": [],
           "links": [], "buildings": [], "parcels": []}
    if address:
        params = {"q": address, "limit": 1, **({"lat": lat, "lon": lon} if point else {})}
        feats = _get_json(client, BAN_URL, params).get("features") or []
        f = feats[0] if feats else {}
        props = f.get("properties") or {}
        if props.get("type") == "housenumber" and (props.get("score") or 0) > BAN_MIN_SCORE:
            blon, blat = f["geometry"]["coordinates"][:2]
            dist = _dist_m(lon, lat, blon, blat) if point else 0.0
            if dist <= BAN_MAX_DIST_M:
                ctx["ban"] = {"id": props.get("id"), "label": props.get("label"), "lon": blon, "lat": blat,
                              "score": round(props["score"], 3), "distance_m": round(dist, 1)}
        if not ctx["ban"]:
            ctx["warnings"].append("ban_mismatch")
    ref = (ctx["ban"]["lon"], ctx["ban"]["lat"]) if ctx["ban"] else point
    if ref is None:
        raise AddressNotFound("adresse introuvable dans la BAN (numéro inconnu)")
    if ctx["lon"] is None:
        ctx["lon"], ctx["lat"] = ref
    ban_id = (ctx["ban"] or {}).get("id") or ""
    if re.fullmatch(r"[A-Za-z0-9_]+", ban_id):
        ctx["links"] = [_slim(f, LINK_KEYS)["properties"] for f in _wfs(client, LAYER_LINKS, cql=f"id_adr='{ban_id}'")]
    box = _bbox(*ref, HALF_BOX_M)
    ctx["buildings"] = [_slim(f, BUILDING_KEYS) for f in _wfs(client, LAYER_BUILDINGS, bbox=box)]
    ctx["parcels"] = [_slim(f, PARCEL_KEYS) for f in _wfs(client, LAYER_PARCELS, bbox=box)]
    return ctx


# ---------- géométrie (pure) ----------
def _scale(lat: float) -> tuple[float, float]:
    """Mètres par degré de longitude et de latitude (ellipsoïde WGS84) à la latitude donnée."""
    p = math.radians(lat)
    return (111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p),
            111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p) - 0.0023 * math.cos(6 * p))


def _dist_m(lon0, lat0, lon1, lat1) -> float:
    kx, ky = _scale((lat0 + lat1) / 2)
    return math.hypot((lon1 - lon0) * kx, (lat1 - lat0) * ky)


class _Proj:
    """Projection équirectangulaire locale (mètres, x vers l'est, y vers le nord) centrée sur un point."""

    def __init__(self, lon0: float, lat0: float):
        self.o = np.array([lon0, lat0])
        self.k = np.array(_scale(lat0))

    def fwd(self, geom):
        return shapely.transform(geom, lambda c: (c - self.o) * self.k)

    def geojson(self, geom) -> dict:
        g = shapely.transform(geom, lambda c: np.round(c / self.k + self.o, 6))
        return json.loads(shapely.to_geojson(g))


def _bearing(dx: float, dy: float) -> int:
    return round(math.degrees(math.atan2(dx, dy))) % 360


def _kind(p: dict, area: float) -> str:
    """Volume principal (« main ») ou annexe (garage, abri, véranda, petit ou bas) : design D3."""
    h = p.get("hauteur") or 0
    if p.get("construction_legere") or p.get("usage_1") == "Annexe" or area < ANNEX_MAX_AREA_M2 \
            or (h and h < ANNEX_MAX_HEIGHT_M):
        return "annex"
    if p.get("usage_1") == "Indifférencié" and ((h and h < UNDIFF_MAX_HEIGHT_M) or area < UNDIFF_MAX_AREA_M2):
        return "annex"
    return "main"


def _polygons(features, proj, key):
    out = {}
    for i, f in enumerate(features):
        p = f.get("properties") or {}
        if p.get("etat_de_l_objet") not in (None, "En service"):
            continue
        try:
            g = proj.fwd(shape(f["geometry"]))
        except Exception:      # géométrie absente ou illisible : ignorée
            continue
        if not g.is_valid:
            g = g.buffer(0)
        if g.is_empty or g.area <= 0:
            continue
        out[p.get(key) or f"#{i}"] = {"geom": g, "p": p, "area": g.area}
    return out


def _lines(g) -> list:
    if g is None or g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [g]
    if g.geom_type in ("MultiLineString", "GeometryCollection"):
        return [x for part in g.geoms for x in _lines(part)]
    return []                                   # points de contact isolés


def _contact_length(g) -> float:
    """Longueur d'un mur commun : la frontière de la propriété dans la zone de tolérance du voisin, moins le
    bout de façade (≈ TOL) que la zone englobe à chaque extrémité d'un tronçon. Un simple angle donne 0."""
    parts = _lines(g)
    if not parts:
        return 0.0
    merged = shapely.line_merge(shapely.MultiLineString(parts)) if len(parts) > 1 else parts[0]
    return sum(max(0.0, x.length - 2 * TOL_M) for x in getattr(merged, "geoms", [merged]))


def _boundary(geoms):
    return unary_union(geoms).boundary if geoms else shapely.GeometryCollection()


def analyse(ctx: dict) -> dict:
    """Mitoyenneté du bâtiment de l'adresse à partir du contexte de `fetch()` (fonction pure)."""
    ban = ctx.get("ban")
    ref_lon, ref_lat = (ban["lon"], ban["lat"]) if ban else (ctx["lon"], ctx["lat"])
    proj = _Proj(ref_lon, ref_lat)
    warnings = list(ctx.get("warnings") or [])
    if ctx.get("confidence") == "probable" and "probable_address" not in warnings:
        warnings.append("probable_address")
    B = _polygons(ctx.get("buildings") or [], proj, "cleabs")
    P = _polygons(ctx.get("parcels") or [], proj, "idu")
    pt = Point(0, 0)
    pids = list(P)
    tree = shapely.STRtree([P[k]["geom"] for k in pids]) if pids else None

    def parcel_of(geom):
        if tree is None:
            return None
        hits = tree.query(geom.representative_point(), predicate="within")
        return pids[hits[0]] if len(hits) else None

    owner = {k: parcel_of(b["geom"]) for k, b in B.items()}
    links = ctx.get("links") or []
    target_ids = [l["id_bat"] for l in links if l.get("id_bat") in B]
    idu = next((l["idu"] for l in links if l.get("idu") in P), None)
    if idu is None and tree is not None:
        hits = tree.query(pt, predicate="within")
        if len(hits):
            idu = pids[hits[0]]
        else:
            near = min(pids, key=lambda k: P[k]["geom"].distance(pt))
            idu = near if P[near]["geom"].distance(pt) <= PARCEL_NEAR_M else None
    method = "link" if target_ids else None
    if not target_ids and idu is not None:
        cands = [k for k, b in B.items() if owner[k] == idu and not b["p"].get("construction_legere")]
        if cands:
            target_ids, method = [max(cands, key=lambda k: B[k]["area"])], "parcel"
    if not target_ids:
        cands = [(b["geom"].distance(pt), k) for k, b in B.items()
                 if not b["p"].get("construction_legere") and b["area"] >= NEAREST_MIN_AREA_M2]
        cands = [c for c in cands if c[0] <= NEAREST_M]
        if cands:
            target_ids, method = [min(cands)[1]], "nearest"
    if not target_ids:
        return {"v": V, "status": "no_building", "type": None, "usable": False,
                "warnings": warnings + ["no_building"], "target": None, "neighbours": [],
                "living_walls": 0, "annex_links": 0, "orientation": None, "nearest_m": None,
                "geometry": _collection(proj, P.get(idu), [], [], [], [])}
    if method != "link":
        warnings.append("fallback_target")
    main = max(target_ids, key=lambda k: B[k]["area"])
    if idu is None:
        idu = owner[main]
    prop = set(target_ids) | ({k for k in B if owner[k] == idu} if idu is not None else set())
    kinds = {k: _kind(b["p"], b["area"]) for k, b in B.items()}
    own_main = {k for k in prop if kinds[k] == "main"} | {main}
    own_annex = prop - own_main
    b_main = _boundary([B[k]["geom"] for k in own_main])
    b_annex = _boundary([B[k]["geom"] for k in own_annex])
    whole = unary_union([B[k]["geom"] for k in prop])

    groups: dict[str, list[str]] = {}
    nearest = None
    for k, b in B.items():
        if k in prop:
            continue
        d = whole.distance(b["geom"])
        nearest = d if nearest is None else min(nearest, d)
        if d <= TOL_M:
            groups.setdefault(owner[k] or f"bat:{k}", []).append(k)

    tp = B[main]["p"]
    centre = B[main]["geom"].centroid
    facade = _facade(centre, P.get(idu), [P[k]["geom"] for k in pids if k != idu])
    neighbours, walls, shown = [], [], []
    for parcel, members in groups.items():
        theirs_main = [B[k]["geom"] for k in members if kinds[k] == "main"]
        theirs_annex = [B[k]["geom"] for k in members if kinds[k] == "annex"]
        z_main = unary_union(theirs_main).buffer(TOL_M) if theirs_main else None
        z_annex = unary_union(theirs_annex).buffer(TOL_M) if theirs_annex else None
        mm = b_main.intersection(z_main) if z_main is not None else None
        am = b_annex.intersection(z_main) if z_main is not None and own_annex else None
        ma = b_main.intersection(z_annex) if z_annex is not None else None
        l_mm, l_am, l_ma = (_contact_length(g) if g is not None else 0.0 for g in (mm, am, ma))
        if l_mm >= MIN_CONTACT_M:
            kind, ours, theirs, length, wall = "living", "main", "main", l_mm, mm
        elif max(l_am, l_ma) >= MIN_CONTACT_M:      # liaison par un garage / une annexe (le nôtre ou le sien)
            if l_am >= l_ma:
                kind, ours, theirs, length, wall = "annex_link", "annex", "main", l_am, am
            else:
                kind, ours, theirs, length, wall = "annex_link", "main", "annex", l_ma, ma
        else:                                        # angle, contact trop court, ou annexe contre annexe
            continue
        c = wall.centroid
        bearing = _bearing(c.x - centre.x, c.y - centre.y)
        heights = [B[k]["p"].get("hauteur") for k in members if B[k]["p"].get("hauteur")]
        neighbours.append({"parcel": None if parcel.startswith("bat:") else parcel, "kind": kind,
                           "side": _side(wall, centre, facade), "bearing": bearing, "length_m": round(length, 1),
                           "ours": ours, "theirs": theirs, "their_height_m": max(heights) if heights else None})
        walls.append((wall, kind))
        shown += [(B[k]["geom"], kinds[k]) for k in members]
    neighbours.sort(key=lambda n: (n["kind"] != "living", -n["length_m"]))
    living = sum(n["kind"] == "living" for n in neighbours)
    links_n = sum(n["kind"] == "annex_link" for n in neighbours)
    typ = "terraced" if living >= 2 else "semi" if living == 1 else "link_detached" if links_n else "detached"
    dwellings = tp.get("nombre_de_logements")
    if (dwellings or 0) > 1:
        warnings.append("multi_dwelling")
    if B[main]["area"] > LARGE_FOOTPRINT_M2:
        warnings.append("large_footprint")
    year = str(tp.get("date_d_apparition") or "")[:4]
    target = {"method": method, "bdtopo_id": main, "rnb_id": tp.get("identifiants_rnb"), "parcel": idu,
              "parcel_m2": (P.get(idu) or {}).get("p", {}).get("contenance") if idu else None,
              "area_m2": round(B[main]["area"]), "height_m": tp.get("hauteur"), "floors": tp.get("nombre_d_etages"),
              "dwellings": dwellings, "year": int(year) if year.isdigit() else None, "usage": tp.get("usage_1"),
              "buildings": len(prop)}
    geometry = _collection(proj, P.get(idu), [B[main]["geom"]], [B[k]["geom"] for k in prop if k != main],
                           shown, walls)
    return {"v": V, "status": "ok", "type": typ, "usable": not any(w in BLOCKING for w in warnings),
            "warnings": warnings, "target": target, "neighbours": neighbours, "living_walls": living,
            "annex_links": links_n, "orientation": {"facade_bearing": facade, "estimated": True} if facade is not None
            else None, "nearest_m": round(nearest, 1) if nearest is not None else None, "geometry": geometry}


def _facade(centre, parcel, others) -> int | None:
    """Direction de la façade sur rue, du centre du bâtiment vers :
    - le point de la limite de parcelle sur la voie publique (aucune autre parcelle au contact) le plus proche
      du point d'adresse ; la voirie n'est pas cadastrée, et le point d'adresse départage une parcelle d'angle ;
    - à défaut, le point d'adresse lui-même (origine de la projection), s'il n'est pas au centre du bâtiment."""
    if parcel is not None:
        near = [g for g in others if g.distance(parcel["geom"]) <= 1.0]
        free = parcel["geom"].boundary.difference(unary_union(near).buffer(1.0)) if near else parcel["geom"].boundary
        if free.length >= MIN_CONTACT_M:
            p = nearest_points(free, Point(0, 0))[0]
            if math.hypot(p.x - centre.x, p.y - centre.y) >= FACADE_MIN_M:
                return _bearing(p.x - centre.x, p.y - centre.y)
    if math.hypot(centre.x, centre.y) >= FACADE_MIN_M:
        return _bearing(-centre.x, -centre.y)
    return None


def _axis(g) -> float | None:
    """Direction dominante d'un mur (0-180°, moyenne des segments pondérée par leur longueur)."""
    c = s = 0.0
    for ln in _lines(g):
        d = np.diff(np.asarray(ln.coords)[:, :2], axis=0)
        length, th = np.hypot(d[:, 0], d[:, 1]), np.arctan2(d[:, 0], d[:, 1])
        c, s = c + float((length * np.cos(2 * th)).sum()), s + float((length * np.sin(2 * th)).sum())
    return None if c == s == 0 else (math.degrees(math.atan2(s, c)) / 2) % 180


def _side(wall, centre, facade: int | None) -> str:
    """Côté vu de la rue. Un mur orienté comme la direction de la façade (de la rue vers le fond) est à gauche
    ou à droite selon sa position ; sinon il est à l'avant ou à l'arrière."""
    if facade is None:
        return "unknown"
    f = math.radians(facade)
    c = wall.centroid
    dx, dy = c.x - centre.x, c.y - centre.y
    along, lateral = dx * math.sin(f) + dy * math.cos(f), dx * math.cos(f) - dy * math.sin(f)
    ax = _axis(wall)
    if ax is not None and abs((ax - facade + 90) % 180 - 90) <= 45:
        return "left" if lateral > 0 else "right"
    return "front" if along > 0 else "rear"


def _collection(proj, parcel, target, prop, neighbours, walls) -> dict:
    feats = []
    if parcel:
        feats.append(("parcel", parcel["geom"], {}))
    feats += [("target", g, {}) for g in target] + [("property", g, {}) for g in prop]
    feats += [("neighbour_main" if k == "main" else "neighbour_annex", g, {}) for g, k in neighbours]
    feats += [("shared_wall", g, {"kind": k}) for g, k in walls]
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"role": role, **extra}, "geometry": proj.geojson(g)}
                         for role, g, extra in feats if not g.is_empty]}


# ---------- calcul complet, éligibilité, étape du collecteur ----------
def _now():
    return datetime.now(timezone.utc)


def compute(client, lon: float | None, lat: float | None, address: str | None = None,
            confidence: str | None = None) -> dict:
    """fetch + analyse ; un échec (réseau ou données) donne `status: error` au lieu d'une exception."""
    now = _now()
    inp = {"address": address, "lat": lat, "lon": lon, "confidence": confidence}
    try:
        ctx = fetch(client, (lon, lat) if lon is not None and lat is not None else None, address)
        ctx["confidence"] = confidence
        res = analyse(ctx)
    except Exception as e:                       # noqa: BLE001 — un calcul raté ne doit pas casser l'appelant
        if not isinstance(e, FetchError):
            log.warning("mitoyenneté %s: %r", address or (lon, lat), e)
        return {"v": V, "status": "error", "error": str(e)[:300] or type(e).__name__, "tried_at": now.isoformat(),
                "input": inp, **({"not_found": True} if isinstance(e, AddressNotFound) else {})}
    inp["ban_id"] = (ctx.get("ban") or {}).get("id")
    if inp["lon"] is None:                       # adresse seule : point du numéro trouvé par la BAN
        inp["lon"], inp["lat"] = ctx["lon"], ctx["lat"]
    return {**res, "computed_at": now.isoformat(), "input": inp}


def address_confidence(enr: dict) -> str | None:
    """Confiance d'une adresse au numéro : user | exact | probable | source (adresse de la source géocodée)."""
    if enr.get("address_method") == "user":
        return "user"
    c = enr.get("address_confidence")
    if c in ("exact", "probable"):
        return c
    return "source" if c is None else None           # « street », « none » : pas de calcul


def wanted_input(row: dict) -> tuple[dict | None, str | None]:
    """Entrée du calcul pour une annonce principale (position fusionnée comprise), ou (None, raison)."""
    view = merged_columns(row)
    from_fill = view.get("lat") != row.get("lat") or view.get("geo_precision") != row.get("geo_precision")
    enr = (row.get("fill_enrichment") if from_fill else row.get("enrichment")) or {}
    if view.get("lat") is None or view.get("geo_precision") != "exact":
        return None, "imprecise_position"
    if not view.get("address"):
        return None, "no_address"
    conf = address_confidence(enr)
    if conf is None:
        return None, "low_confidence"
    return {"address": view["address"], "lat": round(float(view["lat"]), 6), "lon": round(float(view["lon"]), 6),
            "confidence": conf}, None


def _same(inp: dict | None, want: dict) -> bool:
    inp = inp or {}
    return all(inp.get(k) == want[k] for k in ("address", "lat", "lon", "confidence"))


def _ts(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


SELECT_SQL = """
SELECT l.id, l.address, l.lat, l.lon, l.geo_precision, l.enrichment, l.cluster, l.has_garage,
       l.adjacency - 'geometry' AS adjacency,
       (SELECT m.enrichment FROM listing m WHERE m.merged_into = l.id
          AND m.lat = (l.cluster->'fill'->>'lat')::float8 AND m.lon = (l.cluster->'fill'->>'lon')::float8
        ORDER BY m.id LIMIT 1) AS fill_enrichment
FROM listing l
WHERE l.merged_into IS NULL AND (l.status = 'active' OR coalesce((l.cluster->>'active')::boolean, false))
ORDER BY l.enriched_at DESC NULLS LAST, l.first_seen DESC, l.id DESC
"""


def due(conn, now=None) -> tuple[list[tuple[dict, dict]], list[tuple[int, str]]]:
    """(annonces à calculer avec leur entrée, dans l'ordre de traitement ; annonces à marquer non applicables)."""
    now = now or _now()
    todo, na = [], []
    for r in conn.execute(SELECT_SQL).fetchall():
        cur = r["adjacency"] or {}
        want, why = wanted_input(r)
        if want is None:
            if cur.get("status") != "not_applicable" or cur.get("reason") != why:
                na.append((r["id"], why))
            continue
        if _same(cur.get("input"), want):
            if cur.get("status") != "error":
                continue
            tried = _ts(cur.get("tried_at"))
            if tried and now - tried < RETRY_ERROR_AFTER:
                continue
        todo.append((r, want))
    return todo, na


def conflict_request(row: dict, res: dict) -> dict | None:
    """Demande de vérification « conflict » (design D13) : la carte, à adresse exacte ou validée, contredit le
    verdict de l'agent sur une annonce dont le garage est confirmé. Rien si l'utilisateur a fixé le type ou si
    une demande attend déjà."""
    if res.get("status") != "ok" or not res.get("usable") or res.get("type") not in TYPES:
        return None
    if (res.get("input") or {}).get("confidence") == "probable":
        return None
    enr = row.get("enrichment") or {}
    if enr.get("type_request") or enr.get("property_type_user"):
        return None
    agent = enr.get("property_type")
    if agent not in TYPES:
        agent = (((row.get("cluster") or {}).get("fill") or {}).get("agent_type") or {}).get("type")
    if agent not in TYPES or agent == res["type"]:
        return None
    garage = enr.get("has_garage") if enr.get("has_garage") is not None else merged_columns(row).get("has_garage")
    if garage is not True:
        return None
    return {"requested_at": _now().isoformat(), "reason": "conflict", "note": f"carte : {res['type']}, agent : {agent}",
            "adjacency_at": res.get("computed_at")}


def process(conn, client, limit: int = LIMIT_PER_CYCLE) -> dict:
    """Étape du collecteur (design D5) : au plus `limit` calculs par cycle, un commit par annonce."""
    todo, na = due(conn)
    now = _now().isoformat()
    for lid, why in na:
        conn.execute("UPDATE listing SET adjacency=%s WHERE id=%s",
                     (Jsonb({"v": V, "status": "not_applicable", "reason": why, "computed_at": now}), lid))
    conn.commit()
    st = {"computed": 0, "errors": 0, "pending": max(0, len(todo) - limit), "not_applicable": len(na)}
    for r, want in todo[:limit]:
        res = compute(client, want["lon"], want["lat"], want["address"], want["confidence"])
        req = conflict_request(r, res)
        if res["status"] == "ok":
            res["type_request_created"] = bool(req)     # une seule demande par calcul
        conn.execute("UPDATE listing SET adjacency=%s WHERE id=%s", (Jsonb(res), r["id"]))
        if req:
            conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s",
                         (Jsonb({"type_request": req}), r["id"]))
        conn.commit()
        st["errors" if res["status"] == "error" else "computed"] += 1
    return st
