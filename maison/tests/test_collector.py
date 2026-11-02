from app import collector, db, ingest


def test_postprocess_geocodes_city_when_insee_known_but_no_position(dburl, monkeypatch):
    """Régression : une annonce avec INSEE mais sans lat/lon (agences, notaires) restait sans carte ni trajets."""
    crit = {"price": {"max": 550000}, "require": {"garage": True, "garden": True},
            "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": [],
            "zones": {"Wambrechies": {"insee": "59636", "postal_codes": ["59118"], "zone_ids": []}},
            "commute": {"destinations": {"a": {"lat": 50.6, "lon": 3.1}}}, "alert": {"min_score": 75}}
    monkeypatch.setattr(collector.geo, "geocode",
                        lambda c, q, **k: {"lat": 50.68, "lon": 3.05, "citycode": "59636", "label": q, "type": "municipality"})
    monkeypatch.setattr(collector.geo, "commute", lambda c, lat, lon, d: {"a": 12, "total_min": 12})
    monkeypatch.setattr(collector.settings, "DATABASE_URL", dburl)
    with db.connect(dburl) as conn:
        lid, _ = ingest.upsert(conn, dict(source="agences", external_id="t:1", url="https://x/1", title="Maison",
                                          price=300000, city="Wambrechies", insee="59636", postal_code="59118",
                                          has_garage=True, has_garden=True))
        collector.postprocess(conn, None, crit, lid)
        row = conn.execute("SELECT lat, geo_precision, commute FROM listing WHERE id=%s", (lid,)).fetchone()
    assert row["lat"] == 50.68 and row["geo_precision"] == "city" and row["commute"]["total_min"] == 12


def test_postprocess_keeps_street_geocode_instead_of_overwriting_with_city(dburl, monkeypatch):
    crit = {"price": {"max": 550000}, "require": {"garage": True, "garden": True},
            "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": [],
            "zones": {"Wambrechies": {"insee": "59636", "postal_codes": ["59118"], "zone_ids": []}},
            "commute": {"destinations": {"a": {"lat": 50.6, "lon": 3.1}}}, "alert": {"min_score": 75}}

    def fake_geocode(client, query, **kwargs):
        if kwargs.get("kind") == "municipality":
            return {"lat": 50.68, "lon": 3.05, "citycode": "59636", "type": "municipality"}
        return {"lat": 50.681, "lon": 3.051, "citycode": "59636", "type": "street"}

    monkeypatch.setattr(collector.geo, "geocode", fake_geocode)
    monkeypatch.setattr(collector.geo, "commute", lambda c, lat, lon, d: {"a": 12, "total_min": 12})
    monkeypatch.setattr(collector.settings, "DATABASE_URL", dburl)
    with db.connect(dburl) as conn:
        lid, _ = ingest.upsert(conn, dict(source="pap", external_id="pap:test-street-priority",
                                          url="https://www.pap.fr/annonces/test-r123", title="Maison",
                                          price=300000, city="Wambrechies", insee="59636", postal_code="59118",
                                          address="Rue du Test, 59118 Wambrechies",
                                          has_garage=True, has_garden=True))
        collector.postprocess(conn, None, crit, lid)
        row = conn.execute("SELECT lat, lon, geo_precision FROM listing WHERE id=%s", (lid,)).fetchone()
    assert (row["lat"], row["lon"], row["geo_precision"]) == (50.681, 3.051, "approx")


CRIT_DEDUPE = {"price": {"max": 550000}, "require": {"garage": True, "garden": True},
               "preferred_cities": ["Wambrechies"], "other_cities": [], "excluded_cities": [],
               "zones": {"Wambrechies": {"insee": "59636", "postal_codes": ["59118"], "zone_ids": []}},
               "commute": {"destinations": {"a": {"lat": 50.6, "lon": 3.1}}}, "alert": {"min_score": 75}}


def test_postprocess_hashes_thumbnail_of_card_awaiting_completion(dburl, monkeypatch, tmp_path):
    """Carte SeLoger sans commune (« à compléter ») : sa vignette est téléchargée et hachée dans le cycle,
    sans aucune autre requête vers SeLoger (déduplication avant l'enrichissement de l'agent)."""
    import httpx
    from app import dedupe
    from tests.dedupe_helpers import jpeg, scene
    thumb = "https://cdnihddipa.cloudimg.io/a/b/c/abc.jpg?ci_seal=x&w=500&h=370"
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, content=jpeg(scene(7)))

    monkeypatch.setattr(collector.settings, "DATA_DIR", tmp_path)
    with db.connect(dburl) as conn, httpx.Client(transport=httpx.MockTransport(handler)) as client:
        lid, _ = ingest.upsert(conn, dict(source="seloger", external_id="26TEST", title="Maison 5 pièces",
                                          url="https://www.seloger.com/annonce/achat/x/26TEST", price=300000,
                                          surface_m2=100, photo_urls=[thumb]))
        collector.postprocess(conn, client, CRIT_DEDUPE, lid)
        row = conn.execute("SELECT photos FROM listing WHERE id=%s", (lid,)).fetchone()
        ph = conn.execute("SELECT idx, path, phash, std FROM listing_photo WHERE listing_id=%s", (lid,)).fetchall()
    assert seen == [thumb]
    assert row["photos"] == [f"{lid}/00.jpg"] and (tmp_path / "photos" / str(lid) / "00.jpg").exists()
    assert len(ph) == 1 and ph[0]["path"] == f"{lid}/00.jpg" and ph[0]["std"] >= dedupe.UNIFORM_STD
    expected = dedupe.image_hashes(jpeg(scene(7)))[0]
    assert dedupe.from_db(ph[0]["phash"]) == expected


def test_postprocess_still_skips_other_exclusions(dburl, monkeypatch, tmp_path):
    import httpx
    seen = []
    monkeypatch.setattr(collector.settings, "DATA_DIR", tmp_path)
    with db.connect(dburl) as conn, httpx.Client(transport=httpx.MockTransport(
            lambda req: seen.append(req) or httpx.Response(200, content=b"x"))) as client:
        lid, _ = ingest.upsert(conn, dict(source="bienici", external_id="hz", url="https://x/hz", city="Paris",
                                          price=300000, photo_urls=["https://p/1.jpg"]))
        collector.postprocess(conn, client, CRIT_DEDUPE, lid)       # « hors zone »
    assert seen == []


def _cycle_env(monkeypatch, dburl, tmp_path, crit, cards, photo_seed):
    """run_once isolé : aucune source balayée, e-mails = `cards`, photos servies localement, pas de réseau."""
    import httpx
    from tests.dedupe_helpers import jpeg, scene
    sent = []
    monkeypatch.setattr(collector.settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(collector.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collector.settings, "DISCORD_WEBHOOK_URL", "https://discord.example/hook")
    monkeypatch.setattr(collector.settings, "load_criteria", lambda: crit)
    monkeypatch.setattr(collector, "FULL_SWEEP", {})
    monkeypatch.setattr(collector.email_alerts, "fetch", lambda client, crit: cards)
    monkeypatch.setattr(collector.geo, "geocode", lambda c, q, **k: None)
    monkeypatch.setattr(collector.geo, "commute", lambda c, lat, lon, d: {"a": 20, "total_min": 20})
    monkeypatch.setattr(collector.alerts, "send_discord", lambda client, hook, row, crit=None: sent.append(row["id"]))
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    real = httpx.Client
    monkeypatch.setattr(collector.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=jpeg(scene(photo_seed))))))
    return sent


CRIT_CYCLE = {**CRIT_DEDUPE, "alert": {"min_score": 10, "max_price": 600000, "require_confirmed_garage_garden": False}}


def _known_listing(conn, tmp_path, seeds, **kw):
    from psycopg.types.json import Jsonb
    from app import dedupe
    from tests.test_dedupe_db import put_photos
    item = {"source": "bienici", "external_id": "known", "url": "https://www.bienici.com/annonce/known",
            "title": "Maison", "price": 300000, "surface_m2": 100, "city": "Wambrechies", "postal_code": "59118",
            "insee": "59636", "lat": 50.68, "lon": 3.05, "geo_precision": "city", "has_garage": True,
            "has_garden": True, **kw}
    lid, _ = ingest.upsert(conn, item)
    conn.execute("UPDATE listing SET photos=%s, enrichment_status='done', alerted_at=now() - interval '2 days', "
                 "commute='{\"a\": 20, \"total_min\": 20}' WHERE id=%s",
                 (Jsonb(put_photos(tmp_path / "photos", lid, seeds)), lid))
    dedupe.ensure_hashes(conn, base=tmp_path / "photos")
    dedupe.apply(conn, dedupe.analyze(conn, changed_only=False))     # analyse initiale appliquée
    conn.commit()
    return lid


def test_email_card_of_known_house_merged_in_same_cycle_without_alert(dburl, monkeypatch, tmp_path):
    card = {"source": "seloger", "external_id": "26NEWCARD", "url": "https://www.seloger.com/annonce/x/26NEWCARD",
            "title": "Maison 5 pièces 100 m² — Wambrechies", "price": 312000, "surface_m2": 100,
            "city": "Wambrechies", "postal_code": "59118", "photo_urls": ["https://cdnihddipa.cloudimg.io/x.jpg"]}
    sent = _cycle_env(monkeypatch, dburl, tmp_path, CRIT_CYCLE, [card], photo_seed=5)
    with db.connect(dburl) as conn:
        known = _known_listing(conn, tmp_path, seeds=(5, 6))
    collector.run_once()
    with db.connect(dburl) as conn:
        c = conn.execute("SELECT id, merged_into, score FROM listing WHERE external_id='26NEWCARD'").fetchone()
        p = conn.execute("SELECT cluster FROM listing WHERE id=%s", (known,)).fetchone()
        pending = conn.execute("SELECT id FROM listing WHERE enrichment_status='pending' AND merged_into IS NULL "
                               "AND status='active'").fetchall()
    assert c["merged_into"] == known and c["score"] is None
    assert [o["source"] for o in p["cluster"]["offers"]] == ["bienici", "seloger"]
    assert sent == [] and pending == []


def test_relisting_of_recently_gone_house_is_merged(dburl, monkeypatch, tmp_path):
    card = {"source": "seloger", "external_id": "26RELIST", "url": "https://www.seloger.com/annonce/x/26RELIST",
            "title": "Maison 5 pièces", "price": 289000, "surface_m2": 100, "city": "Wambrechies",
            "postal_code": "59118", "photo_urls": ["https://cdnihddipa.cloudimg.io/y.jpg"]}
    _cycle_env(monkeypatch, dburl, tmp_path, CRIT_CYCLE, [card], photo_seed=8)
    with db.connect(dburl) as conn:
        gone = _known_listing(conn, tmp_path, seeds=(8, 9), external_id="gone")
        conn.execute("UPDATE listing SET status='gone', last_seen=now() - interval '30 days' WHERE id=%s", (gone,))
    collector.run_once()
    with db.connect(dburl) as conn:
        c = conn.execute("SELECT merged_into FROM listing WHERE external_id='26RELIST'").fetchone()
        p = conn.execute("SELECT cluster FROM listing WHERE id=%s", (gone,)).fetchone()["cluster"]
    assert c["merged_into"] == gone and p["active"]
    assert [h["price"] for h in p["price_history"]] == [300000, 289000] and p["price"]["value"] == 289000


def test_dedupe_step_is_logged(dburl, monkeypatch, tmp_path, caplog):
    import logging
    _cycle_env(monkeypatch, dburl, tmp_path, CRIT_CYCLE, [], photo_seed=1)
    with db.connect(dburl) as conn:
        _known_listing(conn, tmp_path, seeds=(1,))
    with caplog.at_level(logging.INFO, logger="collector"):
        collector.run_once()
    assert any(r.message.startswith("doublons: ") and "fusions" in r.message for r in caplog.records)


def test_dedupe_step_skipped_until_initial_analysis_applied(dburl, monkeypatch, tmp_path, caplog):
    import logging
    _cycle_env(monkeypatch, dburl, tmp_path, CRIT_CYCLE, [], photo_seed=1)
    with caplog.at_level(logging.WARNING, logger="collector"):
        collector.run_once()
    assert any("étape ignorée" in r.message for r in caplog.records)


# ---------- mitoyenneté cartographique (spec building-adjacency) ----------
def _adj_listing(conn, ext, geo_precision="exact", address="1 rue de l'Exemple", confidence="exact", **kw):
    from psycopg.types.json import Jsonb
    from tests.adjacency_helpers import ll
    lon, lat = ll(3, -2)
    item = {"source": "bienici", "external_id": ext, "url": f"https://www.bienici.com/annonce/{ext}", "title": "Maison",
            "price": 300000, "city": "Wambrechies", "postal_code": "59118", "insee": "59636", "lat": lat, "lon": lon,
            "geo_precision": geo_precision, "address": address, "has_garage": True, "has_garden": True, **kw}
    lid, _ = ingest.upsert(conn, item)
    if confidence:
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s",
                     (Jsonb({"address_confidence": confidence}), lid))
    return lid


def _fake_fetch(monkeypatch, calls, error=None):
    from app import adjacency
    from tests.adjacency_helpers import building, house, plot, scene

    def fetch(client, point, address=None):
        calls.append((point, address))
        if error:
            raise adjacency.FetchError(error)
        return scene([house(), building("L", -6, 0, 0, 9), building("R", 6, 0, 12, 8)],
                     plot() + plot("PL", -6, 0) + plot("PR", 6, 12))
    monkeypatch.setattr(adjacency, "fetch", fetch)


def _adj(conn, lid):
    return conn.execute("SELECT adjacency FROM listing WHERE id=%s", (lid,)).fetchone()["adjacency"]


def test_adjacency_computed_for_an_exact_address(dburl, monkeypatch):
    from app import adjacency
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        lid = _adj_listing(conn, "adj-exact")
        st = adjacency.process(conn, None)
        a = _adj(conn, lid)
    assert st == {"computed": 1, "errors": 0, "pending": 0, "not_applicable": 0}
    assert a["status"] == "ok" and a["type"] == "terraced" and a["computed_at"]
    assert a["input"]["confidence"] == "exact" and a["input"]["address"] == "1 rue de l'Exemple"
    assert len(calls) == 1


def test_city_and_street_positions_are_not_applicable(dburl, monkeypatch):
    from app import adjacency
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        city = _adj_listing(conn, "adj-city", geo_precision="city", address=None, confidence=None)
        street = _adj_listing(conn, "adj-street", geo_precision="approx", confidence="street")
        number_unsure = _adj_listing(conn, "adj-none", confidence="none")
        st = adjacency.process(conn, None)
        res = {k: _adj(conn, i) for k, i in (("city", city), ("street", street), ("none", number_unsure))}
    assert calls == [] and st["not_applicable"] == 3
    assert {k: (v["status"], v["reason"]) for k, v in res.items()} == {
        "city": ("not_applicable", "imprecise_position"), "street": ("not_applicable", "imprecise_position"),
        "none": ("not_applicable", "low_confidence")}


def test_no_request_without_change_and_recompute_after_address_correction(dburl, monkeypatch):
    from psycopg.types.json import Jsonb
    from app import adjacency
    from tests.adjacency_helpers import ll
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        lid = _adj_listing(conn, "adj-change")
        adjacency.process(conn, None)
        first = _adj(conn, lid)["computed_at"]
        assert adjacency.process(conn, None)["computed"] == 0 and len(calls) == 1        # rien n'a changé
        lon, lat = ll(3, 40)                                                          # adresse corrigée à la main
        conn.execute("UPDATE listing SET address='3 rue de l''Exemple', lat=%s, lon=%s, enrichment = enrichment || %s "
                     "WHERE id=%s", (lat, lon, Jsonb({"address_method": "user"}), lid))
        assert adjacency.process(conn, None)["computed"] == 1
        a = _adj(conn, lid)
    assert len(calls) == 2 and calls[1][1] == "3 rue de l'Exemple"
    assert a["input"]["confidence"] == "user" and a["computed_at"] != first


def test_merged_position_of_a_secondary_source_is_used(dburl, monkeypatch):
    from psycopg.types.json import Jsonb
    from app import adjacency
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        card = _adj_listing(conn, "adj-card", geo_precision="city", address=None, confidence=None)
        agency = _adj_listing(conn, "adj-agency", confidence="probable")
        row = conn.execute("SELECT lat, lon FROM listing WHERE id=%s", (agency,)).fetchone()
        conn.execute("UPDATE listing SET merged_into=%s WHERE id=%s", (card, agency))
        conn.execute("UPDATE listing SET cluster=%s WHERE id=%s", (Jsonb({"active": True, "fill": {
            "lat": row["lat"], "lon": row["lon"], "geo_precision": "exact", "address": "1 rue de l'Exemple"}}), card))
        adjacency.process(conn, None)
        a, sec = _adj(conn, card), _adj(conn, agency)
    assert a["status"] == "ok" and a["input"]["confidence"] == "probable" and sec is None
    assert "probable_address" in a["warnings"]


def test_wfs_outage_does_not_stop_the_cycle_and_is_retried_after_6h(dburl, monkeypatch, tmp_path, caplog):
    import logging
    from app import adjacency
    calls = []
    _cycle_env(monkeypatch, dburl, tmp_path, CRIT_CYCLE, [], photo_seed=1)
    _fake_fetch(monkeypatch, calls, error="data.geopf.fr : HTTP 504")
    with db.connect(dburl) as conn:
        lid = _adj_listing(conn, "adj-outage")
    with caplog.at_level(logging.INFO, logger="collector"):
        collector.run_once()
    with db.connect(dburl) as conn:
        row = conn.execute("SELECT adjacency, score FROM listing WHERE id=%s", (lid,)).fetchone()
        assert row["adjacency"]["status"] == "error" and "504" in row["adjacency"]["error"]
        assert row["score"] is not None                                        # le cycle est allé jusqu'au score
        assert any(r.message == "mitoyenneté : 0 calculées, 1 échecs, 0 en attente" for r in caplog.records)
        assert adjacency.process(conn, None)["errors"] == 0 and len(calls) == 1   # pas de nouvel essai avant 6 h
        conn.execute("UPDATE listing SET adjacency = jsonb_set(adjacency, '{tried_at}', "
                     "to_jsonb((now() - interval '7 hours')::text)) WHERE id=%s", (lid,))
        assert adjacency.process(conn, None)["errors"] == 1 and len(calls) == 2


def test_backlog_is_bounded_per_cycle_most_recently_enriched_first(dburl, monkeypatch):
    from app import adjacency
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        ids = [_adj_listing(conn, f"adj-backlog-{i}") for i in range(600)]
        conn.execute("UPDATE listing SET enriched_at = now() - id * interval '1 minute' WHERE id = ANY(%s)", (ids,))
        st = adjacency.process(conn, None)
        done = [r["id"] for r in conn.execute("SELECT id FROM listing WHERE adjacency->>'status' = 'ok' ORDER BY id")]
        assert st == {"computed": 40, "errors": 0, "pending": 560, "not_applicable": 0}
        assert done == sorted(ids)[:40]                     # enriched_at le plus récent = plus petit id ici
        assert adjacency.process(conn, None)["pending"] == 520


def test_map_contradicting_the_agent_creates_one_conflict_request_when_garage_confirmed(dburl, monkeypatch):
    from psycopg.types.json import Jsonb
    from app import adjacency
    calls = []
    _fake_fetch(monkeypatch, calls)
    with db.connect(dburl) as conn:
        lid = _adj_listing(conn, "adj-conflict")
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s",
                     (Jsonb({"property_type": "detached"}), lid))
        adjacency.process(conn, None)
        row = conn.execute("SELECT enrichment, adjacency, enrichment_status FROM listing WHERE id=%s", (lid,)).fetchone()
        req = row["enrichment"]["type_request"]
        assert (req["reason"], req["note"]) == ("conflict", "carte : terraced, agent : detached")
        assert req["adjacency_at"] == row["adjacency"]["computed_at"] and row["adjacency"]["type_request_created"]
        conn.execute("UPDATE listing SET enrichment = enrichment - 'type_request' WHERE id=%s", (lid,))
        adjacency.process(conn, None)                                   # même calcul : pas de nouvelle demande
        assert "type_request" not in conn.execute("SELECT enrichment FROM listing WHERE id=%s",
                                                  (lid,)).fetchone()["enrichment"]
    assert len(calls) == 1


def test_no_conflict_request_without_confirmed_garage(dburl, monkeypatch):
    from psycopg.types.json import Jsonb
    from app import adjacency
    _fake_fetch(monkeypatch, [])
    with db.connect(dburl) as conn:
        lid = _adj_listing(conn, "adj-nogarage", has_garage=None)
        conn.execute("UPDATE listing SET enrichment = enrichment || %s WHERE id=%s",
                     (Jsonb({"property_type": "detached"}), lid))
        adjacency.process(conn, None)
        row = conn.execute("SELECT enrichment, adjacency FROM listing WHERE id=%s", (lid,)).fetchone()
    assert "type_request" not in row["enrichment"] and row["adjacency"]["type_request_created"] is False
