"""Vue de rue Panoramax (spec photo-adjacency-review, design D9) : choix de la photo, recadrage vers la maison,
cache. Réponses Panoramax simulées, panorama synthétique (aucun appel réseau)."""
import io
import httpx
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from app import adjacency, db, settings, streetview
from app.ingest import upsert
from tests.adjacency_helpers import building, house, ll, plot, scene

RED = (230, 20, 20)


def panorama(rel_deg: float, size=(720, 360)) -> bytes:
    """Équirectangulaire gris, bande rouge à l'azimut `rel_deg` (relatif au centre = direction de prise de vue)."""
    w, h = size
    img = Image.new("RGB", size, (120, 120, 120))
    u = (rel_deg / 360 + 0.5) * (w - 1)
    ImageDraw.Draw(img).rectangle((u - 12, 0, u + 12, h), fill=RED)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    return out.getvalue()


def photo(pid, xy, heading, fov=360, date="2026-09-22T10:00:00Z"):
    lon, lat = ll(*xy)
    return {"type": "Feature", "id": pid, "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"datetime": date, "view:azimuth": heading, "license": "etalab-2.0",
                           "geovisio:producer": "métropole européenne de lille",
                           "pers:interior_orientation": {"field_of_view": fov} if fov else {}},
            "assets": {"hd": {"href": f"https://panoramax.example/{pid}/hd.jpg"}}}


def test_choose_keeps_a_360_and_drops_a_flat_photo_facing_away():
    tlon, tlat = ll(3, 4.5)
    away = photo("plane", (3, -8), heading=180, fov=None)              # regarde vers le sud, la maison est au nord
    assert streetview.choose([away], tlon, tlat) is None
    facing = photo("plane2", (3, -8), heading=10, fov=None)
    assert streetview.choose([away, facing], tlon, tlat)["feature"]["id"] == "plane2"
    far = photo("loin", (3, -40), heading=0)
    assert streetview.choose([far], tlon, tlat) is None                 # à plus de 30 m
    old, new = photo("ancienne", (3, -8), 0, date="2021-01-01T00:00:00Z"), photo("récente", (4, -8.5), 0)
    assert streetview.choose([old, new], tlon, tlat)["feature"]["id"] == "récente"


def test_projection_points_the_view_at_the_house():
    img = Image.open(io.BytesIO(panorama(-90)))
    view = streetview.project_equirect(img, yaw=-90, w=300, h=200)
    r, g, b = view.getpixel((150, 100))
    assert r > 180 and g < 80 and b < 80
    r, g, b = streetview.project_equirect(img, yaw=0, w=300, h=200).getpixel((150, 100))
    assert abs(r - g) < 30                                              # visée ailleurs : gris


@pytest.fixture
def web(dburl, monkeypatch, tmp_path):
    from app import web as webmod
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(adjacency, "fetch", lambda c, point, address=None: scene(
        [house(), building("L", -6, 0, 0, 9)], plot() + plot("PL", -6, 0)))
    lon, lat = ll(3, -2)
    with db.connect(dburl) as conn:
        lid, _ = upsert(conn, dict(source="bienici", external_id="rue", url="https://e/rue", city="Wambrechies",
                                   lat=lat, lon=lon, geo_precision="exact", address="1 rue de l'Exemple"))
        conn.execute("""UPDATE listing SET enrichment='{"address_confidence": "exact"}' WHERE id=%s""", (lid,))
        adjacency.process(conn, None)
        city, _ = upsert(conn, dict(source="seloger", external_id="ville", url="https://e/ville", city="Wambrechies",
                                    lat=50.68, lon=3.05, geo_precision="city"))
    calls = {"search": 0, "hd": 0}
    features = [photo("pano", (3, -8), heading=90)]       # maison plein nord, photo orientée vers l'est : -90°

    def handler(req):
        if req.url.host == "api.panoramax.xyz":
            calls["search"] += 1
            return httpx.Response(200, json={"features": features})
        assert req.url.host == "panoramax.example"
        calls["hd"] += 1
        return httpx.Response(200, content=panorama(-90))
    real = httpx.Client
    monkeypatch.setattr(webmod.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    with TestClient(webmod.app) as c:
        c.lid, c.city, c.calls, c.features = lid, city, calls, features
        yield c


def test_street_view_of_a_listing_is_centred_on_the_house_and_cached(web):
    meta = web.get(f"/api/listings/{web.lid}/street").json()
    assert meta["available"] and meta["date"] == "2026-09-22" and meta["kind"] == "360"
    assert meta["author"] == "métropole européenne de lille" and meta["license"] == "etalab-2.0"
    assert meta["viewer_url"].endswith("pic=pano") and 12 < meta["distance_m"] < 13
    r = web.get(meta["image_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    assert img.size == (1200, 800)
    red, green, blue = img.getpixel((600, 400))
    assert red > 180 and green < 80 and blue < 80                       # la maison est au centre de la vue
    web.get(f"/api/listings/{web.lid}/street")
    assert web.calls == {"search": 1, "hd": 1}                          # second appel : servi par le cache


def test_no_street_view_when_only_a_photo_facing_away_exists(web):
    web.features[:] = [photo("plane", (3, -8), heading=180, fov=None)]
    meta = web.get(f"/api/listings/{web.lid}/street").json()
    assert meta == {"available": False, "reason": "aucune vue de rue"}
    assert web.get(f"/api/listings/{web.lid}/street.jpg").status_code == 404 and web.calls["hd"] == 0


def test_no_street_view_for_an_imprecise_position(web):
    assert web.get(f"/api/listings/{web.city}/street").json() == {"available": False, "reason": "maison non localisée"}
    assert web.calls["search"] == 0
