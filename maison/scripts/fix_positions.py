"""Répare les positions écrasées par la position floutée du portail (bug corrigé dans ingest.upsert) :
re-géocode via la BAN les annonces dont l'adresse est connue (probable/exact) mais geo_precision != exact."""
import time
import httpx
from app import db, geo, settings

with db.connect() as conn, httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
    rows = conn.execute(
        "SELECT id, address FROM listing WHERE address IS NOT NULL AND geo_precision IS DISTINCT FROM 'exact' "
        "AND enrichment->>'address_confidence' IN ('probable','exact')").fetchall()
    fixed = 0
    for r in rows:
        try:
            g = geo.geocode(client, r["address"])
        except Exception:
            g = None
        if g and g["type"] in ("housenumber", "street"):
            conn.execute("UPDATE listing SET lat=%s, lon=%s, geo_precision='exact' WHERE id=%s",
                         (g["lat"], g["lon"], r["id"]))
            fixed += 1
        time.sleep(0.1)
    conn.commit()
    print(f"{fixed}/{len(rows)} positions restaurées")
