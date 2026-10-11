"""Recalcule `works` depuis le texte stocké (après amélioration de textparse.detect_works). Affiche les changements."""
import collections
from app import db
from app.textparse import detect_works

with db.connect() as conn:
    rows = conn.execute("SELECT id, works, title, description FROM listing WHERE status='active'").fetchall()
    changes = collections.Counter()
    for r in rows:
        new = detect_works(f"{r['title'] or ''} {r['description'] or ''}")
        if new != r["works"] and (new is not None or r["works"] == "heavy"):
            conn.execute("UPDATE listing SET works=%s WHERE id=%s", (new, r["id"]))
            changes[(r["works"], new)] += 1
    conn.commit()
for (old, new), n in changes.most_common():
    print(f"{old} -> {new} : {n}")
