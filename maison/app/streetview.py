"""Vue de rue ouverte (design D9) : photo Panoramax la plus proche de la maison (360° ou plane orientée vers
elle), recadrée vers le bâtiment de l'annonce. Téléchargée seulement à l'ouverture d'une fiche ou à l'appel de
l'agent, puis mise en cache (clé = position arrondie, 180 jours)."""
import io
import json
import math
import threading
from datetime import datetime, timedelta, timezone
import numpy as np
from PIL import Image
from app import adjacency, settings
from app.aerial import centre_of
from app.scoring import merged_columns

SEARCH_URL = "https://api.panoramax.xyz/api/search"
VIEWER_URL = "https://api.panoramax.xyz/#focus=pic&pic={id}"
MAX_DIST_M = 30
FLAT_FOV_DEFAULT = 60          # photos planes sans champ déclaré (MEL 2023) : hypothèse prudente
OUT_W, OUT_H, HFOV, PITCH = 1200, 800, 90, 3
CACHE_DAYS, NONE_CACHE_DAYS = 180, 7
_LOCK = threading.Lock()       # une projection à la fois (≈ 57 Mo transitoires pour une photo 360°)


def target_of(row: dict) -> tuple[float, float] | None:
    """Point visé : centre du bâtiment retenu par le calcul cartographique, sinon la position exacte de l'annonce."""
    adj = row.get("adjacency") or {}
    if adj.get("status") in ("ok", "no_building") and adj.get("geometry"):
        c = centre_of(adj)
        if c:
            return c
    view = merged_columns(row) if row.get("cluster") else row
    if view.get("geo_precision") == "exact" and view.get("lat") is not None:
        return float(view["lon"]), float(view["lat"])
    return None


def _bearing(lon0, lat0, lon1, lat1) -> float:
    kx, ky = adjacency._scale((lat0 + lat1) / 2)
    return math.degrees(math.atan2((lon1 - lon0) * kx, (lat1 - lat0) * ky)) % 360


def _rel(a: float, b: float) -> float:
    return (a - b + 540) % 360 - 180


def choose(features: list[dict], lon: float, lat: float) -> dict | None:
    """Photo retenue : 360°, ou plane dont le champ contient la maison ; parmi celles à moins de 1,5 fois la
    distance minimale, la plus récente."""
    cands = []
    for f in features:
        p = f.get("properties") or {}
        try:
            plon, plat = f["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        dist = adjacency._dist_m(plon, plat, lon, lat)
        heading = p.get("view:azimuth")
        if dist > MAX_DIST_M or heading is None or not (f.get("assets") or {}).get("hd"):
            continue
        az = _bearing(plon, plat, lon, lat)
        fov = (p.get("pers:interior_orientation") or {}).get("field_of_view")
        if fov != 360:
            fov = fov or FLAT_FOV_DEFAULT
            if abs(_rel(az, heading)) > fov / 2:            # maison hors du champ de la photo plane
                continue
        cands.append({"feature": f, "distance_m": dist, "azimuth": az, "heading": heading, "fov": fov})
    if not cands:
        return None
    dmin = min(c["distance_m"] for c in cands)
    near = [c for c in cands if c["distance_m"] <= max(1.5 * dmin, dmin + 1)]
    return max(near, key=lambda c: (c["feature"]["properties"].get("datetime") or "", -c["distance_m"]))


def project_equirect(img: Image.Image, yaw: float, pitch: float = PITCH, hfov: float = HFOV,
                     w: int = OUT_W, h: int = OUT_H) -> Image.Image:
    """Vue plane extraite d'une photo équirectangulaire dont le centre est l'azimut de prise de vue ;
    `yaw` = azimut visé relatif à ce centre (plus proche voisin)."""
    width, height = img.size
    arr = np.asarray(img)
    f = (w / 2) / math.tan(math.radians(hfov / 2))
    xs, ys = np.meshgrid(np.arange(w) - w / 2, np.arange(h) - h / 2)
    x, y, z = xs, -ys, np.full(xs.shape, f, dtype=float)
    p = math.radians(pitch)
    y2, z2 = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)
    lon = np.arctan2(x, z2) + math.radians(yaw)
    lat = np.arctan2(y2, np.hypot(x, z2))
    u = ((lon / (2 * math.pi) + 0.5) % 1.0) * (width - 1)
    v = (0.5 - lat / math.pi) * (height - 1)
    return Image.fromarray(arr[v.astype(int).clip(0, height - 1), u.astype(int).clip(0, width - 1)])


def crop_flat(img: Image.Image, rel: float, fov: float) -> Image.Image:
    """Photo plane : recadrage 3:2 centré sur l'azimut visé, réduit à 1200 px de large au plus."""
    width, height = img.size
    fpx = (width / 2) / math.tan(math.radians(fov / 2))
    cx = width / 2 + fpx * math.tan(math.radians(rel))
    cw = min(width, int(height * 1.5))
    x0 = int(min(max(cx - cw / 2, 0), width - cw))
    out = img.crop((x0, 0, x0 + cw, height))
    if out.width > OUT_W:
        out = out.resize((OUT_W, round(out.height * OUT_W / out.width)))
    return out


def render(raw: bytes, cand: dict) -> Image.Image:
    im = Image.open(io.BytesIO(raw))
    rel = _rel(cand["azimuth"], cand["heading"])
    if cand["fov"] == 360:
        im.draft("RGB", (im.width // 2, im.height // 2))     # décodage JPEG à demi-résolution
        return project_equirect(im.convert("RGB"), rel)
    return crop_flat(im.convert("RGB"), rel, cand["fov"])


def _key(lon: float, lat: float) -> str:
    return f"{lon:.5f},{lat:.5f}"


def _meta(cand: dict) -> dict:
    f = cand["feature"]
    p = f["properties"]
    return {"available": True, "photo_id": f.get("id"), "date": (p.get("datetime") or "")[:10] or None,
            "distance_m": round(cand["distance_m"], 1), "kind": "360" if cand["fov"] == 360 else "plane",
            "author": p.get("geovisio:producer") or (p.get("providers") or [{}])[0].get("name"),
            "license": p.get("license"), "viewer_url": VIEWER_URL.format(id=f.get("id"))}


def street_view(client, lid: int, lon: float, lat: float) -> tuple[dict, bytes | None]:
    """(métadonnées, JPEG) de la vue de rue d'une annonce, depuis le cache si la position n'a pas changé."""
    folder = settings.DATA_DIR / "streetview"
    meta_f, img_f = folder / f"{lid}.json", folder / f"{lid}.jpg"
    key, now = _key(lon, lat), datetime.now(timezone.utc)
    try:
        meta = json.loads(meta_f.read_text())
        age = now - datetime.fromisoformat(meta["fetched_at"])
        ttl = timedelta(days=CACHE_DAYS if meta.get("available") else NONE_CACHE_DAYS)
        if meta.get("key") == key and age < ttl and (not meta.get("available") or img_f.exists()):
            return meta, img_f.read_bytes() if meta.get("available") else None
    except (OSError, ValueError, KeyError):
        pass
    with _LOCK:
        found = adjacency.request(client, SEARCH_URL, {"place_position": f"{lon},{lat}",
                                                       "place_distance": f"0-{MAX_DIST_M}", "limit": 20},
                                  parse=lambda r: r.json())
        cand = choose(found.get("features") or [], lon, lat)
        folder.mkdir(parents=True, exist_ok=True)
        if not cand:
            meta = {"available": False, "reason": "aucune vue de rue", "key": key, "fetched_at": now.isoformat()}
            meta_f.write_text(json.dumps(meta))
            img_f.unlink(missing_ok=True)
            return meta, None
        raw = adjacency.request(client, cand["feature"]["assets"]["hd"]["href"], {}, parse=lambda r: r.content,
                                follow_redirects=True)                # stockage objet derrière une redirection
        out = io.BytesIO()
        render(raw, cand).save(out, "JPEG", quality=85)
        data = out.getvalue()
        meta = {**_meta(cand), "key": key, "fetched_at": now.isoformat()}
        img_f.write_bytes(data)
        meta_f.write_text(json.dumps(meta, ensure_ascii=False))
        return meta, data
