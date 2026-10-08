import json
from app.scoring import fingerprint

FIELDS = ("url", "title", "description", "price", "surface_m2", "land_m2", "rooms", "bedrooms",
          "property_type", "city", "postal_code", "insee", "dpe", "ges", "dpe_value", "dpe_date",
          "year_built", "has_garage", "has_garden", "has_bathtub", "works", "agency", "lat", "lon",
          "geo_precision", "published_at")


def upsert(conn, item: dict) -> tuple[int, bool]:
    vals = {f: item.get(f) for f in FIELDS}
    vals["photo_urls"] = json.dumps(item.get("photo_urls") or []) if item.get("photo_urls") else None
    vals["raw"] = json.dumps(item.get("raw")) if item.get("raw") is not None else None
    vals["fingerprint"] = fingerprint(item)
    cols = ["source", "external_id", *vals.keys()]
    params = {"source": item["source"], "external_id": str(item["external_id"]), **vals}
    insert_vals = ", ".join(
        "COALESCE(%(photo_urls)s::jsonb, '[]'::jsonb)" if c == "photo_urls" else f"%({c})s" for c in cols)
    # Ne pas écraser une valeur connue par NULL (les e-mails donnent des fiches partielles)
    updates = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, listing.{c})" for c in vals
                        if c not in ("price", "photo_urls"))
    sql = f"""
    INSERT INTO listing ({", ".join(cols)}) VALUES ({insert_vals})
    ON CONFLICT (source, external_id) DO UPDATE SET
      {updates},
      photo_urls = CASE WHEN %(photo_urls)s::jsonb IS NULL THEN listing.photo_urls ELSE EXCLUDED.photo_urls END,
      price_history = CASE WHEN EXCLUDED.price IS NOT NULL AND listing.price IS NOT NULL
                           AND listing.price <> EXCLUDED.price
                      THEN listing.price_history || jsonb_build_array(
                           jsonb_build_object('date', now(), 'price', listing.price))
                      ELSE listing.price_history END,
      price = COALESCE(EXCLUDED.price, listing.price),
      last_seen = now(), status = 'active'
    RETURNING id, (xmax = 0) AS inserted"""
    row = conn.execute(sql, params).fetchone()
    return row["id"], row["inserted"]


def mark_gone(conn, source: str, seen_ids: set[str]) -> int:
    cur = conn.execute(
        "UPDATE listing SET status='gone' WHERE source=%s AND status='active' "
        "AND last_seen < now() - interval '1 hour' AND NOT (external_id = ANY(%s))",
        (source, list(seen_ids)))
    return cur.rowcount
