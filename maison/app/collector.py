import argparse
import logging
import os
import random
import time
import traceback
from datetime import datetime
import httpx
from psycopg.types.json import Jsonb
from app import db, settings, geo, photos, alerts, dedupe
from app.ingest import upsert, mark_gone
from app.scoring import score_listing
from app.sources import bienici, notaires, maes, netty, agences, pap, email_alerts

log = logging.getLogger("collector")
FULL_SWEEP = {"bienici": bienici, "notaires": notaires, "maes": maes, "netty": netty, "agences": agences,
              "pap": pap}
PENDING_OK = ("à compléter",)   # exclusions « provisoires » : l'agent Hermes doit compléter


def alerts_enabled() -> bool:
    return bool(settings.DISCORD_WEBHOOK_URL) and os.environ.get("DISABLE_ALERTS") != "1"


def rescore(conn, client, crit, ids=None):
    # Annonces principales seulement (les secondaires d'un bien fusionné n'ont ni score ni alerte) ;
    # un bien reste actif tant qu'une de ses offres l'est (cluster.active).
    q = ("SELECT * FROM listing WHERE merged_into IS NULL "
         "AND (status='active' OR coalesce((cluster->>'active')::boolean, false))" + (" AND id = ANY(%s)" if ids else ""))
    sent = 0
    for row in conn.execute(q, (list(ids),) if ids else ()).fetchall():
        s, d, why = score_listing(row, crit)
        enr_status = row["enrichment_status"]
        if why and why not in PENDING_OK and enr_status == "pending":
            enr_status = "skipped"
        elif not why and enr_status == "skipped":
            enr_status = "pending"
        conn.execute("UPDATE listing SET score=%s, score_details=%s, excluded_reason=%s, "
                     "enrichment_status=%s WHERE id=%s",
                     (s, Jsonb(d), why, enr_status, row["id"]))
        row.update(score=s, score_details=d, excluded_reason=why)
        if alerts_enabled() and alerts.should_alert(row, crit):
            dup = row["fingerprint"] and conn.execute(
                "SELECT 1 FROM listing WHERE fingerprint=%s AND alerted_at IS NOT NULL AND id<>%s",
                (row["fingerprint"], row["id"])).fetchone()
            if not dup:
                try:
                    alerts.send_discord(client, settings.DISCORD_WEBHOOK_URL, row, crit)
                    sent += 1
                    time.sleep(1)
                except Exception:
                    log.warning("discord: %s", traceback.format_exc(limit=1))
                    continue
            conn.execute("UPDATE listing SET alerted_at=now() WHERE id=%s", (row["id"],))
    return sent


def postprocess(conn, client, crit, lid):
    """Photos, géocodage, adresse via DPE ADEME, trajets. Idempotent."""
    row = conn.execute("SELECT * FROM listing WHERE id=%s", (lid,)).fetchone()
    _, _, why = score_listing(row, crit)
    if why and why not in PENDING_OK:
        return
    upd = {}
    if not row["photos"] and row["photo_urls"]:
        base, hashes = settings.DATA_DIR / "photos", {}
        paths = photos.download(client, lid, row["photo_urls"], base, limit=10, hashes=hashes)
        upd["photos"] = Jsonb(paths)
        for idx, h in hashes.items():           # empreintes calculées en mémoire au téléchargement
            f = base / f"{lid}/{idx:02d}.jpg"
            st = f.stat()
            dedupe.store_hash(conn, lid, idx, f"{lid}/{idx:02d}.jpg", h, st.st_size, st.st_mtime)
    if why:     # « à compléter » (carte e-mail sans commune) : seulement la vignette, pour la déduplication
        if upd:
            conn.execute("UPDATE listing SET photos=%s WHERE id=%s", (upd["photos"], lid))
        return
    insee = row["insee"]
    if (row["address"] and row["city"] and row["geo_precision"] != "exact"
            and (row["lat"] is None or row["geo_precision"] in (None, "city"))):
        # adresse de rue fournie par la source (PAP) : position au niveau rue/numéro si la BAN reconnaît
        gs = geo.geocode(client, row["address"], citycode=insee)
        if gs and gs["type"] in ("housenumber", "street"):
            upd.update(lat=gs["lat"], lon=gs["lon"],
                       geo_precision="exact" if gs["type"] == "housenumber" else "approx",
                       commute=None)
    if (not insee or row["lat"] is None) and row["city"]:
        g = geo.geocode(client, f"{row['city']} {row['postal_code'] or ''}".strip(), kind="municipality")
        if g:
            if not insee:
                insee = upd["insee"] = g["citycode"]
            if row["lat"] is None and upd.get("lat") is None:
                upd.update(lat=g["lat"], lon=g["lon"], geo_precision="city")
    enr = row["enrichment"] or {}
    if insee and not row["address"] and not enr.get("ademe_tried") and (row["dpe_value"] or row["dpe_date"]):
        a = geo.ademe_address(client, insee, row["dpe"], row["surface_m2"] and float(row["surface_m2"]),
                              row["dpe_date"], row["dpe_value"])
        flags = {"ademe_tried": True}
        if a:
            upd.update(address=a["address"], lat=a["lat"], lon=a["lon"], geo_precision="exact",
                       commute=None)
            flags.update(address_confidence="probable", address_method="ademe_dpe")
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s", (Jsonb(flags), lid))
    lat, lon = upd.get("lat", row["lat"]), upd.get("lon", row["lon"])
    if lat and lon and (not row["commute"] or "commute" in upd):
        upd["commute"] = Jsonb(geo.commute(client, lat, lon, crit["commute"]["destinations"]))
    if upd:
        sets = ", ".join(f"{k}=%({k})s" for k in upd)
        conn.execute(f"UPDATE listing SET {sets} WHERE id=%(id)s", {**upd, "id": lid})


def run_once():
    crit = settings.load_criteria()
    touched = []
    with httpx.Client(headers={"User-Agent": settings.USER_AGENT}, timeout=30) as client, db.connect() as conn:
        known = {"maes": {}, "netty": {}, "agences": {}, "pap": {}}
        for r in conn.execute("SELECT source, external_id, url, raw FROM listing "
                              "WHERE source IN ('maes','netty','agences','pap') AND status='active'").fetchall():
            raw = r["raw"] or {}
            known[r["source"]][r["external_id"]] = raw.get("modified") if r["source"] == "maes" else {**raw, "url": r["url"]}
        for name, mod in FULL_SWEEP.items():
            try:
                items = mod.fetch(client, crit, known=known.get(name))
                seen = set()
                for it in items:
                    lid, _ = upsert(conn, it)
                    seen.add(str(it["external_id"]))
                    touched.append(lid)
                conn.commit()
                if items:
                    gone = mark_gone(conn, name, seen)
                    conn.commit()
                    log.info("%s: %d annonces (%d disparues)", name, len(items), gone)
                else:
                    log.warning("%s: 0 annonce (source cassée ?)", name)
            except Exception:
                conn.rollback()
                log.error("source %s en échec\n%s", name, traceback.format_exc())
        try:
            mails = email_alerts.fetch(client, crit)
            for it in mails:
                touched.append(upsert(conn, it)[0])
            conn.commit()
            log.info("emails: %d annonces", len(mails))
        except Exception:
            conn.rollback()
            log.error("emails en échec\n%s", traceback.format_exc())
        for lid in dict.fromkeys(touched):
            try:
                postprocess(conn, client, crit, lid)
                conn.commit()
            except Exception:
                conn.rollback()
                log.warning("postprocess %s: %s", lid, traceback.format_exc(limit=2))
        try:        # doublons : avant le score et les alertes, pour qu'une copie ne soit ni alertée ni enrichie
            st = dedupe.process(conn, list(dict.fromkeys(touched)))
            conn.commit()
            if "skipped" in st:
                log.warning("doublons: étape ignorée (%s)", st["skipped"])
            else:
                log.info("doublons: %d annonces comparées, %d fusions, %d suggestions, %d photos génériques, "
                         "%d empreintes en %.2fs", st["compared"], st["merged"], st["suggested"], st["generic"],
                         st["hashed"], st["elapsed_s"])
        except Exception:
            conn.rollback()
            log.error("doublons en échec\n%s", traceback.format_exc())
        sent = rescore(conn, client, crit)
        conn.commit()
        log.info("alertes envoyées: %d", sent)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    db.init_schema()
    while True:
        started = datetime.now()
        try:
            run_once()
        except Exception:
            log.error("cycle en échec\n%s", traceback.format_exc())
        log.info("cycle terminé en %ss", (datetime.now() - started).seconds)
        if args.once:
            return
        time.sleep(3600 + random.randint(0, 600))


if __name__ == "__main__":
    main()
