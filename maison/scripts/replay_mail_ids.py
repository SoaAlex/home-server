"""Rejoue la boîte d'alertes SeLoger après le correctif des identifiants d'annonce (motif …/annonce/…/<ID>).

Avant le correctif, toute annonce dont le slug de ville commençait par au moins 8 lettres (« lambersart-59130 »)
recevait la ville pour identifiant : ses cartes s'écrasaient sur une seule ligne `seloger/<ville>`. Les cartes sont
toujours dans la boîte ; ce script les relit avec le motif corrigé.

Deux temps, pour contrôler avant d'écrire :
  extract   lecture seule (BODY.PEEK) ; résout les liens de suivi comme le collecteur (en-tête Location, sans suivre la
            redirection vers le portail) mais réessaie les 429/5xx au lieu de perdre la carte en silence ; écrit une
            ligne JSON par mail. Reprenable : les UID déjà faits (sans échec de lien) sont sautés.
  apply     relit ce fichier, classe chaque carte par rapport à la base et n'écrit que ce qui manque ou ce qui est plus
            récent. --dry-run : classement seul. Sans --dry-run, DISABLE_ALERTS=1 est obligatoire.

Règles d'écriture (apply) :
  - identifiant déjà en base et vu plus récemment que le mail : carte ignorée, ligne intacte (aucune régression de prix
    ni d'historique, first_seen non modifié) ;
  - sinon app.ingest.upsert (même chemin que le collecteur), puis first_seen / last_seen ramenés à la date du mail, et
    date de l'éventuelle entrée price_history ajoutée = date du mail ;
  - postprocess (photos, géocodage, trajets) puis rescore, sur les seules lignes touchées.

Usage, dans l'image du collecteur (script monté sur /replay) :
  python /replay/replay_mail_ids.py extract --out /data/replay/seloger_cards.jsonl [--from-uid N] [--until-uid M]
  DISABLE_ALERTS=1 python /replay/replay_mail_ids.py apply --in /data/replay/seloger_cards.jsonl [--dry-run]
"""
import argparse
import collections
import concurrent.futures as cf
import email
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email import policy
from email.utils import parsedate_to_datetime

import httpx

from app import db, settings
from app.ingest import upsert
from app.sources import email_alerts as ea

# Motif d'AVANT le correctif : sert à repérer les cartes dont l'identifiant était faux.
OLD_SELOGER = re.compile(r"https?://(?:www\.)?seloger\.com/annonces/achat/[^\s\"'<>?#]*?/(\d+)\.htm"
                         r"|https?://(?:www\.)?seloger\.com/annonce/[^\s\"'<>?#]*?/([A-Za-z0-9]{8,})")
RETRY_STATUS = (429, 500, 502, 503, 504)


def old_id(url):
    m = OLD_SELOGER.search(url or "")
    return next((g for g in m.groups() if g), None) if m else None


class Tracker:
    """Client minimal pour make_resolver : réessaie 429/5xx/erreurs réseau (le collecteur, lui, perd la carte)."""

    def __init__(self, client):
        self.client, self.stats, self.calls, self.failed = client, collections.Counter(), 0, 0

    def get(self, url, **kw):
        for attempt in range(5):
            self.calls += 1
            try:
                r = self.client.get(url, **kw)
            except Exception as e:                                    # délai dépassé, connexion coupée…
                self.stats[type(e).__name__] += 1
            else:
                self.stats[str(r.status_code)] += 1
                if r.status_code not in RETRY_STATUS:
                    return r
            time.sleep(1.5 * 2 ** attempt)
        self.failed += 1
        raise RuntimeError(f"lien de suivi injoignable après 5 essais : {url[:80]}")


def parse_mail(raw, cities, client):
    tr = Tracker(client)
    items = ea.parse_message(raw, ea.make_resolver(tr, limit=200), cities)
    return items, dict(tr.stats), tr.calls, tr.failed


def _login():
    m = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
    m.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
    m.select("INBOX", readonly=True)
    return m


def fetch_raw(m, uid):
    _, d = m.uid("fetch", str(uid), "(BODY.PEEK[])")
    return next((p[1] for p in d if isinstance(p, tuple)), b"")


def list_mails(m, lo, hi, sender):
    """UID -> (date ISO UTC, expéditeur, sujet) des mails dont l'expéditeur contient `sender`."""
    _, data = m.uid("fetch", f"{lo}:*", "(BODY.PEEK[HEADER.FIELDS (FROM DATE SUBJECT)])")
    out = {}
    for part in data:
        if not isinstance(part, tuple):
            continue
        found = re.search(rb"UID (\d+)", part[0])
        if not found:
            continue
        uid = int(found.group(1))
        if uid < lo or (hi and uid > hi):
            continue
        h = email.message_from_bytes(part[1], policy=policy.default)
        try:
            frm, subj = str(h["from"] or ""), str(h["subject"] or "")
        except Exception:
            frm, subj = "", ""
        if sender not in frm.lower():
            continue
        try:
            when = parsedate_to_datetime(h["date"]).astimezone(timezone.utc).isoformat()
        except Exception:
            when = None
        out[uid] = (when, frm[:60], subj[:120])
    return out


def extract(args):
    crit = settings.load_criteria()
    cities = [*crit.get("zones", {}).keys(), *crit.get("excluded_cities", [])]
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    done = set()
    if os.path.exists(args.out):
        for line in open(args.out):
            r = json.loads(line)
            if not r.get("error") and not r.get("failed"):
                done.add(r["uid"])
    m = _login()
    mails = list_mails(m, args.from_uid, args.until_uid, args.sender)
    todo = [u for u in sorted(mails) if u not in done]
    print(f"{len(mails)} mails « {args.sender} » dans la plage (UID {min(mails, default=0)}–{max(mails, default=0)}), "
          f"{len(done & set(mails))} déjà faits, {len(todo)} à traiter", flush=True)
    t0, n_items, agg = time.time(), 0, collections.Counter()
    out = open(args.out, "a")
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client, \
            cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        inflight = {}

        def drain():
            nonlocal n_items
            finished, _ = cf.wait(list(inflight), return_when=cf.FIRST_COMPLETED)
            for f in finished:
                uid = inflight.pop(f)
                when, frm, subj = mails[uid]
                try:
                    items, stats, calls, failed = f.result()
                    rec = {"uid": uid, "date": when, "from": frm, "subject": subj, "calls": calls, "failed": failed,
                           "status": stats, "items": items}
                    n_items += len(items)
                    agg.update(stats)
                except Exception as e:
                    rec = {"uid": uid, "date": when, "error": repr(e)[:300]}
                    agg["erreurs"] += 1
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                out.flush()

        for i, uid in enumerate(todo, 1):
            try:
                raw = fetch_raw(m, uid)
            except Exception:
                m = _login()
                raw = fetch_raw(m, uid)
            inflight[pool.submit(parse_mail, raw, cities, client)] = uid
            while len(inflight) >= args.workers * 2:
                drain()
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} mails lancés, {n_items} cartes, {dict(agg)}, {time.time() - t0:.0f}s",
                      flush=True)
        while inflight:
            drain()
    m.logout()
    print(f"extract terminé : {len(todo)} mails, {n_items} cartes, statuts {dict(agg)}, {time.time() - t0:.0f}s",
          flush=True)


def apply(args):
    dry = args.dry_run
    if not dry and os.environ.get("DISABLE_ALERTS") != "1":
        sys.exit("refus : DISABLE_ALERTS=1 est obligatoire pour écrire (aucune alerte Discord pendant un rejeu)")
    recs = {}
    for line in open(args.inp):
        r = json.loads(line)
        if not r.get("error"):
            recs[r["uid"]] = r
    crit = settings.load_criteria()
    st, touched, ads = collections.Counter(), {}, {}
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client, db.connect() as conn:
        for uid in sorted(recs):
            rec = recs[uid]
            when = datetime.fromisoformat(rec["date"]) if rec.get("date") else datetime.now(timezone.utc)
            st["mails"] += 1
            if rec.get("failed"):
                st["mails avec liens de suivi non résolus"] += 1
            for it in rec["items"]:
                if it.get("source") != "seloger":
                    st["cartes d'une autre source (ignorées)"] += 1
                    continue
                ext = it["external_id"]
                buggy = old_id(it["url"]) != ext
                st["cartes"] += 1
                st["cartes à identifiant faux avant correctif" if buggy else "cartes à identifiant juste"] += 1
                ad = ads.get(ext)
                if ad is None:
                    r = conn.execute("SELECT id, first_seen, last_seen, jsonb_array_length(price_history) AS n "
                                     "FROM listing WHERE source='seloger' AND external_id=%s", (ext,)).fetchone()
                    ad = ads[ext] = {"id": r["id"] if r else None, "first": r and r["first_seen"],
                                     "last": r and r["last_seen"], "n": r["n"] if r else 0,
                                     "known": bool(r), "buggy": buggy}
                if ad["id"] is not None and ad["last"] > when:       # déjà vue plus récemment : ne rien toucher
                    st["cartes ignorées (annonce déjà vue plus récemment)"] += 1
                    continue
                st["cartes appliquées"] += 1
                if dry:
                    if ad["id"] is None:
                        ad["id"], ad["first"] = -1, when                # ligne fictive
                    ad["last"] = when
                    continue
                lid, inserted = upsert(conn, it)
                n_after = conn.execute("SELECT jsonb_array_length(price_history) AS n FROM listing WHERE id=%s",
                                       (lid,)).fetchone()["n"]
                conn.execute("UPDATE listing SET last_seen=%s, first_seen=" +
                             ("%s" if inserted else "LEAST(first_seen, %s)") + " WHERE id=%s", (when, when, lid))
                if n_after > ad["n"]:                                   # entrée d'historique ajoutée par upsert
                    conn.execute("UPDATE listing SET price_history = jsonb_set(price_history, "
                                 "ARRAY[(jsonb_array_length(price_history)-1)::text, 'date'], to_jsonb(%s::text)) "
                                 "WHERE id=%s", (when.isoformat(), lid))
                    st["changements de prix datés du mail"] += 1
                ad.update(id=lid, last=when, n=n_after, first=ad["first"] or when)
                touched[lid] = True
                if st["cartes appliquées"] % 200 == 0:
                    conn.commit()
        created = [e for e, a in ads.items() if not a["known"] and a["id"] is not None]
        st["annonces distinctes"] = len(ads)
        st["annonces déjà en base"] = sum(a["known"] for a in ads.values())
        st["annonces à créer" if dry else "annonces créées"] = len(created)
        st["  dont identifiant faux avant correctif"] = sum(ads[e]["buggy"] for e in created)
        st["  dont identifiant juste (cartes perdues pour une autre raison)"] = sum(not ads[e]["buggy"] for e in created)
        for watched in args.watch:
            print(f"annonce suivie {watched} :", {k: str(v) for k, v in ads.get(watched, {}).items()} or "absente des cartes",
                  flush=True)
        if not dry:
            conn.commit()
            ids = list(touched)
            if ids:
                from app.collector import postprocess, rescore
                print(f"{len(ids)} lignes touchées : postprocess (photos, géocodage, trajets)…", flush=True)
                failed_ids = []
                for i, lid in enumerate(ids, 1):
                    # le collecteur n'insiste pas (une ligne en échec reste sans photo/trajets pour toujours, car une
                    # annonce récupérée n'est plus « touchée ») : ici, 3 essais espacés
                    for attempt in range(3):
                        try:
                            postprocess(conn, client, crit, lid)
                            conn.commit()
                            break
                        except Exception as e:
                            conn.rollback()
                            if attempt == 2:
                                st["postprocess en échec"] += 1
                                failed_ids.append(lid)
                                print("  postprocess", lid, repr(e)[:160], flush=True)
                            else:
                                time.sleep(10 * (attempt + 1))
                    if i % 25 == 0:
                        print(f"  postprocess {i}/{len(ids)}", flush=True)
                if failed_ids:
                    print("lignes à retraiter (postprocess en échec) :", failed_ids, flush=True)
                st["alertes envoyées (doit rester 0)"] = rescore(conn, client, crit, ids=ids)
                conn.commit()
    print(("SIMULATION — " if dry else "") + "bilan :", flush=True)
    for k, v in st.items():
        print(f"  {k}: {v}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--out", required=True)
    e.add_argument("--from-uid", type=int, default=1)
    e.add_argument("--until-uid", type=int, default=0, help="0 = jusqu'au dernier mail")
    e.add_argument("--workers", type=int, default=3)
    e.add_argument("--sender", default="seloger", help="sous-chaîne de l'expéditeur")
    a = sub.add_parser("apply")
    a.add_argument("--in", dest="inp", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--watch", nargs="*", default=[], help="identifiants SeLoger à détailler dans le bilan")
    args = ap.parse_args()
    {"extract": extract, "apply": apply}[args.cmd](args)


if __name__ == "__main__":
    main()
