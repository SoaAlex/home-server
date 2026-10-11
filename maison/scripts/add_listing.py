"""Ajoute (ou met à jour) une annonce à partir de son URL, même hors budget : `python scripts/add_listing.py URL`.

Sites pris en charge : ceux de agences.DETAIL_FETCHERS. L'annonce est marquée `user_added` (pas exclue pour
« prix > max ») puis suivie par le collecteur horaire comme les autres (prix, disparition).
"""
import sys
from urllib.parse import urlparse
import httpx
from psycopg.types.json import Jsonb
from app import db, settings
from app.collector import postprocess, rescore
from app.ingest import upsert
from app.sources import agences


def main(url: str):
    host = urlparse(url).netloc.removeprefix("www.")
    fetcher = agences.DETAIL_FETCHERS.get(host)
    if not fetcher:
        sys.exit(f"site non pris en charge : {host} (connus : {', '.join(agences.DETAIL_FETCHERS)})")
    crit = settings.load_criteria()
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30, follow_redirects=True) as client, \
            db.connect() as conn:
        item = fetcher(client, url, agences.zone_index(crit))
        if not item:
            sys.exit("ville hors des zones de recherche (config/criteria.yaml)")
        lid, created = upsert(conn, item)
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s", (Jsonb({"user_added": True}), lid))
        conn.commit()
        postprocess(conn, client, crit, lid)
        rescore(conn, client, crit)
        conn.commit()
        row = conn.execute("SELECT id, score, excluded_reason, price, city FROM listing WHERE id=%s", (lid,)).fetchone()
    print(("créée" if created else "mise à jour"), dict(row))


if __name__ == "__main__":
    main(sys.argv[1])
