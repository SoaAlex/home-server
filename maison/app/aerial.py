"""Vue aérienne annotée (design D10) : orthophotographie IGN de 70 × 70 m, tracés de la mitoyenneté calculée
(parcelle, bâtiment retenu, propriété, voisins accolés, murs communs) et légende."""
import io
import re
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from app import adjacency, settings

WMS_URL = "https://data.geopf.fr/wms-r/wms"
SIZE_PX = 700
SPAN_M = 70
COLOURS = {"parcel": (255, 255, 255), "target": (30, 100, 255), "property": (0, 225, 235),
           "neighbour_main": (255, 35, 35), "neighbour_annex": (255, 150, 0), "shared_wall": (255, 0, 255)}
LEGEND = (("parcel", "parcelle"), ("target", "bâtiment de l'annonce"), ("property", "même propriété"),
          ("neighbour_main", "voisin accolé : habitation"), ("neighbour_annex", "voisin accolé : annexe"),
          ("shared_wall", "mur commun"))
FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",)
ORDER = {"parcel": 0, "neighbour_annex": 1, "neighbour_main": 2, "property": 3, "target": 4, "shared_wall": 5}


def box(lon: float, lat: float, span_m: float = SPAN_M) -> tuple:
    return adjacency._bbox(lon, lat, span_m / 2)


def ortho(client, bbox) -> Image.Image:
    lon0, lat0, lon1, lat1 = bbox
    params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": "ORTHOIMAGERY.ORTHOPHOTOS",
              "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{lat0:.7f},{lon0:.7f},{lat1:.7f},{lon1:.7f}",  # lat, lon
              "WIDTH": SIZE_PX, "HEIGHT": SIZE_PX, "FORMAT": "image/jpeg"}

    def parse(r):
        try:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        except UnidentifiedImageError as e:       # erreur WMS renvoyée en XML avec un statut 200
            raise ValueError("pas une image") from e
    img = adjacency.request(client, WMS_URL, params, parse=parse)
    return img if img.size == (SIZE_PX, SIZE_PX) else img.resize((SIZE_PX, SIZE_PX))


def centre_of(adj: dict) -> tuple[float, float] | None:
    """Centre de la vue : centroïde du bâtiment retenu, sinon le point d'entrée du calcul."""
    for f in (adj.get("geometry") or {}).get("features", []):
        if f["properties"].get("role") == "target":
            ring = f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" \
                else f["geometry"]["coordinates"][0][0]
            pts = ring[:-1] or ring
            return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    inp = adj.get("input") or {}
    return (inp["lon"], inp["lat"]) if inp.get("lon") is not None and inp.get("lat") is not None else None


def _rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return [("poly", r) for r in c[:1]]
    if t == "MultiPolygon":
        return [("poly", p[0]) for p in c]
    if t == "LineString":
        return [("line", c)]
    if t == "MultiLineString":
        return [("line", x) for x in c]
    if t == "GeometryCollection":
        return [x for g in geom["geometries"] for x in _rings(g)]
    return []


def render(client, adj: dict) -> bytes:
    """JPEG 700 × 700 : orthophoto récente + tracés de `adj["geometry"]` + légende."""
    centre = centre_of(adj)
    if centre is None:
        raise ValueError("vue aérienne : aucune position")
    bbox = box(*centre)
    lon0, lat0, lon1, lat1 = bbox
    img = ortho(client, bbox).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    def px(c):
        return ((c[0] - lon0) / (lon1 - lon0) * SIZE_PX, (lat1 - c[1]) / (lat1 - lat0) * SIZE_PX)
    feats = sorted((adj.get("geometry") or {}).get("features", []), key=lambda f: ORDER.get(f["properties"]["role"], 9))
    for f in feats:
        role = f["properties"]["role"]
        col = COLOURS.get(role, (255, 255, 0))
        for kind, coords in _rings(f["geometry"]):
            pts = [px(c) for c in coords]
            if kind == "line":
                d.line(pts, fill=col + (255,), width=7, joint="curve")
            elif role == "parcel":
                d.line(pts, fill=col + (230,), width=2)
            else:
                d.polygon(pts, fill=col + (80,))
                d.line(pts, fill=col + (255,), width=3)
    _legend(d)
    out = io.BytesIO()
    Image.alpha_composite(img, over).convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue()


def font(size: int):
    """DejaVu Sans (paquet fonts-dejavu-core de l'image) ; à défaut, la police intégrée de Pillow (sans accents)."""
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _legend(d):
    font_ = font(14)
    lines = list(LEGEND) + [(None, "tracés BD TOPO ±3 m · orthophoto © IGN")]
    x0, y0, h = 10, SIZE_PX - 12 - 22 * len(lines), 22
    d.rectangle((x0 - 6, y0 - 8, x0 + 318, SIZE_PX - 6), fill=(0, 0, 0, 165))
    for i, (role, text) in enumerate(lines):
        y = y0 + i * h
        if role:
            col = COLOURS[role]
            if role == "shared_wall":
                d.line((x0, y + 8, x0 + 22, y + 8), fill=col + (255,), width=6)
            elif role == "parcel":
                d.rectangle((x0, y + 1, x0 + 22, y + 15), outline=col + (255,), width=2)
            else:
                d.rectangle((x0, y + 1, x0 + 22, y + 15), fill=col + (140,), outline=col + (255,), width=2)
        d.text((x0 + (32 if role else 0), y), text, font=font_, fill=(255, 255, 255, 255))


def cached(client, lid: int, adj: dict) -> bytes:
    """Vue d'une annonce, mise en cache dans data/aerial/ et invalidée par un nouveau calcul (`computed_at`)."""
    folder = settings.DATA_DIR / "aerial"
    key = re.sub(r"\D", "", adj.get("computed_at") or "")[:20] or "0"
    path = folder / f"{lid}_{key}.jpg"
    if path.exists():
        return path.read_bytes()
    data = render(client, adj)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{lid}_*.jpg"):
        old.unlink(missing_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data
