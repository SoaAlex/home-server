from app import settings
from app.scoring import SOURCE_LABELS as TYPE_SOURCES, effective, resolve_type


def should_alert(row: dict, crit: dict) -> bool:
    a, r = crit["alert"], effective(row)
    if row.get("merged_into"):          # source secondaire d'un bien fusionné : l'alerte porte sur la principale
        return False
    if row.get("alerted_at") or row.get("excluded_reason") or (row.get("score") or 0) < a["min_score"]:
        return False
    if row.get("status") == "gone" or row.get("reaction") == "dislike":
        return False
    if (r.get("price") or 10**9) > a["max_price"]:
        return False
    if a.get("require_confirmed_garage_garden") and not (r.get("has_garage") and r.get("has_garden")):
        return False
    return True


def _fmt_price(p):
    return f"{p:,} €".replace(",", " ") if p else "?"


SOURCE_LABELS = {"bienici": "Bien'ici", "seloger": "SeLoger", "leboncoin": "LeBonCoin", "pap": "PAP",
                 "netty": "Netty", "maes": "Maes", "notaires": "Notaires", "agences": "Agence"}


def best_url(row: dict) -> str:
    """Lien de l'offre active la moins chère d'un bien multi-sources (sinon l'annonce elle-même)."""
    cl = row.get("cluster") or {}
    oid = (cl.get("price") or {}).get("offer_id")
    return next((o["url"] for o in cl.get("offers", []) if o["id"] == oid and o.get("url")), row["url"])


def build_embed(row: dict, crit: dict | None = None) -> dict:
    r = effective(row)
    t = resolve_type(row)
    c = r.get("commute") or {}
    dests = (crit or {}).get("commute", {}).get("destinations", {})
    trips = " · ".join(f"{dests.get(k, {}).get('short', k)} {v} min"
                       for k, v in c.items() if k != "total_min" and v is not None) or "?"
    fields = [{"name": k, "value": str(v), "inline": True} for k, v in [
        ("Prix", _fmt_price(r.get("price"))), ("Ville", r.get("city") or "?"),
        ("Surface", f"{r.get('surface_m2') or '?'} m²"), ("Terrain", f"{r.get('land_m2') or '?'} m²"),
        ("Type", t["label"] + (f" ({TYPE_SOURCES[t['source']]})" if t["source"] else "")),
        ("DPE", r.get("dpe") or "?"), ("Trajets", trips)]]
    offers = (row.get("cluster") or {}).get("offers") or []
    if len(offers) > 1:
        fields.append({"name": f"Sources ({len(offers)})", "inline": False, "value": " · ".join(
            f"{SOURCE_LABELS.get(o['source'], o['source'])} {_fmt_price(o.get('price'))}"
            + ("" if o["status"] == "active" else " (retirée)" if o["status"] == "gone" else " (non confirmée)")
            for o in offers)[:1000]})
    embed = {"title": f"🏡 {row.get('score')}/100 — {r.get('title') or 'Maison'}"[:250],
             "url": best_url(row), "fields": fields,
             "description": f"[Fiche privée]({settings.PUBLIC_BASE_URL}/#/l/{row['id']})"}
    photos = row.get("photo_urls") or []
    if photos:
        embed["image"] = {"url": photos[0]}
    return embed


def send_discord(client, webhook: str, row: dict, crit: dict | None = None) -> None:
    client.post(webhook, json={"embeds": [build_embed(row, crit)]}, timeout=20).raise_for_status()
