"""API de l'onglet « Doublons » (change OpenSpec add-duplicate-review-tab, spec duplicate-review)."""
import pytest
from fastapi.testclient import TestClient
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


def _add(dburl, key, source="bienici", **kw):
    fields = dict(source=source, external_id=key, url=f"https://e/{key}", price=400000, city="Wambrechies",
                  surface_m2=100)
    fields.update(kw)
    with db.connect(dburl) as conn:
        return upsert(conn, fields)[0]


def _sql(dburl, q, args=()):
    with db.connect(dburl) as conn:
        cur = conn.execute(q, args)
        return cur.fetchall() if cur.description else None


def _suggest(dburl, a, b, photos=0, rule="caractéristiques jumelles"):
    with db.connect(dburl) as conn:
        dedupe.suggest(conn, a, b, {"photos": photos, "rule": rule, "price": [400000, 400000]})


def _queue(client, **params):
    r = client.get("/api/duplicates", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _decide(client, a, b, action):
    return client.post("/api/duplicates/decision", json={"a": a, "b": b, "action": action})


# ---------- file ----------
def test_three_pairs_between_two_houses_make_one_entry_with_best_evidence(client, dburl):
    pa, m1, m2 = _add(dburl, "pa", "netty"), _add(dburl, "m1", "seloger"), _add(dburl, "m2", "leboncoin")
    pb = _add(dburl, "pb", surface_m2=97)
    with db.connect(dburl) as conn:
        dedupe.merge(conn, m1, pa, method="auto")
        dedupe.merge(conn, m2, pa, method="auto")
    _suggest(dburl, m1, pb, photos=1)
    _suggest(dburl, m2, pb, photos=3, rule="1 photo commune, caractéristiques à vérifier")
    _suggest(dburl, pa, pb, photos=0)
    q = _queue(client)["pairs"]
    assert len(q) == 1
    e = q[0]
    assert {e["a"]["id"], e["b"]["id"]} == {pa, pb} and e["key"] == f"{min(pa, pb)}-{max(pa, pb)}"
    assert e["pairs"] == 3 and e["evidence"]["photos"] == 3 and sorted(e["pair"]) == sorted([m2, pb])
    side = e["a"] if e["a"]["id"] == pa else e["b"]
    assert set(side["sources"]) == {"netty", "seloger", "leboncoin"} and len(side["offers"]) == 3
    assert side["active"] is True and "first_seen" in side and len(side["photos"]) <= 6


def test_couple_inside_one_house_is_not_listed(client, dburl):
    p, m = _add(dburl, "p", "netty"), _add(dburl, "m", "seloger")
    with db.connect(dburl) as conn:
        dedupe.merge(conn, m, p, method="auto")
        # paire suggérée restée entre deux sources d'un même bien (fusion faite par un autre chemin)
        conn.execute("INSERT INTO duplicate_pair (id_a, id_b, status, decided_by, evidence) "
                     "VALUES (%s, %s, 'suggested', 'auto', '{}') ON CONFLICT (id_a, id_b) DO UPDATE "
                     "SET status='suggested'", (min(p, m), max(p, m)))
    assert _queue(client)["pairs"] == []


def test_scope_excluded_and_disliked(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    c, d = _add(dburl, "c"), _add(dburl, "d", "seloger")
    _suggest(dburl, a, b)
    _suggest(dburl, c, d)
    _sql(dburl, "UPDATE listing SET excluded_reason='ville exclue' WHERE id = ANY(%s)", ([a, b],))
    _sql(dburl, "UPDATE listing SET reaction='dislike' WHERE id=%s", (c,))
    q = _queue(client)
    assert [e["key"] for e in q["pairs"]] == [f"{c}-{d}"] and q["hidden"] == 1
    full = _queue(client, all=1)
    assert {e["key"]: e["visible"] for e in full["pairs"]} == {f"{a}-{b}": False, f"{c}-{d}": True}
    assert full["hidden"] == 1
    _sql(dburl, "UPDATE listing SET status='gone' WHERE id = ANY(%s)", ([c, d],))
    assert _queue(client)["pairs"] == [] and _queue(client)["hidden"] == 2


def test_queue_order_best_score_first_unknown_last(client, dburl):
    ids = {k: _add(dburl, k) for k in ("a1", "a2", "b1", "b2", "c1", "c2")}
    _suggest(dburl, ids["c1"], ids["c2"])        # sans score, détecté en premier
    _suggest(dburl, ids["b1"], ids["b2"])
    _suggest(dburl, ids["a1"], ids["a2"])
    _sql(dburl, "UPDATE listing SET score=NULL WHERE id = ANY(%s)", ([ids["c1"], ids["c2"]],))
    _sql(dburl, "UPDATE listing SET score=40 WHERE id = ANY(%s)", ([ids["b1"], ids["b2"]],))
    _sql(dburl, "UPDATE listing SET score=CASE WHEN id=%s THEN 82 ELSE 10 END WHERE id = ANY(%s)",
         (ids["a2"], [ids["a1"], ids["a2"]]))
    keys = [e["key"] for e in _queue(client)["pairs"]]
    assert keys == [f"{ids['a1']}-{ids['a2']}", f"{ids['b1']}-{ids['b2']}", f"{ids['c1']}-{ids['c2']}"]


# ---------- décisions ----------
def test_merge_decision_removes_couple_and_badge(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    _suggest(dburl, a, b)
    r = _decide(client, a, b, "merge")
    assert r.status_code == 200, r.text
    keep = r.json()["principal"]
    assert keep in (a, b) and _queue(client, all=1)["pairs"] == []
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert keep in rows and ({a, b} - {keep}).isdisjoint(rows) and rows[keep]["dup_pending"] == 0


def test_reject_decision_is_remembered(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    _suggest(dburl, a, b)
    r = _decide(client, b, a, "reject")
    assert r.status_code == 200, r.text
    assert _sql(dburl, "SELECT status, decided_by FROM duplicate_pair WHERE id_a=%s AND id_b=%s",
                (min(a, b), max(a, b))) == [{"status": "rejected", "decided_by": "user"}]
    assert _queue(client, all=1)["pairs"] == []
    with db.connect(dburl) as conn:
        assert dedupe.suggest(conn, a, b, {"photos": 2}) is False


def test_reject_after_automatic_merge_is_refused(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    _suggest(dburl, a, b)
    with db.connect(dburl) as conn:                 # le collecteur fusionne pendant la revue
        dedupe.merge(conn, a, b, method="auto")
    r = _decide(client, a, b, "reject")
    assert r.status_code == 409 and r.json()["detail"] == "déjà traité"
    assert _sql(dburl, "SELECT count(*) AS n FROM duplicate_pair WHERE status='rejected'") == [{"n": 0}]


def test_decision_without_pending_pair_is_refused(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    _suggest(dburl, a, b)
    assert _decide(client, a, b, "reject").status_code == 200
    assert _decide(client, a, b, "merge").status_code == 409       # déjà refusé : rien à fusionner ici
    assert _sql(dburl, "SELECT count(*) AS n FROM listing WHERE merged_into IS NOT NULL") == [{"n": 0}]


def test_merge_remaps_other_couples_to_principal(client, dburl):
    a, b, c = _add(dburl, "a", "netty"), _add(dburl, "b", "seloger"), _add(dburl, "c", "leboncoin")
    _suggest(dburl, a, b)
    _suggest(dburl, a, c)
    assert len(_queue(client)["pairs"]) == 2
    keep = _decide(client, a, b, "merge").json()["principal"]
    q = _queue(client)["pairs"]
    assert len(q) == 1 and {q[0]["a"]["id"], q[0]["b"]["id"]} == {keep, c}
    assert _decide(client, q[0]["a"]["id"], q[0]["b"]["id"], "reject").status_code == 200
    assert _queue(client)["pairs"] == []


def test_decision_errors(client, dburl):
    a, b = _add(dburl, "a"), _add(dburl, "b", "seloger")
    _suggest(dburl, a, b)
    assert _decide(client, a, 999999, "merge").status_code == 404
    assert _decide(client, a, a, "merge").status_code == 400
    assert _decide(client, a, b, "split").status_code == 400
    assert _decide(client, a, "x", "merge").status_code == 400
    assert client.post("/api/duplicates/decision", json={"a": True, "b": b, "action": "merge"}).status_code == 400


def test_merge_keeping_the_other_side_remaps_couple_key(client, dburl):
    a, b, c = _add(dburl, "a", "netty"), _add(dburl, "b", "pap"), _add(dburl, "c", "seloger")
    _sql(dburl, "UPDATE listing SET reaction='like' WHERE id=%s", (b,))   # données utilisateur : b reste principale
    _suggest(dburl, a, b)
    _suggest(dburl, a, c)
    assert _decide(client, a, b, "merge").json()["principal"] == b
    q = _queue(client)["pairs"]
    assert [e["key"] for e in q] == [f"{min(b, c)}-{max(b, c)}"]
    side = q[0]["a"] if q[0]["a"]["id"] == b else q[0]["b"]
    assert {o["id"] for o in side["offers"]} == {a, b}      # l'UI retrouve le couple a–c par les offres du côté b
    assert _decide(client, a, c, "reject").status_code == 200  # décision envoyée avec les anciens identifiants
    assert _queue(client)["pairs"] == []
