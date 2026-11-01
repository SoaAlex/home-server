"""Analyse initiale (scripts/dedupe_backfill.py) : à blanc sans modification, application idempotente."""
import importlib.util
from pathlib import Path

from app import db, settings
from tests.test_dedupe_db import add

CRIT = {"price": {"max": 550000, "target": 450000}, "require": {"garage": True, "garden": True},
        "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": [],
        "commute": {"destinations": {}}, "alert": {"min_score": 10, "max_price": 600000,
                                                   "require_confirmed_garage_garden": False}}


def load_script():
    spec = importlib.util.spec_from_file_location(
        "dedupe_backfill", Path(__file__).resolve().parent.parent / "scripts" / "dedupe_backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(dburl, tmp_path):
    base = tmp_path / "photos"
    with db.connect(dburl) as conn:
        a = add(conn, base, seeds=(1, 2, 3), price=300000, surface_m2=100, ext="a")
        b = add(conn, base, seeds=(1, 2), price=305000, surface_m2=100, ext="b", source="netty")
        c = add(conn, base, seeds=(9,), price=497000, surface_m2=118, land_m2=367, rooms=6, ext="c", source="seloger")
        d = add(conn, base, seeds=(10,), price=497000, surface_m2=118, land_m2=367, rooms=6, ext="d", source="seloger")
        e = add(conn, base, seeds=(20,), price=210000, surface_m2=70, ext="e")
        f = add(conn, base, seeds=(20,), price=215000, surface_m2=76, ext="f", source="netty")   # 1 photo, 70/76 m²
        add(conn, base, seeds=(30,), price=150000, surface_m2=55, ext="g")
    return a, b, c, d, e, f


def snapshot(dburl):
    with db.connect(dburl) as conn:
        return conn.execute("SELECT md5(string_agg(t::text, '|' ORDER BY id)) AS h FROM listing t").fetchone()["h"]


def env(monkeypatch, dburl, tmp_path):
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "load_criteria", lambda *a: CRIT)
    monkeypatch.setenv("DISABLE_ALERTS", "0")


def test_dry_run_modifies_no_listing_and_writes_report(dburl, tmp_path, monkeypatch):
    env(monkeypatch, dburl, tmp_path)
    a, b, c, d, e, f = seed(dburl, tmp_path)
    before = snapshot(dburl)
    report = tmp_path / "r.html"
    stats = load_script().main(["--report", str(report), "--workers", "1"])
    assert snapshot(dburl) == before
    assert stats["fusions automatiques prévues (= annonces qui deviennent secondaires)"] == 2
    assert stats["doublons possibles"] == 1 and stats["photos hachées (nouvelles)"] == 10
    html = report.read_text()
    assert "Fusions automatiques prévues : 2 biens, 4 annonces" in html and f"#{a}" in html and f"#{b}" in html
    assert "caractéristiques jumelles" in html                         # cartes c/d fusionnées sans photo commune
    assert "Doublons possibles (à valider dans l'UI) : 1" in html and f"#{e} ↔ #{f}" in html
    assert "data:image/jpeg;base64," in html
    with db.connect(dburl) as conn:
        assert conn.execute("SELECT count(*) AS n FROM duplicate_pair").fetchone()["n"] == 0


def test_apply_merges_suggests_and_second_apply_is_a_no_op(dburl, tmp_path, monkeypatch):
    env(monkeypatch, dburl, tmp_path)
    a, b, c, d, e, f = seed(dburl, tmp_path)
    script = load_script()
    first = script.main(["--apply", "--no-report", "--workers", "1"])
    assert first["fusions appliquées"] == 2 and first["suggestions enregistrées"] == 1
    with db.connect(dburl) as conn:
        merged = conn.execute("SELECT id, merged_into FROM listing WHERE merged_into IS NOT NULL").fetchall()
        sigs = conn.execute("SELECT count(*) AS n FROM listing WHERE dedupe_sig IS NOT NULL").fetchone()["n"]
    assert {frozenset((m["id"], m["merged_into"])) for m in merged} == {frozenset((a, b)), frozenset((c, d))}
    assert sigs == 7
    second = script.main(["--apply", "--no-report", "--workers", "1"])
    assert second["fusions appliquées"] == 0 and second["suggestions enregistrées"] == 0
    assert second["photos hachées (nouvelles)"] == 0
