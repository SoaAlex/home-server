"""Contrat de données du change add-surface-filters (spec listing-surface-filters).

L'UI filtre dans le navigateur les surfaces lues dans GET /api/listings : ces tests figent ce que la réponse doit
contenir pour que la surface filtrée soit celle que la carte affiche (colonne, complétée par les sources fusionnées,
corrigée par l'agent côté navigateur)."""
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from app import db, dedupe, settings
from app.ingest import upsert


@pytest.fixture
def client(dburl, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "ENRICH_TOKEN", "tok")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    from app import web
    from app.web import app
    monkeypatch.setattr(web, "postprocess", lambda *a, **k: None)   # pas d'appels réseau
    with TestClient(app) as c:
        yield c


def _by_url(client):
    return {r["url"]: r for r in client.get("/api/listings").json()}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def test_surfaces_are_json_numbers_or_null(client, dburl):
    with db.connect(dburl) as conn:
        upsert(conn, dict(source="bienici", external_id="full", url="https://e/full", price=400000,
                          city="Wambrechies", surface_m2=109.5, land_m2=317))
        upsert(conn, dict(source="seloger", external_id="noland", url="https://e/noland", price=400000,
                          city="Wambrechies", surface_m2=90))
        upsert(conn, dict(source="leboncoin", external_id="nosurf", url="https://e/nosurf", price=400000,
                          city="Wambrechies", land_m2=0))
    rows = _by_url(client)
    full, noland, nosurf = rows["https://e/full"], rows["https://e/noland"], rows["https://e/nosurf"]
    assert full["surface_m2"] == 109.5 and full["land_m2"] == 317
    assert all(_is_number(v) for v in (full["surface_m2"], full["land_m2"], noland["surface_m2"], nosurf["land_m2"]))
    assert noland["land_m2"] is None and nosurf["surface_m2"] is None      # inconnu = null, jamais 0 ni texte
    assert nosurf["land_m2"] == 0                                          # 0 m² reste une valeur connue


def test_agent_correction_travels_next_to_the_column(client, dburl):
    with db.connect(dburl) as conn:
        i, _ = upsert(conn, dict(source="bienici", external_id="fix", url="https://e/fix", price=400000,
                                 city="Wambrechies", surface_m2=100))
        conn.execute("UPDATE listing SET enrichment=%s WHERE id=%s", (Jsonb({"surface_m2": 120}), i))
    row = _by_url(client)["https://e/fix"]
    assert row["surface_m2"] == 100 and row["enrichment"]["surface_m2"] == 120     # l'UI lit enrichment avant la colonne


def test_merged_listing_exposes_land_filled_from_the_merged_source(client, dburl):
    with db.connect(dburl) as conn:
        p, _ = upsert(conn, dict(source="netty", external_id="p", url="https://e/p", price=400000,
                                 city="Wambrechies", surface_m2=100))
        m, _ = upsert(conn, dict(source="seloger", external_id="m", url="https://e/m", price=395000,
                                 city="Wambrechies", surface_m2=100, land_m2=367))
        assert dedupe.merge(conn, m, p, method="auto") == p
        assert conn.execute("SELECT land_m2 FROM listing WHERE id=%s", (p,)).fetchone()["land_m2"] is None   # colonne vide
    rows = {r["id"]: r for r in client.get("/api/listings").json()}
    assert m not in rows                                                   # la source fusionnée n'est pas dans la liste
    assert rows[p]["land_m2"] == 367 and rows[p]["surface_m2"] == 100      # terrain complété par la source fusionnée
    assert _is_number(rows[p]["land_m2"])
