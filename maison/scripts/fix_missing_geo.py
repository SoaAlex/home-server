"""Rattrapage : géolocalise (centre de la ville) + trajets pour les annonces actives sans position, puis re-score."""
import httpx
from app import db, settings
from app.collector import postprocess, rescore

crit = settings.load_criteria()
with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client, db.connect() as conn:
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM listing WHERE status='active' AND lat IS NULL AND city IS NOT NULL").fetchall()]
    for lid in ids:
        postprocess(conn, client, crit, lid)
        conn.commit()
    rescore(conn, client, crit)
    conn.commit()
    left = conn.execute("SELECT count(*) n FROM listing WHERE status='active' AND lat IS NULL").fetchone()["n"]
print(f"{len(ids)} annonces traitées, {left} encore sans position")
