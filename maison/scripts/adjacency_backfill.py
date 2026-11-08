#!/usr/bin/env python3
"""Rattrapage de la mitoyenneté (change OpenSpec improve-adjacency-detection, design D12).

À blanc par défaut : calcule la mitoyenneté cartographique de toutes les annonces éligibles (design D5 : principale
active, adresse précise exacte/probable/validée/source), recalcule les verdicts « annonce » du texte (design D7),
puis écrit un rapport HTML autonome (effectifs, confusion ancien type → type retenu, variations de score,
franchissements du seuil d'alerte, désaccords illustrés par la vue aérienne et la première photo). Les écritures
sont faites dans une transaction annulée à la fin : aucune annonce n'est modifiée.

    docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py [--report PATH] [--workers 4]
    docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py --apply
    docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py --request-checks

--apply : enregistre `adjacency`, `property_type`/`property_type_src` (sauf champ structuré d'une source),
rafraîchit les groupes et recalcule les scores SANS alerte Discord ; le résumé liste les biens devenus alertables
(signalés au cycle suivant du collecteur). Relançable : une seconde application ne recalcule que les échecs.
--request-checks : demande une vérification du type (`type_request`, raison `garage_backfill`) pour chaque annonce
principale active non exclue dont le garage est confirmé, sauf type corrigé par l'utilisateur, demande en attente
ou déjà traitée. Idempotent.
"""
import argparse
import base64
import html
import io
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from PIL import Image  # noqa: E402

from app import adjacency, aerial, alerts, db, dedupe, settings, textparse  # noqa: E402
from app.scoring import TYPE_LABELS, merged_columns, resolve_type, score_listing  # noqa: E402

PRINCIPAL_ACTIVE = ("merged_into IS NULL AND (status = 'active' OR coalesce((cluster->>'active')::boolean, false))")
ILLUSTRATED = 40
AGENT_UA = "maison/1.0 (rattrapage mitoyennete ; usage prive)"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------- calcul ----------
def to_compute(conn) -> tuple[list, list]:
    """Annonces à calculer (D5) ; un échec est retenté quel que soit son âge (date de référence lointaine)."""
    return adjacency.due(conn, now=adjacency._now() + timedelta(days=3650))


def compute_all(todo, workers: int) -> dict[int, dict]:
    out = {}
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
        def one(item):
            r, want = item
            return r["id"], adjacency.compute(client, want["lon"], want["lat"], want["address"], want["confidence"])
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for n, (lid, res) in enumerate(ex.map(one, todo), 1):
                out[lid] = res
                if n % 25 == 0 or n == len(todo):
                    log(f"mitoyenneté : {n}/{len(todo)}")
    return out


def old_type(row: dict) -> str:
    """Type affiché avant le change : verdict de l'agent, sinon la colonne (complétée par le groupe)."""
    enr = row.get("enrichment") or {}
    t = enr.get("property_type")
    if t not in TYPE_LABELS or t == "unknown":
        t = merged_columns(row).get("property_type")
    return t if t in TYPE_LABELS else "unknown"


def text_verdicts(conn) -> list[tuple]:
    """Nouveaux verdicts « annonce » (D7) pour toutes les annonces dont le type ne vient pas d'un champ structuré."""
    out = []
    for r in conn.execute("SELECT id, title, description, property_type, property_type_src FROM listing "
                          "WHERE property_type_src IS DISTINCT FROM 'structured'").fetchall():
        f = textparse.type_fields(f"{r['title'] or ''} {r['description'] or ''}")
        typ = f["property_type"] if f["property_type"] != "unknown" else None
        if (typ, f["property_type_src"]) != (r["property_type"], r["property_type_src"]):
            out.append((r["id"], typ, f["property_type_src"]))
    return out


def write(conn, results: dict[int, dict], texts: list[tuple]) -> None:
    for lid, res in results.items():
        conn.execute("UPDATE listing SET adjacency=%s WHERE id=%s", (Jsonb(res), lid))
    for lid, typ, src in texts:
        conn.execute("UPDATE listing SET property_type=%s, property_type_src=%s WHERE id=%s", (typ, src, lid))
    dedupe.refresh_clusters(conn)


def snapshot(conn) -> dict[int, dict]:
    return {r["id"]: r for r in conn.execute(f"SELECT * FROM listing WHERE {PRINCIPAL_ACTIVE}").fetchall()}


def request_checks(conn) -> int:
    """Demandes `garage_backfill` (spec photo-adjacency-review, « Périmètre de la re-vérification »)."""
    from app.scoring import effective
    n = 0
    for r in conn.execute(f"SELECT * FROM listing WHERE {PRINCIPAL_ACTIVE} AND excluded_reason IS NULL").fetchall():
        enr = r["enrichment"] or {}
        if enr.get("property_type_user") or enr.get("type_request"):
            continue
        if any((c or {}).get("reason") == "garage_backfill" for c in enr.get("type_checks") or []):
            continue
        if effective(r).get("has_garage") is not True:
            continue
        req = {"requested_at": adjacency._now().isoformat(), "reason": "garage_backfill", "note": "",
               "adjacency_at": (r["adjacency"] or {}).get("computed_at")}
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s", (Jsonb({"type_request": req}), r["id"]))
        n += 1
    return n


# ---------- rapport HTML ----------
def b64(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def first_photo(row: dict, size=260) -> str:
    pics = ((row.get("cluster") or {}).get("photos") or [])
    rel = next((p[len("/photos/"):] for p in pics if isinstance(p, str) and p.startswith("/photos/")), None)
    rel = rel or next(iter(row.get("photos") or []), None)
    if not rel:
        return ""
    try:
        im = Image.open(dedupe.photo_dir() / rel)
        im.draft("RGB", (size * 2, size * 2))
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=65)
        return b64(buf.getvalue())
    except Exception:
        return ""


CSS = """body{font:13px/1.45 system-ui,sans-serif;margin:20px;color:#18202a;background:#f5f6f8}
h1{font-size:20px}h2{margin-top:30px;border-bottom:2px solid #2f6f5e;padding-bottom:4px}
table{border-collapse:collapse;background:#fff}td,th{border:1px solid #e3e6eb;padding:3px 9px;text-align:right}
th:first-child,td:first-child{text-align:left}.d{background:#fff;border:1px solid #e3e6eb;border-radius:10px;padding:10px;
margin:12px 0;display:flex;gap:12px;flex-wrap:wrap}.d img{height:260px;border-radius:6px}.up{color:#1f9d6b}.down{color:#d64545}
.muted{color:#6b7684}"""


def _table(head, rows) -> str:
    return ("<table><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


def report(path: Path, stats: dict, results: dict, before: dict, after: dict, new_scores: dict, crit: dict,
           apply: bool, illustrate: bool = True) -> None:
    lbl = lambda t: TYPE_LABELS.get(t, t)  # noqa: E731
    ok = [r for r in results.values() if r.get("status") == "ok"]
    status = Counter(r.get("status") for r in results.values())
    types = Counter(r.get("type") for r in ok if r.get("usable"))
    warns = Counter(w for r in ok for w in r.get("warnings") or [])
    out = [f"<!doctype html><meta charset='utf-8'><title>Mitoyenneté — {datetime.now():%d/%m/%Y %H:%M}</title>",
           f"<style>{CSS}</style><h1>Rattrapage de la mitoyenneté — {datetime.now():%d/%m/%Y %H:%M} "
           f"({'APPLIQUÉ' if apply else 'à blanc : rien n’a été modifié'})</h1>",
           _table(["", ""], stats.items())]
    out.append("<h2>Effectifs du calcul cartographique</h2>"
               + _table(["statut", "annonces"], status.most_common())
               + "<p></p>" + _table(["catégorie (carte utilisable)", "annonces"], [(lbl(k), v) for k, v in types.most_common()])
               + "<p></p>" + _table(["avertissement", "annonces"], warns.most_common()))
    # confusion ancien type → type retenu
    conf = Counter()
    for lid, row in after.items():
        if lid in before:
            conf[(old_type(before[lid]), resolve_type(row)["type"])] += 1
    order = ["detached", "link_detached", "semi", "terraced", "unknown"]
    out.append("<h2>Confusion : type actuel (lignes) → type retenu (colonnes)</h2>"
               + _table(["actuel \\ retenu"] + [lbl(t) for t in order] + ["total"],
                        [[lbl(a)] + [conf[(a, b)] for b in order] + [sum(conf[(a, b)] for b in order)] for a in order]))
    src = Counter(resolve_type(r)["source"] or "aucune" for r in after.values())
    out.append("<p></p>" + _table(["source du type retenu", "annonces"], src.most_common()))
    # scores
    deltas = [(lid, before[lid]["score"], new_scores[lid]) for lid in new_scores
              if lid in before and (before[lid]["score"] or 0) != (new_scores[lid] or 0)]
    hist = Counter((s1 or 0) - (s0 or 0) for _, s0, s1 in deltas)
    out.append(f"<h2>Variations de score : {len(deltas)} annonces</h2>"
               + _table(["écart", "annonces"], sorted(hist.items())))
    seuil = crit["alert"]["min_score"]
    cross = [(lid, s0, s1) for lid, s0, s1 in deltas if ((s0 or 0) >= seuil) != ((s1 or 0) >= seuil)]
    rows = []
    for lid, s0, s1 in sorted(cross, key=lambda x: -(x[2] or 0)):
        a, b = before[lid], after[lid]
        rows.append((f"#{lid}", s0, s1, "↗ franchit" if (s1 or 0) >= seuil else "↘ repasse sous",
                     lbl(old_type(a)), lbl(resolve_type(b)["type"]),
                     "oui" if (s1 or 0) >= seuil and alerts.should_alert({**b, "score": s1}, crit) else "",
                     (b.get("city") or ""), (b.get("title") or "")[:60]))
    out.append(f"<h2>Franchissements du seuil d'alerte ({seuil}) : {len(cross)}</h2>"
               + _table(["annonce", "score actuel", "nouveau", "sens", "type actuel", "type retenu",
                         "alertable (alerte au cycle suivant)", "commune", "titre"], rows))
    # désaccords illustrés
    dis = [lid for lid, r in after.items() if lid in before and
           (resolve_type(r)["disagreement"] or old_type(before[lid]) != resolve_type(r)["type"])
           and (r.get("adjacency") or {}).get("status") == "ok"]
    dis.sort(key=lambda i: -(new_scores.get(i) or 0))
    out.append(f"<h2>Désaccords ({len(dis)}), les {min(ILLUSTRATED, len(dis))} au plus fort score illustrés</h2>"
               "<p class='muted'>Vue aérienne : parcelle blanche, maison bleue, propriété cyan, voisin habité rouge, "
               "annexe voisine orange, murs communs magenta (tracés ±3 m).</p>")
    with httpx.Client(headers={"User-Agent": AGENT_UA}, timeout=30) as client:
        for lid in dis[:ILLUSTRATED]:
            r = after[lid]
            ti = resolve_type(r, explain=True)
            img = ""
            if illustrate:
                try:
                    img = b64(aerial.render(client, r["adjacency"]))
                except Exception as e:  # noqa: BLE001
                    img = ""
                    log(f"vue aérienne #{lid} : {e}")
            pic = first_photo(r)
            verd = "<br>".join(f"{'✔' if v['used'] else '⚠' if v['disagrees'] else '·'} {html.escape(v['source_label'])} : "
                               f"{html.escape(v['label'])} — {html.escape(v['detail'])}" for v in ti["verdicts"])
            out.append(f"<div class='d'>{f'<img src={chr(39)}{img}{chr(39)}>' if img else ''}"
                       f"{f'<img src={chr(39)}{pic}{chr(39)}>' if pic else ''}<div><b>#{lid}</b> · "
                       f"{html.escape(r.get('city') or '')} · score {before[lid]['score']} → {new_scores.get(lid)}<br>"
                       f"type actuel : <b>{lbl(old_type(before[lid]))}</b> → retenu : <b>{html.escape(ti['label'])}</b> "
                       f"({html.escape(ti['source'] or '')})<br>{verd}<br>"
                       f"<a href='{html.escape(settings.PUBLIC_BASE_URL)}/#/l/{lid}' target='_blank'>fiche ↗</a></div></div>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="enregistrer les résultats (sinon : à blanc)")
    ap.add_argument("--request-checks", action="store_true", help="créer les demandes de vérification (garage confirmé)")
    ap.add_argument("--report", type=Path, help="chemin du rapport HTML (défaut : /data/reports/adjacency-…html)")
    ap.add_argument("--workers", type=int, default=4, help="calculs en parallèle (défaut 4, au plus 4)")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--no-images", action="store_true", help="rapport sans vues aériennes (pas de requête WMS)")
    a = ap.parse_args(argv)
    os.environ["DISABLE_ALERTS"] = "1"                 # aucune rafale Discord, ni à blanc ni à l'application
    from app.collector import rescore
    t0 = time.monotonic()
    db.init_schema()
    stats = {}
    crit = settings.load_criteria()
    with db.connect() as conn:
        if a.request_checks and not a.apply:
            n = request_checks(conn)
            conn.commit()
            stats["demandes de vérification créées"] = n
            log(f"demandes de vérification : {n}")
            for k, v in stats.items():
                print(f"  {k} : {v}")
            return stats
        todo, na = to_compute(conn)
        conn.commit()                                  # pas de transaction ouverte pendant les calculs réseau
        stats["annonces à calculer"] = len(todo)
        stats["annonces non applicables (position imprécise)"] = len(na)
        log(f"{len(todo)} annonces à calculer, {len(na)} non applicables")
        t = time.monotonic()
        results = compute_all(todo, min(4, a.workers))
        stats["durée des calculs (s)"] = round(time.monotonic() - t, 1)
        stats["calculs en échec"] = sum(r["status"] == "error" for r in results.values())
        before = snapshot(conn)
        texts = text_verdicts(conn)
        stats["verdicts « annonce » recalculés (texte)"] = len(texts)
        for lid, why in na:
            results.setdefault(lid, {"v": adjacency.V, "status": "not_applicable", "reason": why,
                                     "computed_at": adjacency._now().isoformat()})
        write(conn, results, texts)
        with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client:
            rescore(conn, client, crit)
        after = snapshot(conn)
        new_scores = {lid: r["score"] for lid, r in after.items()}
        stats["scores modifiés"] = sum(1 for lid in after if lid in before and before[lid]["score"] != new_scores[lid])
        if a.apply:
            alertable = [lid for lid, r in after.items() if alerts.should_alert(r, crit)]
            if a.request_checks:
                stats["demandes de vérification créées"] = request_checks(conn)
            conn.commit()
            stats["biens devenus alertables (alerte au prochain cycle)"] = ", ".join(map(str, alertable)) or "aucun"
            log("appliqué")
        else:
            conn.rollback()           # à blanc : tout est annulé avant le rapport (verrous de lignes relâchés)
    stats["durée totale (s)"] = round(time.monotonic() - t0, 1)
    if not a.no_report:
        path = a.report or settings.DATA_DIR / "reports" / f"adjacency-{datetime.now():%Y%m%d-%H%M}.html"
        report(path, stats, results, before, after, new_scores, crit, a.apply, illustrate=not a.no_images)
        log(f"rapport : {path}")
    for k, v in stats.items():
        print(f"  {k} : {v}")
    return stats


if __name__ == "__main__":
    main()
