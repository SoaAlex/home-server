from app import settings
from app.scoring import effective


def should_alert(row: dict, crit: dict) -> bool:
    a, r = crit["alert"], effective(row)
    if row.get("alerted_at") or row.get("excluded_reason") or (row.get("score") or 0) < a["min_score"]:
        return False
    if row.get("status") == "gone":
        return False
    if (r.get("price") or 10**9) > a["max_price"]:
        return False
    if a.get("require_confirmed_garage_garden") and not (r.get("has_garage") and r.get("has_garden")):
        return False
    return True


def _fmt_price(p):
    return f"{p:,} €".replace(",", " ") if p else "?"


def build_embed(row: dict, crit: dict | None = None) -> dict:
    r = effective(row)
    c = row.get("commute") or {}
    dests = (crit or {}).get("commute", {}).get("destinations", {})
    trips = " · ".join(f"{dests.get(k, {}).get('short', k)} {v} min"
                       for k, v in c.items() if k != "total_min" and v is not None) or "?"
    fields = [{"name": k, "value": str(v), "inline": True} for k, v in [
        ("Prix", _fmt_price(r.get("price"))), ("Ville", r.get("city") or "?"),
        ("Surface", f"{r.get('surface_m2') or '?'} m²"), ("Terrain", f"{r.get('land_m2') or '?'} m²"),
        ("DPE", r.get("dpe") or "?"), ("Trajets", trips)]]
    embed = {"title": f"🏡 {row.get('score')}/100 — {r.get('title') or 'Maison'}"[:250],
             "url": row["url"], "fields": fields,
             "description": f"[Fiche privée]({settings.PUBLIC_BASE_URL}/#/l/{row['id']})"}
    photos = row.get("photo_urls") or []
    if photos:
        embed["image"] = {"url": photos[0]}
    return embed


def send_discord(client, webhook: str, row: dict, crit: dict | None = None) -> None:
    client.post(webhook, json={"embeds": [build_embed(row, crit)]}, timeout=20).raise_for_status()
