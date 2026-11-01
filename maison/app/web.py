from datetime import datetime, timezone
import json
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from psycopg.types.json import Jsonb
from app import db, dedupe, geo, settings
from app.collector import postprocess, rescore
from app.scoring import effective, merged_columns

app = FastAPI(title="Maison")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
(settings.DATA_DIR / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=settings.DATA_DIR / "photos"), name="photos")

COLS = ("id, source, url, title, price, surface_m2, land_m2, rooms, bedrooms, property_type, city, "
        "postal_code, dpe, ges, year_built, has_garage, has_garden, has_bathtub, works, agency, lat, lon, "
        "geo_precision, address, photos, photo_urls, first_seen, last_seen, published_at, status, "
        "price_history, commute, enrichment, enrichment_status, score, score_details, excluded_reason, "
        "fingerprint, alerted_at, user_status, user_note, reaction, seen_at, merged_into, cluster")
# Champs que l'agent d'enrichissement peut écrire directement dans les colonnes (liste blanche)
DIRECT = ("address", "lat", "lon", "city", "postal_code", "description", "title", "surface_m2",
          "land_m2", "bedrooms", "rooms", "dpe", "price")


def _auth(token):
    if not settings.ENRICH_TOKEN or token != settings.ENRICH_TOKEN:
        raise HTTPException(401)


@app.on_event("startup")
def startup():
    # Le collecteur applique aussi le schéma à son démarrage : ici on n'attend pas derrière ses transactions.
    db.init_schema(lock_timeout="500ms")   # < deadlock_timeout (1 s) : le web cède toujours en premier


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/criteria")
def criteria():
    c = settings.load_criteria()
    return {k: c[k] for k in ("price", "preferred_cities", "other_cities", "commute", "alert")}


# Doublons possibles en attente, comptés par annonce principale (une paire peut relier deux secondaires).
DUP_PENDING_CTE = """
WITH pend AS (
  SELECT coalesce(a.merged_into, a.id) AS pa, coalesce(b.merged_into, b.id) AS pb
  FROM duplicate_pair p JOIN listing a ON a.id = p.id_a JOIN listing b ON b.id = p.id_b
  WHERE p.status = 'suggested'),
dup AS (
  SELECT x AS dup_lid, count(*) AS dup_n FROM (
    SELECT pa AS x FROM pend WHERE pa <> pb UNION ALL SELECT pb FROM pend WHERE pa <> pb) t GROUP BY x)
"""
ACTIVE_SQL = "(status = 'active' OR coalesce((cluster->>'active')::boolean, false))"


def _view(row: dict) -> dict:
    """Annonce principale telle qu'affichée : colonnes vides complétées par les autres sources du bien."""
    return merged_columns(row) if row.get("cluster") else row


@app.get("/api/listings")
def listings(show_excluded: int = 0, include_gone: int = 0, dups: int = 0):
    where = ["merged_into IS NULL"]           # un bien = une annonce (les sources fusionnées sont dans `cluster`)
    if not show_excluded:
        where.append("(excluded_reason IS NULL OR excluded_reason = 'à compléter')")
    if not include_gone:
        where.append(ACTIVE_SQL)
    if dups:
        where.append("dup_n > 0")
    with db.connect() as conn:
        rows = conn.execute(f"{DUP_PENDING_CTE} SELECT {COLS}, coalesce(dup_n, 0) AS dup_pending FROM listing "
                            f"LEFT JOIN dup ON dup_lid = listing.id WHERE {' AND '.join(where)} "
                            "ORDER BY score DESC NULLS LAST, first_seen DESC").fetchall()
    return [_view(r) for r in rows]


SUMMARY_COLS = ("id, source, url, title, price, surface_m2, land_m2, rooms, bedrooms, city, postal_code, address, "
                "agency, status, score, excluded_reason, reaction, photos, photo_urls, enrichment, cluster, "
                "first_seen, last_seen, merged_into, geo_precision, lat, lon, dpe")


def _summary(row: dict) -> dict:
    eff = effective(row)
    cl = row.get("cluster") or {}
    pics = cl.get("photos") or ([f"/photos/{p}" for p in row["photos"]] if row["photos"] else row["photo_urls"] or [])
    return {"id": row["id"], "source": row["source"], "url": row["url"], "title": eff.get("title"),
            "price": eff.get("price"), "surface_m2": eff.get("surface_m2"), "land_m2": eff.get("land_m2"),
            "rooms": eff.get("rooms"), "bedrooms": eff.get("bedrooms"), "city": eff.get("city"),
            "address": eff.get("address"), "agency": row["agency"], "status": row["status"], "score": row["score"],
            "excluded_reason": row["excluded_reason"], "reaction": row["reaction"], "dpe": eff.get("dpe"),
            "sources": cl.get("sources") or [row["source"]], "photos": pics[:4]}


def _suggestions(conn, group: list[int]) -> list[dict]:
    """Doublons possibles du bien : l'autre annonce (principale de son groupe), preuves de la meilleure paire."""
    out = {}
    for p in conn.execute("SELECT id_a, id_b, evidence, created_at FROM duplicate_pair WHERE status='suggested' "
                          "AND (id_a = ANY(%s) OR id_b = ANY(%s)) ORDER BY created_at", (group, group)).fetchall():
        other = p["id_b"] if p["id_a"] in group else p["id_a"]
        o = conn.execute(f"SELECT {SUMMARY_COLS} FROM listing WHERE id = (SELECT coalesce(merged_into, id) "
                         "FROM listing WHERE id=%s)", (other,)).fetchone()
        if not o or o["id"] in group:
            continue
        ev = p["evidence"] or {}
        best = out.get(o["id"])
        if not best or (ev.get("photos") or 0) > (best["evidence"].get("photos") or 0):
            out[o["id"]] = {"other_id": o["id"], "pair": [p["id_a"], p["id_b"]], "evidence": ev,
                            "created_at": p["created_at"], "other": _summary(o)}
    return list(out.values())


@app.get("/api/listings/{lid}")
def listing(lid: int):
    with db.connect() as conn:
        row = conn.execute(f"SELECT {COLS}, description, raw->'blurInfo' AS blur FROM listing WHERE id=%s", (lid,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["merged_into"]:                # source secondaire : l'UI ouvre la fiche principale
            return row
        members = conn.execute(f"SELECT {SUMMARY_COLS} FROM listing WHERE merged_into=%s ORDER BY id", (lid,)).fetchall()
        group = [lid] + [m["id"] for m in members]
        return {**_view(row), "members": [_summary(m) for m in members], "suggestions": _suggestions(conn, group)}


def _rescore(conn, ids, refresh_ids=()):
    """Recalcule les scores après une action sur un groupe (postprocess des annonces redevenues seules)."""
    crit = settings.load_criteria()
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
        for i in refresh_ids:
            try:
                postprocess(conn, client, crit, i)
            except Exception:
                pass
        rescore(conn, client, crit, ids=list(ids))


@app.post("/api/listings/{lid}/merge")
def merge_listing(lid: int, body: dict):
    """Fusion manuelle (doublon possible validé, ou deux annonces désignées) ; lève un refus antérieur."""
    other = body.get("other_id")
    if not isinstance(other, int) or other == lid:
        raise HTTPException(400, "other_id = identifiant d'une autre annonce")
    with db.connect() as conn:
        try:
            keep = dedupe.merge(conn, lid, other, method="user")
        except LookupError:
            raise HTTPException(404)
        _rescore(conn, [keep])
    return {"principal": keep}


@app.post("/api/listings/{lid}/split")
def split_listing(lid: int, body: dict):
    """Sépare une source fusionnée : elle redevient une annonce seule, et la refusion est bloquée."""
    member = body.get("member_id")
    with db.connect() as conn:
        try:
            principal = dedupe.principal_of(conn, lid)
            dedupe.split(conn, principal, member)
        except LookupError as e:
            raise HTTPException(404, str(e))
        _rescore(conn, [principal, member], refresh_ids=[member])
    return {"principal": principal, "member": member}


@app.post("/api/listings/{lid}/not_duplicate")
def not_duplicate(lid: int, body: dict):
    """« Pas un doublon » : la suggestion disparaît et ne sera plus proposée."""
    other = body.get("other_id")
    if not isinstance(other, int) or other == lid:
        raise HTTPException(400, "other_id = identifiant d'une autre annonce")
    with db.connect() as conn:
        try:
            dedupe.reject(conn, lid, other)
        except LookupError:
            raise HTTPException(404)
    return {"ok": True}


@app.post("/api/listings/{lid}/user")
def set_user(lid: int, body: dict):
    with db.connect() as conn:
        conn.execute("UPDATE listing SET user_status=%s, user_note=COALESCE(%s, user_note) WHERE id=%s",
                     (body.get("user_status"), body.get("user_note"), lid))
    return {"ok": True}


@app.post("/api/listings/{lid}/seen")
def mark_seen(lid: int):
    """Appelé par l'UI à l'ouverture d'une fiche : retire le badge « Nouveau »."""
    with db.connect() as conn:
        row = conn.execute("UPDATE listing SET seen_at = COALESCE(seen_at, now()) WHERE id=%s RETURNING seen_at",
                           (lid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return {"id": lid, "seen_at": row["seen_at"]}


@app.post("/api/listings/{lid}/reaction")
def set_reaction(lid: int, body: dict):
    reaction = body.get("reaction")
    if reaction not in ("like", "dislike", None):
        raise HTTPException(400, "reaction = like | dislike | null")
    with db.connect() as conn:
        if not conn.execute("UPDATE listing SET reaction=%s WHERE id=%s RETURNING id", (reaction, lid)).fetchone():
            raise HTTPException(404)
    return {"id": lid, "reaction": reaction}


def _refresh(conn, lid):
    """Recalcule trajets + score après un changement de position."""
    crit = settings.load_criteria()
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
        try:
            postprocess(conn, client, crit, lid)
        except Exception:
            pass
        rescore(conn, client, crit, ids=[lid])


@app.post("/api/listings/{lid}/address")
def set_address(lid: int, body: dict):
    """Adresse corrigée à la main depuis l'UI : géocodée (BAN) et verrouillée contre l'agent."""
    address = (body.get("address") or "").strip()
    if len(address) < 5:
        raise HTTPException(400, "adresse vide")
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
        g = geo.geocode(client, address)
    if not g:
        raise HTTPException(422, "adresse introuvable dans la BAN")
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM listing WHERE id=%s", (lid,)).fetchone():
            raise HTTPException(404)
        conn.execute("UPDATE listing SET address=%s, lat=%s, lon=%s, geo_precision='exact', commute=NULL, "
                     "enrichment = (enrichment - 'address_request') || %s WHERE id=%s",
                     (g["label"], g["lat"], g["lon"],
                      Jsonb({"address_confidence": "exact", "address_method": "user"}), lid))
        _refresh(conn, lid)
        return conn.execute("SELECT id, address, lat, lon, commute, score FROM listing WHERE id=%s", (lid,)).fetchone()


@app.post("/api/listings/{lid}/address_request")
def request_address_investigation(lid: int, body: dict | None = None):
    """Demande à l'agent Hermes une recherche d'adresse approfondie (traitée en priorité)."""
    with db.connect() as conn:
        row = conn.execute("SELECT address, enrichment FROM listing WHERE id=%s", (lid,)).fetchone()
        if not row:
            raise HTTPException(404)
        req = {"requested_at": datetime.now(timezone.utc).isoformat(),
               "note": (body or {}).get("note") or "", "previous_address": row["address"]}
        conn.execute("UPDATE listing SET enrichment = enrichment || %s, enrichment_status='pending' WHERE id=%s",
                     (Jsonb({"address_request": req}), lid))
    return {"ok": True, "address_request": req}


@app.get("/api/enrichment/pending")
def pending(limit: int = 5, x_token: str = Header(None)):
    _auth(x_token)
    with db.connect() as conn:
        return conn.execute(
            f"SELECT {COLS}, description, raw->'blurInfo' AS blur FROM listing WHERE status='active' AND enrichment_status='pending' "
            "AND merged_into IS NULL "     # sources secondaires d'un bien fusionné : jamais proposées à l'agent
            "AND (excluded_reason IS NULL OR excluded_reason='à compléter') "
            "AND (reaction IS DISTINCT FROM 'dislike' OR enrichment ? 'address_request') "
            "ORDER BY (enrichment ? 'address_request') DESC, (first_seen > now() - interval '48 hours') DESC, score DESC NULLS LAST, first_seen DESC LIMIT %s",
            (limit,)).fetchall()


@app.get("/api/tools/ademe")
def tool_ademe(qs: str, size: int = 20, geo_distance: str | None = None, x_token: str = Header(None)):
    """Relais vers l'API DPE ADEME (le sandbox Hermes n'y a pas accès via son proxy).
    geo_distance = « lon,lat,rayon » (ex. 2.99,50.64,400m)."""
    _auth(x_token)
    sel = "adresse_ban,_geopoint,date_etablissement_dpe,surface_habitable_logement,etiquette_dpe,conso_5_usages_par_m2_ep"
    params = {"qs": qs, "size": min(size, 100), "select": sel}
    if geo_distance:
        params["geo_distance"] = geo_distance
    r = httpx.get("https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines",
                  params=params, timeout=30)
    try:
        return r.json()
    except ValueError:
        raise HTTPException(502, f"ADEME {r.status_code}: {r.text[:300]}")


@app.get("/api/tools/ban")
def tool_ban(q: str, citycode: str | None = None, limit: int = 5, x_token: str = Header(None)):
    """Relais vers la Base Adresse Nationale."""
    _auth(x_token)
    params = {"q": q, "limit": limit, **({"citycode": citycode} if citycode else {})}
    return httpx.get("https://api-adresse.data.gouv.fr/search/", params=params, timeout=30).json()


@app.get("/api/tools/reverse")
def tool_reverse(lon: float, lat: float, x_token: str = Header(None)):
    """Relais BAN reverse : adresse la plus proche d'un point."""
    _auth(x_token)
    return httpx.get("https://api-adresse.data.gouv.fr/reverse/", params={"lon": lon, "lat": lat, "limit": 3},
                     timeout=30).json()


@app.get("/api/tools/cadastre")
def tool_cadastre(lon: float, lat: float, radius_m: int = 300, min_m2: int = 0, max_m2: int = 10**7,
                  x_token: str = Header(None)):
    """Parcelles cadastrales (API Carto IGN) autour d'un point, filtrées par contenance (m²).
    Renvoie section, numéro, contenance et centroïde approximatif de chaque parcelle."""
    _auth(x_token)
    dlat, dlon = radius_m / 111_000, radius_m / 71_000
    poly = {"type": "Polygon", "coordinates": [[[lon - dlon, lat - dlat], [lon + dlon, lat - dlat],
                                                [lon + dlon, lat + dlat], [lon - dlon, lat + dlat],
                                                [lon - dlon, lat - dlat]]]}
    r = httpx.get("https://apicarto.ign.fr/api/cadastre/parcelle",
                  params={"geom": json.dumps(poly), "_limit": 1000}, timeout=60)
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        if not (min_m2 <= (p.get("contenance") or 0) <= max_m2):
            continue
        ring = f["geometry"]["coordinates"][0][0]
        out.append({"section": p.get("section"), "numero": p.get("numero"), "contenance": p.get("contenance"),
                    "lon": round(sum(x for x, _ in ring) / len(ring), 6),
                    "lat": round(sum(y for _, y in ring) / len(ring), 6)})
    return {"count": len(out), "parcels": out[:200]}


@app.post("/api/listings/{lid}/enrichment")
def enrich(lid: int, body: dict, x_token: str = Header(None)):
    _auth(x_token)
    status = "failed" if body.pop("_failed", False) else "done"
    photo_urls = body.pop("photo_urls", None)
    with db.connect() as conn:
        cur = conn.execute("SELECT enrichment FROM listing WHERE id=%s", (lid,)).fetchone()
        if not cur:
            raise HTTPException(404)
        old = cur["enrichment"] or {}
        if old.get("address_method") == "user":      # adresse validée à la main : l'agent n'y touche pas
            for k in ("address", "lat", "lon", "address_confidence", "address_method"):
                body.pop(k, None)
        direct = {k: body.pop(k) for k in DIRECT if body.get(k) is not None}
        if old.get("address_request"):              # garder une trace de l'investigation
            hist = old.get("address_investigations", []) + [{
                **old["address_request"], "done_at": datetime.now(timezone.utc).isoformat(),
                "result": direct.get("address"), "confidence": body.get("address_confidence"),
                "notes": body.get("address_notes")}]
            body["address_investigations"] = hist
        conn.execute("UPDATE listing SET enrichment = (enrichment - 'address_request') || %s, enrichment_status=%s, "
                     "enriched_at=now() WHERE id=%s", (Jsonb(body), status, lid))
        for k, v in direct.items():          # k vient de la liste blanche DIRECT
            conn.execute(f"UPDATE listing SET {k}=%s WHERE id=%s", (v, lid))
        if "lat" in direct and "lon" in direct:
            prec = "exact" if body.get("address_confidence") in ("exact", "probable") else "approx"
            conn.execute("UPDATE listing SET geo_precision=%s, commute=NULL WHERE id=%s", (prec, lid))
        if "city" in direct:
            conn.execute("UPDATE listing SET insee=NULL WHERE id=%s AND lat IS NULL", (lid,))
        if photo_urls:
            conn.execute("UPDATE listing SET photo_urls=%s, photos='[]' WHERE id=%s", (Jsonb(photo_urls), lid))
        crit = settings.load_criteria()
        principal = conn.execute("SELECT merged_into FROM listing WHERE id=%s", (lid,)).fetchone()["merged_into"]
        with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
            rescore(conn, client, crit, ids=[lid])
            try:
                postprocess(conn, client, crit, lid)
            except Exception:
                pass
            if principal:   # annonce fusionnée entre-temps : enrichissement gardé sur elle, groupe recalculé
                dedupe.refresh_cluster(conn, principal)
                rescore(conn, client, crit, ids=[principal])
            rescore(conn, client, crit, ids=[lid])
        # rescore ne doit pas faire repasser en pending une annonce qu'on vient d'enrichir
        conn.execute("UPDATE listing SET enrichment_status=%s WHERE id=%s AND enrichment_status='pending'",
                     (status, lid))
        return conn.execute("SELECT id, score, excluded_reason, enrichment_status FROM listing WHERE id=%s",
                            (lid,)).fetchone()
