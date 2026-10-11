"""Rattrape tout l'arriéré de la boîte d'alertes (au lieu de 40 messages par cycle), puis post-traite et re-score."""
import collections
import httpx
from app import db, settings
from app.collector import postprocess, rescore
from app.ingest import upsert
from app.sources import email_alerts

email_alerts.MAX_MESSAGES_PER_RUN = 1000
crit = settings.load_criteria()
with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client, db.connect() as conn:
    items = email_alerts.fetch(client, crit)
    created, ids = collections.Counter(), []
    for it in items:
        lid, new = upsert(conn, it)
        created[(it["source"], new)] += 1
        ids.append(lid)
    conn.commit()
    for lid in dict.fromkeys(ids):
        try:
            postprocess(conn, client, crit, lid)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print("postprocess", lid, e)
    rescore(conn, client, crit)
    conn.commit()
    rows = conn.execute("SELECT source, count(*) n, count(*) FILTER (WHERE excluded_reason IS NULL) visibles, "
                        "count(*) FILTER (WHERE excluded_reason='à compléter') a_completer FROM listing "
                        "WHERE id = ANY(%s) GROUP BY 1", (list(set(ids)),)).fetchall()
print(f"{len(items)} liens d'annonces extraits ;", dict(created))
for r in rows:
    print(dict(r))
