#!/usr/bin/env python3
"""Harness de santé quotidien de la recherche de maison (stack `maison` + crons Hermes d'enrichissement).

Lancé chaque matin par un cron Hermes sans agent (`~/.hermes/scripts/maison_health.py`, livré sur Discord) ;
lisible aussi à la main : `python3 -I /volume1/docker/maison/ops/healthcheck.py [--no-state] [--json]`.

Il répond à deux questions :
1. Pas d'erreur ? Conteneurs maison-web / maison-collector, DNS épinglé, API, relais BAN/ADEME/cadastre testés
   par la même commande `maison` que les agents, journaux du collecteur (sources en échec, cycle en retard),
   fraîcheur de chaque source en base (une source morte garde un `last_seen` figé : Bien'ici, 26→31/10/2026),
   journaux de maison-web (5xx du relais, POST d'enrichissement refusés), crons Hermes (tirs en échec, réponses
   vides = limite de session, job en pause), disque.
2. Aucune analyse bloquée ? Une annonce de la file standard vue « pending » par le contrôle précédent (≥ 20 h)
   et toujours en attente, une demande « Investiguer » de plus de 12 h, une file qui ne baisse pas (0 fiche
   enrichie en 24 h alors qu'il y avait du travail), des fiches passées en `failed`, ou enrichies sans
   `summary_fr` (JSON partiel). Les sources secondaires d'un bien fusionné (doublons) sont hors file ; des
   doublons possibles non validés depuis plus d'une semaine donnent un rappel ⚠️.

Lecture seule : aucune écriture en base ni redémarrage. État entre deux contrôles (dates de première
observation des fiches en attente, compteurs de redémarrage) dans ~/.local/state/maison-health/.
Sortie : un message en français (une ligne si tout va bien) ; code retour 0 même en cas de problème (le message
EST l'alerte) ; une exception interne produit un message « harness en échec ».
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
HERMES = Path(os.environ.get("HERMES_HOME", HOME / ".hermes"))
STATE_DIR = Path(os.environ.get("MAISON_HEALTH_STATE", HOME / ".local/state/maison-health"))
MAISON_DIR = Path("/volume1/docker/maison")
API = os.environ.get("MAISON_URL", "http://127.0.0.1:8090").rstrip("/")
CONTAINERS = ("maison-web", "maison-collector")
EXPECTED_DNS = ["192.168.1.1", "8.8.8.8"]
JOBS = ("maison-enrichment", "maison-address-investigation")
MAIL_SOURCES = {"leboncoin", "seloger"}      # alimentées par les alertes e-mail : rythme irrégulier
SWEEP_SOURCES = {"bienici", "notaires", "maes", "netty", "agences", "pap"}   # balayées à chaque cycle

# Seuils (heures sauf mention)
SWEEP_STALE_H = 4          # cycle ≈ 15-25 min + sommeil 60-70 min
MAIL_STALE_H = 48
CYCLE_LATE_H = 3
STUCK_SEEN_H = 20          # vue en attente au contrôle de la veille et toujours là = bloquée
INVESTIGATION_LATE_H = 12  # le job d'investigation tourne toutes les 30 min de 8 h à 22 h
DUP_PENDING_WARN_H = 7 * 24  # doublons possibles à valider dans l'UI : rappel au-delà d'une semaine
BACKLOG_WARN = 45          # ≈ 3 passes de 15
ENRICH_RUN_LATE_H = 26     # le job standard tourne de 8 h à 22 h : dernier tir la veille au soir
INVESTIGATION_RUN_LATE_H = 26
DISK_MIN_FREE = 3 * 1024**3
LIMIT_MARKERS = re.compile(r"not processed|usage limit|rate.?limit|HTTP 429|session limit|CRON_FAILURE", re.I)

PENDING_SQL_FILTER = ("status='active' AND enrichment_status='pending' AND merged_into IS NULL "
                      "AND (excluded_reason IS NULL OR excluded_reason='à compléter') "
                      "AND (reaction IS DISTINCT FROM 'dislike' OR enrichment ? 'address_request')")   # = /api/enrichment/pending

DB_SQL = f"""
SELECT json_build_object(
 'sources', (SELECT coalesce(json_agg(s), '[]') FROM (
     SELECT source, count(*) FILTER (WHERE status='active') AS active,
            round((extract(epoch FROM now() - max(last_seen) FILTER (WHERE status='active')) / 3600.0)::numeric, 2) AS last_seen_age_h
     FROM listing GROUP BY source) s),
 'queue', (SELECT coalesce(json_agg(q), '[]') FROM (
     SELECT id, source, (enrichment ? 'address_request') AS inv,
            round((extract(epoch FROM now() - first_seen) / 3600.0)::numeric, 2) AS age_h,
            round((extract(epoch FROM now() - (enrichment->'address_request'->>'requested_at')::timestamptz) / 3600.0)::numeric, 2) AS req_age_h
     FROM listing WHERE {PENDING_SQL_FILTER}) q),
 'enriched_24h', (SELECT count(*) FROM listing WHERE enrichment_status='done' AND enriched_at > now() - interval '24 hours'),
 'failed_24h', (SELECT coalesce(json_agg(id ORDER BY id), '[]') FROM listing
                WHERE enrichment_status='failed' AND enriched_at > now() - interval '24 hours'),
 'no_summary_24h', (SELECT coalesce(json_agg(id ORDER BY id), '[]') FROM listing
                    WHERE enrichment_status='done' AND enriched_at > now() - interval '24 hours'
                      AND NOT enrichment ? 'summary_fr'),
 'new_24h', (SELECT count(*) FROM listing WHERE first_seen > now() - interval '24 hours'),
 'dup_pending', (SELECT json_build_object('count', count(*), 'oldest_h',
                     round((extract(epoch FROM now() - min(p.created_at)) / 3600.0)::numeric, 2))
                 FROM duplicate_pair p JOIN listing a ON a.id = p.id_a JOIN listing b ON b.id = p.id_b
                 WHERE p.status = 'suggested' AND coalesce(a.merged_into, a.id) <> coalesce(b.merged_into, b.id))
);
"""


# ----------------------------------------------------------------------------- collecte

def sh(argv, timeout=60, stdin=None, merge=True):
    """(rc, stdout+stderr) — stdout seul si merge=False ; rc=-1 si commande introuvable ou délai dépassé."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        if not merge and p.returncode == 0:
            return 0, p.stdout or ""
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return -1, f"{argv[0]} introuvable"
    except subprocess.TimeoutExpired:
        return -1, f"délai dépassé ({timeout} s)"


def parse_ts(s):
    """Horodatage ISO (docker : nanosecondes + Z ; Hermes : microsecondes + offset) -> epoch, ou None."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def collect_containers():
    out = {}
    for name in CONTAINERS:
        rc, txt = sh(["docker", "inspect", name], timeout=30)
        if rc != 0:
            out[name] = None
            continue
        info = json.loads(txt)[0]
        st = info["State"]
        out[name] = {"running": bool(st.get("Running")), "status": st.get("Status"),
                     "restarting": bool(st.get("Restarting")), "restart_count": info.get("RestartCount", 0),
                     "started_at": parse_ts(st.get("StartedAt")), "health": (st.get("Health") or {}).get("Status"),
                     "dns": info.get("HostConfig", {}).get("Dns") or []}
    return out


def collect_api():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(API + "/api/criteria", timeout=20) as r:
            return {"criteria": r.status}
    except urllib.error.HTTPError as e:
        return {"criteria": e.code}
    except Exception as e:      # connexion refusée, délai…
        return {"criteria": f"{type(e).__name__}: {e}"}


def maison_cli():
    """Commande `maison` du backend terminal en vigueur (celle qu'utilisent les crons)."""
    backend = "local"
    cfg = HERMES / "config.yaml"
    if cfg.exists():
        m = re.search(r"^terminal:\s*\n(?:[ \t]+.*\n|\s*\n)*?[ \t]+backend:\s*[\"']?(\w+)", cfg.read_text(), re.M)
        if m:
            backend = m.group(1)
    if backend == "docker":
        return backend, HERMES / "sandboxes/docker/default/workspace/bin/maison"
    return backend, Path(shutil.which("maison") or HOME / ".local/bin/maison")


RELAY_TESTS = {   # outil -> (arguments, validation du JSON)
    "reverse": (["reverse", "3.1", "50.67"], lambda d: len(d.get("features") or []) > 0),
    "ban": (["ban", "Wambrechies", "59636"], lambda d: len(d.get("features") or []) > 0),
    "ademe": (["ademe", "type_batiment:maison AND etiquette_dpe:C", "3.048,50.686,1500m"],
              lambda d: (d.get("total") or 0) > 0),
    "cadastre": (["cadastre", "3.048", "50.686", "40"], lambda d: (d.get("count") or 0) > 0),
}


def collect_relay(cli):
    out = {}
    if not cli.exists():
        return {t: {"ok": False, "detail": f"commande {cli} absente"} for t in RELAY_TESTS}
    for tool, (args, valid) in RELAY_TESTS.items():
        t0 = time.monotonic()
        rc, txt = sh([sys.executable, "-I", str(cli), *args], timeout=90)
        secs = round(time.monotonic() - t0, 1)
        ok, detail = False, txt.strip()[:160]
        if rc == 0:
            try:
                ok = bool(valid(json.loads(txt)))
                detail = "" if ok else "réponse vide"
            except ValueError:
                detail = "réponse non JSON"
        out[tool] = {"ok": ok, "detail": detail, "secs": secs}
    return out


def docker_logs(name, since_h):
    rc, txt = sh(["docker", "logs", "-t", "--since", f"{since_h}h", name], timeout=60)
    return txt if rc == 0 else ""


def parse_collector_log(text):
    res = {"last_cycle_end": None, "source_errors": {}, "email_errors": 0, "cycle_errors": 0,
           "zero_sources": {}, "postprocess_warnings": 0, "email_runs": 0}
    for line in text.splitlines():
        ts = parse_ts(line.split(" ", 1)[0])
        if "cycle terminé" in line:
            res["last_cycle_end"] = ts
        elif m := re.search(r"ERROR source (\S+) en échec", line):
            res["source_errors"][m.group(1)] = res["source_errors"].get(m.group(1), 0) + 1
        elif "ERROR emails en échec" in line:
            res["email_errors"] += 1
        elif "ERROR cycle en échec" in line:
            res["cycle_errors"] += 1
        elif m := re.search(r"WARNING (\S+): 0 annonce", line):
            res["zero_sources"][m.group(1)] = res["zero_sources"].get(m.group(1), 0) + 1
        elif "WARNING postprocess" in line:
            res["postprocess_warnings"] += 1
        elif re.search(r"INFO emails: \d+ annonces", line):
            res["email_runs"] += 1
    return res


REQ = re.compile(r'"(GET|POST) (\S+) HTTP/[\d.]+" (\d{3})')


def parse_web_log(text):
    res = {"tools_total": 0, "tools_5xx": 0, "last_tools_status": None, "enrich_post_errors": 0,
           "pending_errors": 0, "server_5xx": 0, "tracebacks": 0}
    for line in text.splitlines():
        if "Traceback (most recent call last)" in line:
            res["tracebacks"] += 1
            continue
        m = REQ.search(line)
        if not m:
            continue
        method, path, code = m.group(1), m.group(2), int(m.group(3))
        if code >= 500:
            res["server_5xx"] += 1
        if path.startswith("/api/tools/"):
            res["tools_total"] += 1
            res["tools_5xx"] += code >= 500
            res["last_tools_status"] = code
        elif method == "POST" and re.match(r"/api/listings/\d+/enrichment", path) and code >= 400:
            res["enrich_post_errors"] += 1
        elif path.startswith("/api/enrichment/pending") and code >= 400:
            res["pending_errors"] += 1
    return res


def collect_db():
    rc, txt = sh(["docker", "exec", "-i", "common-postgres", "sh", "-c",
                  'psql -U "$POSTGRES_USER" -d maison -At -v ON_ERROR_STOP=1'], timeout=60, stdin=DB_SQL,
                 merge=False)
    if rc != 0:
        return {"error": txt.strip()[:300]}
    return json.loads(txt)      # json_agg coupe ses éléments par des retours ligne : tout stdout


def last_response(path):
    """Texte de la section « ## Response » d'une sortie de cron Hermes."""
    try:
        txt = path.read_text(errors="replace")
    except OSError:
        return None
    i = txt.rfind("\n## Response")
    return txt[i + len("\n## Response"):].strip() if i >= 0 else None


def collect_jobs(now):
    out = {}
    try:
        jobs = json.loads((HERMES / "cron/jobs.json").read_text())["jobs"]
    except (OSError, ValueError, KeyError) as e:
        return {"error": f"jobs.json illisible : {e}"}
    try:
        db = sqlite3.connect(f"file:{HERMES / 'cron/executions.db'}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        db = None
    for name in JOBS:
        j = next((x for x in jobs if x.get("name") == name), None)
        if not j:
            out[name] = None
            continue
        info = {"id": j["id"], "enabled": j.get("enabled", True), "state": j.get("state"),
                "paused_reason": j.get("paused_reason"), "last_status": j.get("last_status"),
                "last_error": j.get("last_error"), "last_run_age_h": None,
                "completed_24h": 0, "failed_24h": 0, "errors_24h": [], "empty_24h": 0, "limit_24h": 0}
        if (t := parse_ts(j.get("last_run_at"))) is not None:
            info["last_run_age_h"] = round((now - t) / 3600, 2)
        if db is not None:
            rows = db.execute("SELECT status, error, claimed_at FROM executions WHERE job_id=? ORDER BY claimed_at",
                              (j["id"],)).fetchall()
            for status, err, claimed in rows:
                if (parse_ts(claimed) or 0) < now - 24 * 3600:
                    continue
                if status == "completed":
                    info["completed_24h"] += 1
                elif status == "failed":
                    info["failed_24h"] += 1
                    info["errors_24h"].append((err or "?")[:120])
        outdir = HERMES / "cron/output" / j["id"]
        if outdir.is_dir():
            for f in outdir.glob("*.md"):
                if f.stat().st_mtime < now - 24 * 3600:
                    continue
                r = last_response(f)
                if r is None or r == "":
                    info["empty_24h"] += 1
                elif LIMIT_MARKERS.search(r) and "[SILENT]" not in r:
                    info["limit_24h"] += 1
        out[name] = info
    return out


def collect(now=None):
    now = now or time.time()
    backend, cli = maison_cli()
    containers = collect_containers()
    since_h = 26
    facts = {
        "now": now, "backend": backend, "cli": str(cli), "cli_exists": cli.exists(),
        "containers": containers, "api": collect_api(), "relay": collect_relay(cli),
        "collector_log": parse_collector_log(docker_logs("maison-collector", since_h)),
        "web_log": parse_web_log(docker_logs("maison-web", since_h)),
        "db": collect_db(), "jobs": collect_jobs(now),
    }
    try:
        facts["disk_free"] = shutil.disk_usage("/volume1").free
    except OSError:
        facts["disk_free"] = None
    return facts


# ----------------------------------------------------------------------------- règles

def fmt_h(h):
    if h is None:
        return "?"
    return f"{round(h * 60)} min" if h < 1 else (f"{h:.0f} h" if h < 48 else f"{h / 24:.0f} j")


def evaluate(f, state):
    """-> (problèmes, avertissements, stats, nouvel_état). Fonction pure : testée sans Docker."""
    now = f["now"]
    P, W, S = [], [], {}
    new_state = {"pending_first_seen": {}, "restart_counts": {}, "last_check": now}

    # --- conteneurs
    for name in CONTAINERS:
        c = f["containers"].get(name)
        if not c:
            P.append(f"conteneur {name} introuvable")
            continue
        if not c["running"] or c["restarting"]:
            P.append(f"conteneur {name} : {c['status']}{' (redémarre en boucle)' if c['restarting'] else ''}")
        if c.get("health") == "unhealthy":
            P.append(f"conteneur {name} : unhealthy")
        prev = (state.get("restart_counts") or {}).get(name)
        if prev is not None and c["restart_count"] > prev:
            W.append(f"{name} a redémarré tout seul {c['restart_count'] - prev} fois depuis le dernier contrôle")
        new_state["restart_counts"][name] = c["restart_count"]
        if c["dns"] != EXPECTED_DNS:
            W.append(f"{name} : DNS {c['dns'] or 'hérité de l’hôte'} au lieu de {EXPECTED_DNS} (recréé hors compose ?)")

    # --- API + relais
    if f["api"].get("criteria") != 200:
        P.append(f"API maison-web injoignable ({f['api'].get('criteria')})")
    if not f["cli_exists"]:
        P.append(f"commande `maison` absente pour le backend {f['backend']} ({f['cli']}) : les crons ne peuvent rien faire")
    bad = {t: r for t, r in f["relay"].items() if not r["ok"]}
    if bad:
        by_detail = {}
        for t, r in bad.items():
            by_detail.setdefault(r["detail"] or "échec", []).append(t)
        P.append("relais en panne : " + " ; ".join(f"{', '.join(ts)} → {d}" for d, ts in by_detail.items()))
    slow = [f"{t} {r['secs']} s" for t, r in f["relay"].items() if r["ok"] and r.get("secs", 0) > 30]
    if slow:
        W.append("relais lent : " + ", ".join(slow))

    # --- collecteur (journaux)
    cl = f["collector_log"]
    col = f["containers"].get("maison-collector") or {}
    started = col.get("started_at") or 0
    if cl["last_cycle_end"] is None:
        if now - started > 1.5 * 3600:
            P.append(f"collecteur : aucun cycle terminé depuis son démarrage il y a {fmt_h((now - started) / 3600)}")
    elif now - cl["last_cycle_end"] > CYCLE_LATE_H * 3600:
        P.append(f"collecteur : dernier cycle terminé il y a {fmt_h((now - cl['last_cycle_end']) / 3600)} (bloqué ?)")
    if cl["last_cycle_end"]:
        S["dernier_cycle_h"] = round((now - cl["last_cycle_end"]) / 3600, 2)
    elif started:
        S["collecteur_demarre_h"] = round((now - started) / 3600, 2)
    for src, n in sorted(cl["source_errors"].items()):
        P.append(f"collecteur : source {src} en échec {n} fois en 24 h")
    if cl["email_errors"]:
        P.append(f"collecteur : lecture des e-mails d'alerte en échec {cl['email_errors']} fois en 24 h")
    if cl["cycle_errors"]:
        P.append(f"collecteur : cycle complet en échec {cl['cycle_errors']} fois en 24 h")
    for src, n in sorted(cl["zero_sources"].items()):
        W.append(f"collecteur : {src} a rendu 0 annonce {n} fois (source cassée ?)")
    if cl["postprocess_warnings"] > 20:
        W.append(f"collecteur : {cl['postprocess_warnings']} avertissements postprocess en 24 h")

    # --- base : fraîcheur des sources, file d'attente
    db = f["db"]
    if "error" in db:
        P.append(f"base maison illisible : {db['error']}")
        db = {}
    fresh = 0
    for s in db.get("sources", []):
        src, age = s["source"], s["last_seen_age_h"]
        if not s["active"] or age is None:
            continue
        limit = MAIL_STALE_H if src in MAIL_SOURCES else SWEEP_STALE_H
        if age > limit:
            (W if src in MAIL_SOURCES else P).append(
                f"source {src} : aucune annonce revue depuis {fmt_h(age)} ({s['active']} actives figées)")
        else:
            fresh += 1
    S["sources_fraiches"] = f"{fresh}/{sum(1 for s in db.get('sources', []) if s['active'])}"
    if db and db.get("new_24h", 0) == 0:
        W.append("aucune nouvelle annonce en 24 h")

    queue = db.get("queue", [])
    std = [q for q in queue if not q["inv"]]
    inv = [q for q in queue if q["inv"]]
    seen = state.get("pending_first_seen") or {}
    if not state.get("last_check"):
        S["premier_controle"] = True
    stuck = []
    for q in std:
        key = str(q["id"])
        first = seen.get(key, now)
        new_state["pending_first_seen"][key] = first
        if now - first >= STUCK_SEEN_H * 3600:
            stuck.append((q["id"], (now - first) / 3600))
    if stuck:
        stuck.sort(key=lambda x: -x[1])
        ids = ", ".join(f"#{i} ({fmt_h(h)})" for i, h in stuck[:8]) + (" …" if len(stuck) > 8 else "")
        P.append(f"{len(stuck)} analyse(s) bloquée(s) dans la file standard (déjà en attente au contrôle précédent) : {ids}")
    late_inv = [q for q in inv if (q["req_age_h"] or 0) > INVESTIGATION_LATE_H]
    if late_inv:
        P.append("investigation(s) d'adresse en attente depuis plus de "
                 f"{INVESTIGATION_LATE_H} h : " + ", ".join(f"#{q['id']} ({fmt_h(q['req_age_h'])})" for q in late_inv))
    if len(std) > BACKLOG_WARN:
        W.append(f"file d'enrichissement chargée : {len(std)} annonces en attente")
    enriched = db.get("enriched_24h", 0)
    prev_queue = state.get("queue_standard")
    if db and enriched == 0 and (prev_queue or 0) > 0 and std:
        P.append(f"aucune fiche enrichie en 24 h alors que {len(std)} attendent : l'enrichissement est à l'arrêt")
    if db.get("failed_24h"):
        P.append("fiches marquées en échec par l'agent en 24 h : " + ", ".join(f"#{i}" for i in db["failed_24h"]))
    if db.get("no_summary_24h"):
        W.append("fiches enrichies sans résumé (JSON partiel ?) : " + ", ".join(f"#{i}" for i in db["no_summary_24h"]))
    dup = db.get("dup_pending") or {}
    if dup.get("count") and (dup.get("oldest_h") or 0) > DUP_PENDING_WARN_H:
        W.append(f"{dup['count']} doublon(s) possible(s) à valider dans l'UI (filtre « Doublons à valider »), "
                 f"le plus ancien depuis {fmt_h(dup['oldest_h'])}")
    new_state["queue_standard"] = len(std)
    S.update(enrichies_24h=enriched, file=len(std), investigations=len(inv),
             plus_ancienne_h=round(max((now - new_state["pending_first_seen"][str(q["id"])]) / 3600 for q in std), 2)
             if std else 0, nouvelles_24h=db.get("new_24h"), doublons_a_valider=dup.get("count", 0))

    # --- maison-web (journaux)
    wl = f["web_log"]
    if wl["tools_5xx"]:
        msg = f"relais : {wl['tools_5xx']}/{wl['tools_total']} appels en erreur 5xx en 24 h"
        (P if wl["tools_5xx"] >= 5 or wl["last_tools_status"] and wl["last_tools_status"] >= 500 else W).append(msg)
    if wl["enrich_post_errors"]:
        P.append(f"{wl['enrich_post_errors']} POST d'enrichissement refusés par l'API en 24 h")
    if wl["pending_errors"]:
        P.append(f"{wl['pending_errors']} lectures de la file refusées par l'API en 24 h")
    other_5xx = wl["server_5xx"] - wl["tools_5xx"]
    if other_5xx > 0 or wl["tracebacks"]:
        W.append(f"maison-web : {other_5xx} réponses 5xx hors relais, {wl['tracebacks']} tracebacks en 24 h")

    # --- crons Hermes
    jobs = f["jobs"]
    if "error" in jobs:
        P.append(jobs["error"])
        jobs = {}
    for name in JOBS:
        j = jobs.get(name)
        if j is None:
            P.append(f"cron Hermes {name} introuvable")
            continue
        if not j["enabled"] or j["state"] == "paused":
            P.append(f"cron {name} en pause ({j['paused_reason'] or 'sans raison'}) : rien n'est traité")
            continue
        late = ENRICH_RUN_LATE_H if name == "maison-enrichment" else INVESTIGATION_RUN_LATE_H
        if j["last_run_age_h"] is None or j["last_run_age_h"] > late:
            P.append(f"cron {name} : dernier tir il y a {fmt_h(j['last_run_age_h'])} (gateway arrêtée ?)")
        if j["last_status"] not in (None, "ok"):
            P.append(f"cron {name} : dernier tir en « {j['last_status']} » ({(j['last_error'] or '')[:120]})")
        if j["failed_24h"]:
            errs = sorted(set(j["errors_24h"]))
            (P if j["failed_24h"] >= 3 else W).append(
                f"cron {name} : {j['failed_24h']} tir(s) en échec en 24 h ({'; '.join(errs)[:200]})")
        # un tir en échec laisse aussi une sortie vide : ne compter que les vides des tirs « réussis »
        silent = max(0, j["empty_24h"] - j["failed_24h"]) + j["limit_24h"]
        if silent:
            (P if silent >= 3 else W).append(
                f"cron {name} : {silent} tir(s) sans réponse ou stoppé(s) par la limite de session en 24 h")
        S[f"tirs_{'std' if name == 'maison-enrichment' else 'inv'}"] = j["completed_24h"]

    if f.get("disk_free") is not None and f["disk_free"] < DISK_MIN_FREE:
        W.append(f"/volume1 : plus que {f['disk_free'] / 1024**3:.1f} Gio libres (base + photos)")
    return P, W, S, new_state


def render(P, W, S):
    cycle = (f"dernier cycle il y a {fmt_h(S['dernier_cycle_h'])}" if "dernier_cycle_h" in S else
             f"1er cycle en cours (démarré il y a {fmt_h(S.get('collecteur_demarre_h'))})")
    attente = ("référence posée (1er contrôle)" if S.get("premier_controle") else
               f"plus ancienne vue il y a {fmt_h(S.get('plus_ancienne_h'))}")
    stats = (f"collecteur : {cycle}, sources fraîches {S.get('sources_fraiches', '?')} · "
             f"{S.get('nouvelles_24h', '?')} nouvelles, {S.get('enrichies_24h', '?')} enrichies en 24 h · "
             f"file {S.get('file', '?')} ({attente}), "
             f"investigations {S.get('investigations', '?')} · tirs cron 24 h : {S.get('tirs_std', '?')} std, "
             f"{S.get('tirs_inv', '?')} inv")
    if not P and not W:
        return f"✅ **Maison** — tout fonctionne · {stats}"
    head = (f"🚨 **Maison** — {len(P)} problème(s)" if P else "⚠️ **Maison** — rien de bloquant") + \
        (f", {len(W)} point(s) d'attention" if W else "")
    lines = [head] + [f"• {p}" for p in P] + [f"• ⚠️ {w}" for w in W] + [f"_{stats}_"]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main

def load_state():
    try:
        return json.loads((STATE_DIR / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(new_state, P, W, S):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / "state.json.tmp"
    tmp.write_text(json.dumps(new_state, indent=1))
    tmp.replace(STATE_DIR / "state.json")
    with open(STATE_DIR / "history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                             "problems": P, "warnings": W, "stats": S}, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-state", action="store_true", help="ne pas mettre à jour l'état (essai à blanc)")
    ap.add_argument("--json", action="store_true", help="afficher les faits collectés et le verdict en JSON")
    ap.add_argument("--quiet-ok", action="store_true", help="rien n'afficher si tout va bien")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        facts = collect()
        P, W, S, new_state = evaluate(facts, load_state())
        if not a.no_state:
            save_state(new_state, P, W, S)
    except Exception:
        print("🚨 **Maison** — le harness de santé a planté :\n```\n" + traceback.format_exc()[-1500:] + "```")
        return 0
    if a.json:
        print(json.dumps({"facts": facts, "problems": P, "warnings": W, "stats": S}, ensure_ascii=False, indent=1,
                         default=str))
    elif P or W or not a.quiet_ok:
        print(render(P, W, S))
    return 0


if __name__ == "__main__":
    sys.exit(main())
