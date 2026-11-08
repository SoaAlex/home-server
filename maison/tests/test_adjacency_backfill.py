"""Rattrapage de la mitoyenneté (scripts/adjacency_backfill.py) : à blanc sans modification, application
relançable, demandes de vérification idempotentes. Récupération réseau simulée, aucune donnée réelle."""
import importlib.util
import io
from pathlib import Path

from PIL import Image
from psycopg.types.json import Jsonb

from app import adjacency, aerial, db, settings
from app.ingest import upsert

CRIT = {"price": {"max": 550000, "target": 450000}, "require": {"garage": True, "garden": True},
        "preferred_cities": ["Ville A"], "other_cities": [], "excluded_cities": [],
        "commute": {"destinations": {}}, "alert": {"min_score": 10, "max_price": 600000,
                                                   "require_confirmed_garage_garden": False}}


def load_script():
    spec = importlib.util.spec_from_file_location(
        "adjacency_backfill", Path(__file__).resolve().parent.parent / "scripts" / "adjacency_backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ok_result(typ, lon, lat, address, confidence):
    walls = {"terraced": 2, "semi": 1}.get(typ, 0)
    return {"v": 1, "status": "ok", "type": typ, "usable": True, "warnings": [], "computed_at": "2026-11-08T10:00:00+00:00",
            "input": {"address": address, "lat": lat, "lon": lon, "confidence": confidence, "ban_id": "x"},
            "neighbours": [{"kind": "living", "side": s, "length_m": 9.0, "ours": "main", "theirs": "main"}
                           for s in ("left", "right")[:walls]],
            "living_walls": walls, "annex_links": 0,
            "geometry": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"role": "target"},
                 "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + 1e-4, lat], [lon + 1e-4, lat + 1e-4],
                                                                   [lon, lat + 1e-4], [lon, lat]]]}}]}}


class FakeCompute:
    """Mitoyenne pour « 1 rue A », échec une fois pour « 2 rue B » puis individuelle."""
    def __init__(self):
        self.calls, self.failed = [], set()

    def __call__(self, client, lon, lat, address=None, confidence=None):
        self.calls.append(address)
        if address.startswith("2 ") and address not in self.failed:
            self.failed.add(address)
            return {"v": 1, "status": "error", "error": "504", "tried_at": adjacency._now().isoformat(),
                    "input": {"address": address, "lat": lat, "lon": lon, "confidence": confidence}}
        return ok_result("terraced" if address.startswith("1 ") else "detached", lon, lat, address, confidence)


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def seed(dburl):
    with db.connect(dburl) as conn:
        def add(ext, **kw):
            lid, _ = upsert(conn, {"source": "bienici", "external_id": ext, "url": f"https://x/{ext}", "city": "Ville A",
                                   "price": 300000, "surface_m2": 100, **kw})
            return lid
        a = add("a", address="1 rue A", lat=50.6, lon=3.0, geo_precision="exact", has_garage=True, has_garden=True)
        b = add("b", address="2 rue B", lat=50.61, lon=3.01, geo_precision="exact", has_garage=False)
        c = add("c", source="seloger", description="Belle maison, cuisine indépendante, jardin.",
                lat=50.62, lon=3.02, geo_precision="city")
        # état « avant le change » : ancien verdict de l'agent, ancien texte « indépendante » = individuelle
        conn.execute("UPDATE listing SET enrichment=%s, score=60 WHERE id=%s",
                     (Jsonb({"address_confidence": "exact", "property_type": "detached", "has_garage": True}), a))
        conn.execute("UPDATE listing SET enrichment=%s, score=50 WHERE id=%s", (Jsonb({"address_confidence": "exact"}), b))
        conn.execute("UPDATE listing SET property_type='detached', property_type_src=NULL, score=40 WHERE id=%s", (c,))
    return a, b, c


def snapshot(dburl):
    with db.connect(dburl) as conn:
        return conn.execute("SELECT md5(string_agg(t::text, '|' ORDER BY id)) AS h FROM listing t").fetchone()["h"]


def env(monkeypatch, dburl, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "https://discord.invalid/x")   # doit rester inutilisé
    monkeypatch.setattr(settings, "load_criteria", lambda *a: CRIT)
    fake = FakeCompute()
    monkeypatch.setattr(adjacency, "compute", fake)
    monkeypatch.setattr(aerial, "render", lambda client, adj: jpeg())
    from app import alerts
    monkeypatch.setattr(alerts, "send_discord", lambda *a, **k: (_ for _ in ()).throw(AssertionError("alerte envoyée")))
    return fake


def test_dry_run_modifies_nothing_and_reports(dburl, tmp_path, monkeypatch):
    env(monkeypatch, dburl, tmp_path)
    a, b, c = seed(dburl)
    before = snapshot(dburl)
    out = tmp_path / "r.html"
    stats = load_script().main(["--report", str(out), "--workers", "2"])
    assert snapshot(dburl) == before                                   # aucune ligne `listing` modifiée
    assert stats["annonces à calculer"] == 2 and stats["calculs en échec"] == 1
    assert stats["verdicts « annonce » recalculés (texte)"] >= 1          # « cuisine indépendante » → inconnu
    page = out.read_text()
    assert "Effectifs du calcul cartographique" in page and "Mitoyenne" in page
    assert "Confusion : type actuel" in page and "Variations de score" in page
    assert "Franchissements du seuil d'alerte (10)" in page
    assert f"<b>#{a}</b>" in page and "data:image/jpeg;base64," in page   # désaccord illustré (agent ≠ carte)


def test_apply_then_second_apply_recomputes_only_failures(dburl, tmp_path, monkeypatch):
    fake = env(monkeypatch, dburl, tmp_path)
    a, b, c = seed(dburl)
    script = load_script()
    first = script.main(["--apply", "--no-report", "--workers", "1"])
    assert first["annonces à calculer"] == 2
    with db.connect(dburl) as conn:
        rows = {r["id"]: r for r in conn.execute("SELECT id, adjacency, property_type, property_type_src, score "
                                                 "FROM listing").fetchall()}
    assert rows[a]["adjacency"]["type"] == "terraced" and rows[b]["adjacency"]["status"] == "error"
    assert rows[c]["adjacency"]["status"] == "not_applicable"
    assert rows[c]["property_type"] is None and rows[c]["property_type_src"] is None     # « indépendante » oublié
    assert rows[a]["score"] != 60                                                        # rescore sans alerte
    fake.calls.clear()
    second = script.main(["--apply", "--no-report", "--workers", "1"])
    assert second["annonces à calculer"] == 1 and fake.calls == ["2 rue B"]
    with db.connect(dburl) as conn:
        assert conn.execute("SELECT adjacency->>'type' AS t FROM listing WHERE id=%s", (b,)).fetchone()["t"] == "detached"


def test_request_checks_idempotent_and_garage_only(dburl, tmp_path, monkeypatch):
    env(monkeypatch, dburl, tmp_path)
    a, b, c = seed(dburl)
    script = load_script()
    assert script.main(["--request-checks"])["demandes de vérification créées"] == 1
    assert script.main(["--request-checks"])["demandes de vérification créées"] == 0
    with db.connect(dburl) as conn:
        req = {r["id"]: (r["enrichment"] or {}).get("type_request") for r in conn.execute("SELECT id, enrichment FROM listing")}
        assert req[a]["reason"] == "garage_backfill" and req[b] is None and req[c] is None
        # demande traitée (passée dans l'historique) : pas recréée ; type fixé par l'utilisateur : ignoré
        conn.execute("UPDATE listing SET enrichment = (enrichment - 'type_request') || %s WHERE id=%s",
                     (Jsonb({"type_checks": [req[a]]}), a))
        conn.execute("UPDATE listing SET has_garage=true, enrichment = enrichment || %s WHERE id=%s",
                     (Jsonb({"property_type_user": "semi"}), b))
    assert script.main(["--request-checks"])["demandes de vérification créées"] == 0
