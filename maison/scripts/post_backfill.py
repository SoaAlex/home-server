"""One-off après le backfill : nettoie les titres Maes, recalcule les scores sans alerter,
puis marque comme « déjà alertées » les annonces existantes qui auraient déclenché une alerte."""
import html
import os
import httpx
os.environ["DISABLE_ALERTS"] = "1"
from app import db, settings, alerts
from app.collector import rescore

crit = settings.load_criteria()
with db.connect() as conn, httpx.Client() as client:
    for r in conn.execute("SELECT id, title FROM listing WHERE title LIKE '%&#%'").fetchall():
        conn.execute("UPDATE listing SET title=%s WHERE id=%s", (html.unescape(r["title"]), r["id"]))
    rescore(conn, client, crit)
    n = 0
    for row in conn.execute("SELECT * FROM listing WHERE status='active'").fetchall():
        if alerts.should_alert(row, crit):
            conn.execute("UPDATE listing SET alerted_at=now() WHERE id=%s", (row["id"],))
            n += 1
    print("pré-marquées comme alertées :", n)
