#!/usr/bin/env python3
"""Analyse initiale des doublons (change OpenSpec merge-duplicate-listings, design D13).

À blanc par défaut : hache les photos locales (cache `listing_photo`), compare tout le périmètre (annonces
actives + disparues depuis ≤ 90 jours) et écrit un rapport HTML autonome (vignettes en base64). Aucune
annonce n'est modifiée.

    docker compose run --rm collector python scripts/dedupe_backfill.py [--report PATH] [--workers 4]
    docker compose run --rm collector python scripts/dedupe_backfill.py --apply

--apply : télécharge les vignettes des cartes « à compléter », applique fusions et suggestions sous verrou,
rafraîchit les groupes et recalcule les scores SANS alerte Discord ; le résumé liste les biens devenus
alertables (ils seront signalés au cycle suivant du collecteur). Relançable : une seconde application
ne crée rien de nouveau.
"""
import argparse
import base64
import html
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from PIL import Image  # noqa: E402

from app import alerts, db, dedupe, photos, settings  # noqa: E402


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def awaiting_cards(conn):
    """Cartes « à compléter » du périmètre avec une vignette jamais téléchargée."""
    return conn.execute(f"SELECT id, photo_urls FROM listing WHERE excluded_reason = 'à compléter' AND {dedupe.SCOPE_SQL} "
                        "AND jsonb_array_length(photos) = 0 AND jsonb_array_length(photo_urls) > 0").fetchall()


def thumbnails(conn, apply: bool):
    """Vignettes des cartes à compléter : en mémoire à blanc, sur disque + empreintes avec --apply."""
    extra, n = {}, 0
    rows = awaiting_cards(conn)
    if not rows:
        return extra, 0
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
        for r in rows:
            if apply:
                hashes = {}
                paths = photos.download(client, r["id"], r["photo_urls"], dedupe.photo_dir(), limit=10, hashes=hashes)
                for idx, h in hashes.items():
                    f = dedupe.photo_dir() / f"{r['id']}/{idx:02d}.jpg"
                    st = f.stat()
                    dedupe.store_hash(conn, r["id"], idx, f"{r['id']}/{idx:02d}.jpg", h, st.st_size, st.st_mtime)
                conn.execute("UPDATE listing SET photos=%s WHERE id=%s", (Jsonb(paths), r["id"]))
                n += len(hashes)
            else:
                for idx, u in enumerate(r["photo_urls"][:10]):
                    try:
                        resp = client.get(u, follow_redirects=True)
                        if resp.status_code == 200 and resp.content:
                            ph, dh, std = dedupe.image_hashes(resp.content)
                            extra.setdefault(r["id"], []).append((idx, ph, dh, std))
                            n += 1
                    except Exception:
                        continue
    return extra, n


# ---------- rapport HTML ----------
def thumb(rel: str, size=150) -> str:
    try:
        im = Image.open(dedupe.photo_dir() / rel)
        im.draft("RGB", (size * 2, size * 2))
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=62)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def fmt(v, unit=""):
    if v is None:
        return "?"
    return f"{round(v):,}".replace(",", " ") + unit


def card(it: dict, n=2) -> str:
    imgs = "".join(f'<img src="{src}">' for rel in it["photos"][:n] if (src := thumb(rel)))
    return (f'<div class="l"><div class="pics">{imgs or "<i>sans photo locale</i>"}</div>'
            f'<b>#{it["id"]}</b> {html.escape(it["source"])} · <b>{fmt(it["price"], " €")}</b><br>'
            f'{fmt(it["surface"], " m²")} · terrain {fmt(it["land"], " m²")} · {fmt(it["rooms"])} p. · '
            f'{fmt(it["bedrooms"])} ch.<br>{html.escape(it["city"] or "?")} · {html.escape(it["status"] or "")}<br>'
            f'<span class="t">{html.escape((it["title"] or "")[:70])}</span><br>'
            f'<a href="{html.escape(it["url"] or "")}" target="_blank">annonce ↗</a></div>')


def evidence(a, b, ev) -> str:
    bits = [ev.get("rule", ""), f'{ev.get("photos", 0)} photo(s) commune(s)']
    if ev.get("price"):
        bits.append(f'prix {fmt(ev["price"][0])} / {fmt(ev["price"][1])} €')
    if ev.get("surface"):
        bits.append(f'surface {fmt(ev["surface"][0])} / {fmt(ev["surface"][1])} m²')
    if ev.get("distance_km") is not None:
        bits.append(f'{ev["distance_km"]} km')
    if ev.get("text") is not None:
        bits.append(f'texte {round(ev["text"] * 100)} %')
    return f'<div class="ev">#{a} ↔ #{b} : ' + html.escape(" · ".join(x for x in bits if x)) + "</div>"


def planned_groups(res) -> list[tuple[list[int], list]]:
    """Composantes des fusions prévues (+ groupes existants touchés), avec leurs arêtes."""
    items = res["items"]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, it in items.items():
        if it["group"] != i:
            parent[find(i)] = find(it["group"])
    edges = [(a, b, ev) for op, a, b, ev in res["ops"] if op == "merge"]
    for a, b, _ in edges:
        parent[find(a)] = find(b)
    comps = {}
    for a, b, ev in edges:
        comps.setdefault(find(a), {"ids": set(), "edges": []})["edges"].append((a, b, ev))
    for i in list(parent):
        r = find(i)
        if r in comps:
            comps[r]["ids"].add(i)
    return [(sorted(c["ids"]), c["edges"]) for c in comps.values()]


CSS = """body{font:13px/1.4 system-ui,sans-serif;margin:20px;color:#18202a;background:#f5f6f8}
h1{font-size:20px}h2{margin-top:32px;border-bottom:2px solid #2f6f5e;padding-bottom:4px}
.g{background:#fff;border:1px solid #e3e6eb;border-radius:10px;padding:10px;margin:12px 0}
.row{display:flex;flex-wrap:wrap;gap:10px}.l{width:330px;border:1px solid #eee;border-radius:8px;padding:6px}
.pics{display:flex;gap:4px;margin-bottom:4px}.pics img{flex:1 1 0;min-width:0;max-width:150px;height:105px;object-fit:cover;border-radius:4px}
.ev{color:#555;margin-top:4px}.t{color:#555}.stats td{padding:2px 10px}.gen img{width:110px;margin:3px;border:2px solid #d64545}
"""


def report(path: Path, res, stats: dict, apply: bool):
    items = res["items"]
    groups = planned_groups(res)
    sugg = [(a, b, ev) for op, a, b, ev in res["ops"] if op == "suggest"]
    out = [f"<!doctype html><meta charset='utf-8'><title>Doublons — {datetime.now():%d/%m/%Y %H:%M}</title>",
           f"<style>{CSS}</style><h1>Analyse des doublons — {datetime.now():%d/%m/%Y %H:%M} "
           f"({'APPLIQUÉE' if apply else 'à blanc : rien n’a été modifié'})</h1><table class='stats'>"]
    for k, v in stats.items():
        out.append(f"<tr><td>{html.escape(k)}</td><td><b>{html.escape(str(v))}</b></td></tr>")
    out.append("</table><p>Règles : même photo si pHash ≤ 6, ou pHash ≤ 10 et dHash ≤ 10 · fusion auto si ≥ 2 photos "
               "communes, ou 1 photo + surface (≤ 5 %) / prix (≤ 15 %) compatibles, ou texte identique ≥ 80 %, ou "
               "caractéristiques jumelles (même commune, prix ± 1 %, surface ± 1 m², pièces/chambres/terrain compatibles) "
               "· vétos : surface > 12 %, ≥ 2 chambres d'écart, > 10 km, adresses précises à plus de 500 m (sauf ≥ 2 "
               "photos) · programme neuf : suggestion seulement.</p>")
    out.append(f"<h2>Fusions automatiques prévues : {len(groups)} biens, "
               f"{sum(len(g[0]) for g in groups)} annonces</h2>")
    for ids, edges in sorted(groups, key=lambda g: -len(g[0])):
        out.append("<div class='g'><div class='row'>" + "".join(card(items[i]) for i in ids if i in items) + "</div>"
                   + "".join(evidence(a, b, ev) for a, b, ev in edges) + "</div>")
    out.append(f"<h2>Doublons possibles (à valider dans l'UI) : {len(sugg)}</h2>")
    for a, b, ev in sugg:
        out.append(f"<div class='g'><div class='row'>{card(items[a], 3)}{card(items[b], 3)}</div>{evidence(a, b, ev)}</div>")
    gen = sorted(res["generic"])
    out.append(f"<h2>Photos marquées génériques (ignorées comme preuve) : {len(gen)}</h2><div class='gen'>")
    for lid, idx in gen[:120]:
        src = thumb(f"{lid}/{idx:02d}.jpg", 110)
        if src:
            out.append(f"<img title='#{lid} photo {idx}' src='{src}'>")
    out.append("</div>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="appliquer fusions et suggestions (sinon : à blanc)")
    ap.add_argument("--report", type=Path, help="chemin du rapport HTML (défaut : /data/reports/dedupe-…html)")
    ap.add_argument("--workers", type=int, default=4, help="processus de hachage (défaut 4)")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    db.init_schema()
    stats = {}
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute(f"SELECT id FROM listing WHERE {dedupe.SCOPE_SQL} OR merged_into IS NOT NULL")]
        t = time.monotonic()
        n = dedupe.ensure_hashes(conn, ids, workers=a.workers)
        conn.commit()
        stats["annonces du périmètre"] = len(ids)
        stats["photos hachées (nouvelles)"] = n
        stats["empreintes en base"] = conn.execute("SELECT count(*) AS n FROM listing_photo").fetchone()["n"]
        stats["durée hachage (s)"] = round(time.monotonic() - t, 1)
        log(f"empreintes : {n} nouvelles en {stats['durée hachage (s)']} s")
        extra, nt = thumbnails(conn, a.apply)
        stats["vignettes de cartes à compléter"] = nt
        t = time.monotonic()
        res = dedupe.analyze(conn, changed_only=False, extra_hashes=extra or None)
        merges = sum(op[0] == "merge" for op in res["ops"])
        stats["durée comparaison (s)"] = round(time.monotonic() - t, 1)
        stats["fusions automatiques prévues (= annonces qui deviennent secondaires)"] = merges
        stats["doublons possibles"] = sum(op[0] == "suggest" for op in res["ops"])
        stats["photos génériques (nouvelles)"] = len(res["generic"])
        log(f"comparaison : {merges} fusions, {stats['doublons possibles']} suggestions en {stats['durée comparaison (s)']} s")
        if a.apply:
            os.environ["DISABLE_ALERTS"] = "1"          # pas de rafale Discord pendant l'analyse initiale
            from app.collector import rescore
            applied = dedupe.apply(conn, res)
            dedupe.refresh_clusters(conn)
            crit = settings.load_criteria()
            with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
                rescore(conn, client, crit)
            alertable = [r["id"] for r in conn.execute(
                "SELECT * FROM listing WHERE merged_into IS NULL AND cluster IS NOT NULL AND alerted_at IS NULL "
                "AND (status='active' OR coalesce((cluster->>'active')::boolean, false))").fetchall()
                if alerts.should_alert(r, crit)]
            conn.commit()
            stats["fusions appliquées"] = applied["merged"]
            stats["suggestions enregistrées"] = applied["suggested"]
            stats["biens fusionnés devenus alertables (alerte au prochain cycle)"] = ", ".join(map(str, alertable)) or "aucun"
            log(f"appliqué : {applied}")
        else:
            conn.rollback()
        stats["durée totale (s)"] = round(time.monotonic() - t0, 1)
        if not a.no_report:
            path = a.report or settings.DATA_DIR / "reports" / f"dedupe-{datetime.now():%Y%m%d-%H%M}.html"
            report(path, res, stats, a.apply)
            log(f"rapport : {path}")
    for k, v in stats.items():
        print(f"  {k} : {v}")
    return stats


if __name__ == "__main__":
    main()
