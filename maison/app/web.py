from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from psycopg.types.json import Jsonb
from app import db, settings
from app.collector import postprocess, rescore

app = FastAPI(title="Maison")
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
(settings.DATA_DIR / "photos").mkdir(parents=True, exist_ok=True)
app.mount("/photos", StaticFiles(directory=settings.DATA_DIR / "photos"), name="photos")

COLS = ("id, source, url, title, price, surface_m2, land_m2, rooms, bedrooms, property_type, city, "
        "postal_code, dpe, ges, year_built, has_garage, has_garden, has_bathtub, works, agency, lat, lon, "
        "geo_precision, address, photos, photo_urls, first_seen, last_seen, published_at, status, "
        "price_history, commute, enrichment, enrichment_status, score, score_details, excluded_reason, "
        "fingerprint, alerted_at, user_status, user_note")
# Champs que l'agent d'enrichissement peut écrire directement dans les colonnes (liste blanche)
DIRECT = ("address", "lat", "lon", "city", "postal_code", "description", "title", "surface_m2",
          "land_m2", "bedrooms", "rooms", "dpe", "price")


def _auth(token):
    if not settings.ENRICH_TOKEN or token != settings.ENRICH_TOKEN:
        raise HTTPException(401)


@app.on_event("startup")
def startup():
    db.init_schema()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/criteria")
def criteria():
    c = settings.load_criteria()
    return {k: c[k] for k in ("price", "preferred_cities", "other_cities", "commute", "alert")}


@app.get("/api/listings")
def listings(show_excluded: int = 0, include_gone: int = 0):
    where = ["TRUE"]
    if not show_excluded:
        where.append("(excluded_reason IS NULL OR excluded_reason = 'à compléter')")
    if not include_gone:
        where.append("status = 'active'")
    with db.connect() as conn:
        return conn.execute(f"SELECT {COLS} FROM listing WHERE {' AND '.join(where)} "
                            "ORDER BY score DESC NULLS LAST, first_seen DESC").fetchall()


@app.get("/api/listings/{lid}")
def listing(lid: int):
    with db.connect() as conn:
        row = conn.execute(f"SELECT {COLS}, description FROM listing WHERE id=%s", (lid,)).fetchone()
    if not row:
        raise HTTPException(404)
    return row


@app.post("/api/listings/{lid}/user")
def set_user(lid: int, body: dict):
    with db.connect() as conn:
        conn.execute("UPDATE listing SET user_status=%s, user_note=COALESCE(%s, user_note) WHERE id=%s",
                     (body.get("user_status"), body.get("user_note"), lid))
    return {"ok": True}


@app.get("/api/enrichment/pending")
def pending(limit: int = 5, x_token: str = Header(None)):
    _auth(x_token)
    with db.connect() as conn:
        return conn.execute(
            f"SELECT {COLS}, description FROM listing WHERE status='active' AND enrichment_status='pending' "
            "AND (excluded_reason IS NULL OR excluded_reason='à compléter') "
            "ORDER BY score DESC NULLS LAST, first_seen DESC LIMIT %s", (limit,)).fetchall()


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


@app.post("/api/listings/{lid}/enrichment")
def enrich(lid: int, body: dict, x_token: str = Header(None)):
    _auth(x_token)
    status = "failed" if body.pop("_failed", False) else "done"
    direct = {k: body.pop(k) for k in DIRECT if body.get(k) is not None}
    photo_urls = body.pop("photo_urls", None)
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM listing WHERE id=%s", (lid,)).fetchone():
            raise HTTPException(404)
        conn.execute("UPDATE listing SET enrichment = enrichment || %s, enrichment_status=%s, "
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
        with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
            rescore(conn, client, crit, ids=[lid])
            try:
                postprocess(conn, client, crit, lid)
            except Exception:
                pass
            rescore(conn, client, crit, ids=[lid])
        # rescore ne doit pas faire repasser en pending une annonce qu'on vient d'enrichir
        conn.execute("UPDATE listing SET enrichment_status=%s WHERE id=%s AND enrichment_status='pending'",
                     (status, lid))
        return conn.execute("SELECT id, score, excluded_reason, enrichment_status FROM listing WHERE id=%s",
                            (lid,)).fetchone()
