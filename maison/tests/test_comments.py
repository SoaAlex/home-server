"""Commentaires de l'utilisateur sur un bien (change add-listing-comments)."""
import psycopg
import pytest
from fastapi.testclient import TestClient
from app import db, dedupe, settings
from app.ingest import upsert

AUTHORS = ["Alexandre", "Marie"]


def _listing(conn, ext="x", **kw):
    lid, _ = upsert(conn, dict(source="bienici", external_id=ext, url=f"https://e/{ext}", **kw))
    return lid


# ---------- schéma ----------
def test_schema_twice_and_constraints(dburl):
    assert db.init_schema(dburl) and db.init_schema(dburl)          # idempotent
    with db.connect(dburl) as conn:
        lid = _listing(conn)
        conn.execute("INSERT INTO listing_comment (listing_id, author, body) VALUES (%s, 'Marie', 'ok')", (lid,))
        for bad in ("", "x" * 2001):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("INSERT INTO listing_comment (listing_id, body) VALUES (%s, %s)", (lid, bad))
        conn.execute("DELETE FROM listing WHERE id=%s", (lid,))
        assert conn.execute("SELECT count(*) AS n FROM listing_comment").fetchone()["n"] == 0   # ON DELETE CASCADE


# ---------- API ----------
@pytest.fixture
def client(dburl, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", dburl)
    monkeypatch.setattr(settings, "ENRICH_TOKEN", "tok")
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "")
    from app import web
    monkeypatch.setattr(web, "postprocess", lambda *a, **k: None)   # pas d'appels réseau
    monkeypatch.setattr(web, "comment_authors", lambda crit=None: list(AUTHORS))
    with TestClient(web.app) as c:
        yield c


@pytest.fixture
def lid(dburl):
    with db.connect(dburl) as conn:
        return _listing(conn, "a", price=400000, city="Wambrechies")


def _pair(dburl):
    """Deux annonces seules (Netty, carte SeLoger) prêtes à fusionner."""
    with db.connect(dburl) as conn:
        p, _ = upsert(conn, dict(source="netty", external_id="p", url="https://e/p", price=400000,
                                 city="Wambrechies", surface_m2=100))
        m, _ = upsert(conn, dict(source="seloger", external_id="m", url="https://e/m", price=395000,
                                 city="Wambrechies", surface_m2=100))
    return p, m


def post(client, lid, body="Visite samedi 10 h", author="Marie", **kw):
    return client.post(f"/api/listings/{lid}/comments", json={"body": body, "author": author}, **kw)


def test_add_comment_trimmed_and_listed(client, lid):
    r = post(client, lid, body="  Visite samedi 10 h \n")
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["body"] == "Visite samedi 10 h" and c["author"] == "Marie" and c["listing_id"] == lid
    assert c["created_at"] and c["updated_at"] is None
    assert [x["id"] for x in client.get(f"/api/listings/{lid}/comments").json()] == [c["id"]]


def test_multiline_comment_kept(client, lid):
    body = "ligne 1\nligne 2\nligne 3"
    assert post(client, lid, body=body).json()["body"] == body


@pytest.mark.parametrize("body", ["   ", "", "x" * 2001, None, 12])
def test_invalid_text_refused(client, lid, body):
    assert post(client, lid, body=body).status_code == 400
    assert client.get(f"/api/listings/{lid}/comments").json() == []


def test_2000_chars_accepted(client, lid):
    assert post(client, lid, body="x" * 2000).status_code == 201


def test_unknown_listing_404(client):
    assert post(client, 999999).status_code == 404
    assert client.get("/api/listings/999999/comments").status_code == 404


def test_unknown_author_refused(client, lid):
    assert post(client, lid, author="Paul").status_code == 400
    assert post(client, lid, author=None).status_code == 400          # auteurs configurés : auteur obligatoire
    assert client.get(f"/api/listings/{lid}/comments").json() == []


def test_no_author_configured(client, lid, monkeypatch):
    from app import web
    monkeypatch.setattr(web, "comment_authors", lambda crit=None: [])
    r = post(client, lid, author=None)
    assert r.status_code == 201 and r.json()["author"] is None
    assert post(client, lid, author="").status_code == 201
    assert post(client, lid, author="Marie").status_code == 400


def test_edit_keeps_author_and_creation_date(client, lid):
    c = post(client, lid).json()
    r = client.patch(f"/api/comments/{c['id']}", json={"body": "Visite samedi 11 h"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["body"] == "Visite samedi 11 h" and e["author"] == "Marie"
    assert e["created_at"] == c["created_at"] and e["updated_at"]
    assert client.patch(f"/api/comments/{c['id']}", json={"body": " "}).status_code == 400
    assert client.patch("/api/comments/999999", json={"body": "x"}).status_code == 404


def test_delete_comment(client, lid):
    c = post(client, lid).json()
    assert client.delete(f"/api/comments/{c['id']}").status_code == 204
    assert client.get(f"/api/listings/{lid}/comments").json() == []
    assert client.delete(f"/api/comments/{c['id']}").status_code == 404


def test_agent_token_cannot_write(client, lid, dburl):
    c = post(client, lid).json()
    for h in ({"X-Token": "tok"}, {"X-Token": "autre"}, {"X-Token": ""}):
        assert post(client, lid, headers=h).status_code == 403
        assert client.patch(f"/api/comments/{c['id']}", json={"body": "agent"}, headers=h).status_code == 403
        assert client.delete(f"/api/comments/{c['id']}", headers=h).status_code == 403
    rows = client.get(f"/api/listings/{lid}/comments").json()
    assert [(x["id"], x["body"]) for x in rows] == [(c["id"], "Visite samedi 10 h")]


def test_comment_on_member_goes_to_principal(client, dburl):
    p, m = _pair(dburl)
    with db.connect(dburl) as conn:
        assert dedupe.merge(conn, m, p, method="auto") == p
    c = post(client, m).json()
    assert c["listing_id"] == p
    assert [x["id"] for x in client.get(f"/api/listings/{m}/comments").json()] == [c["id"]]


# ---------- fiche et liste ----------
def test_detail_comments(client, lid):
    assert client.get(f"/api/listings/{lid}").json()["comments"] == []
    a = post(client, lid, author="Marie").json()
    b = post(client, lid, author="Alexandre", body="Cuisine à refaire").json()
    assert [x["id"] for x in client.get(f"/api/listings/{lid}").json()["comments"]] == [a["id"], b["id"]]


def test_group_thread_merged_and_sorted(client, dburl):
    p, m = _pair(dburl)
    first = post(client, m, body="sur la carte").json()          # écrits avant la fusion, chacun sur son annonce
    second = post(client, p, body="sur la netty").json()
    with db.connect(dburl) as conn:
        assert dedupe.merge(conn, m, p, method="auto") == p
        conn.execute("UPDATE listing_comment SET created_at = now() - interval '1 day' WHERE id=%s", (second["id"],))
    d = client.get(f"/api/listings/{p}").json()
    assert [x["id"] for x in d["comments"]] == [second["id"], first["id"]]
    short = client.get(f"/api/listings/{m}").json()                 # secondaire : réponse courte inchangée
    assert short["merged_into"] == p and "comments" not in short


def test_list_counts_and_text(client, dburl, lid):
    row = next(x for x in client.get("/api/listings").json() if x["id"] == lid)
    assert row["comments_n"] == 0 and row["comments_text"] is None
    post(client, lid, body="Humidité au sous-sol")
    post(client, lid, body="Jardin plein sud")
    row = next(x for x in client.get("/api/listings").json() if x["id"] == lid)
    assert row["comments_n"] == 2 and row["comments_text"] == "Humidité au sous-sol Jardin plein sud"


def test_list_counts_group_once(client, dburl):
    p, m = _pair(dburl)
    post(client, p); post(client, p); post(client, m)
    with db.connect(dburl) as conn:
        dedupe.merge(conn, m, p, method="auto")
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert m not in rows and rows[p]["comments_n"] == 3


def test_comment_changes_nothing_else_and_enrichment_keeps_comments(client, dburl, lid):
    cols = "score, reaction, user_status, enrichment_status, enrichment"
    with db.connect(dburl) as conn:
        conn.execute("UPDATE listing SET score=70, reaction='like', user_status='to_visit' WHERE id=%s", (lid,))
        before = conn.execute(f"SELECT {cols} FROM listing WHERE id=%s", (lid,)).fetchone()
    post(client, lid)
    with db.connect(dburl) as conn:
        assert conn.execute(f"SELECT {cols} FROM listing WHERE id=%s", (lid,)).fetchone() == before
    thread = client.get(f"/api/listings/{lid}/comments").json()
    r = client.post(f"/api/listings/{lid}/enrichment", headers={"X-Token": "tok"},
                    json={"summary_fr": "Analyse", "comments": [], "user_comments": []})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/listings/{lid}/comments").json() == thread


# ---------- fusion et séparation par les routes de l'UI (spec merged-listings) ----------
def test_merge_route_joins_threads(client, dburl):
    p, m = _pair(dburl)
    post(client, p, body="un"); post(client, p, body="deux"); post(client, m, body="trois")
    r = client.post(f"/api/listings/{p}/merge", json={"other_id": m})
    assert r.status_code == 200, r.text
    keep = r.json()["principal"]
    other = m if keep == p else p
    d = client.get(f"/api/listings/{keep}").json()
    assert [x["body"] for x in d["comments"]] == ["un", "deux", "trois"]
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert other not in rows and rows[keep]["comments_n"] == 3


def test_split_route_gives_back_own_comments(client, dburl):
    p, m = _pair(dburl)
    client.post(f"/api/listings/{p}/reaction", json={"reaction": "like"})   # données utilisateur des deux côtés :
    own = post(client, m, body="vu sur SeLoger").json()                      # la source (Netty) départage
    assert client.post(f"/api/listings/{p}/merge", json={"other_id": m}).json()["principal"] == p
    later = post(client, m, body="écrit sur le bien fusionné").json()
    assert later["listing_id"] == p                                          # rangé sur la principale
    assert [x["id"] for x in client.get(f"/api/listings/{p}").json()["comments"]] == [own["id"], later["id"]]
    r = client.post(f"/api/listings/{p}/split", json={"member_id": m})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in client.get(f"/api/listings/{m}").json()["comments"]] == [own["id"]]
    assert [x["id"] for x in client.get(f"/api/listings/{p}").json()["comments"]] == [later["id"]]
    rows = {x["id"]: x for x in client.get("/api/listings").json()}
    assert rows[m]["comments_n"] == 1 and rows[p]["comments_n"] == 1
