"""Déduplication des annonces : empreintes photo, détection des doublons, fusion en groupes.

Spécification et choix techniques : openspec/changes/merge-duplicate-listings (specs + design D1-D13).
- Empreintes : pHash (DCT 32×32 → 8×8) + dHash (9×8), 64 bits chacun, et écart type 16×16 (images unies).
- Détection : comparaison exhaustive en mémoire (numpy), règles explicites auto / suggestion / rien, vétos.
- Fusion : pointeur `merged_into` vers l'annonce principale (groupes plats) ; vue du groupe matérialisée
  dans `listing.cluster` (offres, prix retenu, champs complétés, photos, historique).
Toutes les écritures de groupe passent par un verrou consultatif (collecteur, analyse initiale, routes web).
"""
import hashlib
import io
import json
import math
import multiprocessing
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image
from psycopg.types.json import Jsonb

from app import settings
from app.scoring import OVERRIDABLE, POSITION
from app.textparse import city_key, norm

# ---------- seuils (design D4, D7 ; à recalibrer sur le rapport à blanc) ----------
PHASH_SURE, PHASH_MAX, DHASH_MAX = 6, 10, 10          # même photo : p ≤ 6, ou p ≤ 10 et d ≤ 10
UNIFORM_STD = 12.0                                   # image quasi unie (logo, fond blanc)
SURFACE_AUTO, SURFACE_TWIN, SURFACE_VETO = 0.05, 0.03, 0.12
PRICE_AUTO_PHOTO, PRICE_AUTO_TEXT, PRICE_TWIN = 0.15, 0.10, 0.01
TWIN_SURFACE_M2 = 1.0                                # jumeaux : surface à 1 m² près (ou 1 %)
LAND_TWIN = 0.05
DIST_VETO_KM, DIST_TWIN_KM = 10.0, 3.0
EXACT_VETO_KM = 0.5                                  # deux positions « exactes » (adresses) à plus de 500 m
TEXT_CONTAINMENT, TEXT_MIN_WORDS, SHINGLE = 0.8, 60, 5
STALE_DAYS = 45                                      # carte e-mail non revue : offre « non confirmée »
SCOPE_GONE_DAYS = 90                                 # annonces disparues encore comparées (remises en vente)
EMAIL_SOURCES = ("seloger", "leboncoin")
SOURCE_RANK = {"agences": 0, "netty": 0, "maes": 0, "notaires": 0, "pap": 1, "bienici": 1}   # e-mail & autres : 2
NEWBUILD = re.compile(r"programme neuf|\bvefa\b|\bpsla\b|frais de notaire reduits|livraison[^.]{0,30}20\d\d")
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('maison-dedupe'))"
SCOPE_SQL = f"(status = 'active' OR last_seen > now() - interval '{SCOPE_GONE_DAYS} days')"
FILL_FIELDS = ("city", "postal_code", "insee", "surface_m2", "land_m2", "rooms", "bedrooms", "dpe", "ges",
               "dpe_value", "dpe_date", "year_built", "has_garage", "has_garden", "has_bathtub",
               "property_type", "works", "address")
PRECISION = {"exact": 3, "approx": 2, "city": 1}
_MASK = (1 << 64) - 1


# ---------- empreintes ----------
_N = 32
_DCT = np.array([[math.cos(math.pi * (2 * x + 1) * u / (2 * _N)) * (math.sqrt(1 / _N) if u == 0 else math.sqrt(2 / _N))
                  for x in range(_N)] for u in range(_N)])


def _pack(bits) -> int:
    return int.from_bytes(np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes(), "big")


def image_hashes(data: bytes) -> tuple[int, int, float]:
    """(pHash, dHash, écart type) d'une image ; hachages = entiers non signés de 64 bits.
    Décodage JPEG réduit (`draft`) : ~20 ms pour une photo de 1920×1440 (2 Mo)."""
    im = Image.open(io.BytesIO(data))
    im.draft("L", (64, 64))
    g = im.convert("L")
    small = np.asarray(g.resize((_N, _N), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT @ small @ _DCT.T)[:8, :8].flatten()
    ph = _pack(low > np.median(low[1:]))
    d = np.asarray(g.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    dh = _pack((d[:, 1:] > d[:, :-1]).flatten())
    std = float(np.asarray(g.resize((16, 16)), dtype=np.float64).std())
    return ph, dh, std


def to_db(u: int) -> int:
    """uint64 → BIGINT signé (complément à deux)."""
    return u - (1 << 64) if u >= 1 << 63 else u


def from_db(s: int) -> int:
    return s & _MASK


def ham(a: int, b: int) -> int:
    return ((a ^ b) & _MASK).bit_count()


def same_photo(h1, h2) -> bool:
    """h = (pHash, dHash) non signés. Même photo : recompressée, redimensionnée, filigranée, peu recadrée."""
    p = ham(h1[0], h2[0])
    return p <= PHASH_SURE or (p <= PHASH_MAX and ham(h1[1], h2[1]) <= DHASH_MAX)


def photo_dir() -> Path:
    return settings.DATA_DIR / "photos"


def store_hash(conn, lid: int, idx: int, path: str, hashes, size=None, mtime=None) -> None:
    ph, dh, std = hashes
    conn.execute(
        "INSERT INTO listing_photo (listing_id, idx, path, phash, dhash, std, file_size, file_mtime, generic, hashed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) ON CONFLICT (listing_id, idx) DO UPDATE SET "
        "path=EXCLUDED.path, phash=EXCLUDED.phash, dhash=EXCLUDED.dhash, std=EXCLUDED.std, "
        "file_size=EXCLUDED.file_size, file_mtime=EXCLUDED.file_mtime, generic=EXCLUDED.generic, hashed_at=now()",
        (lid, idx, path, to_db(ph), to_db(dh), std, size, mtime, std < UNIFORM_STD))


def _hash_file(path: str):
    try:
        return image_hashes(Path(path).read_bytes())
    except Exception:
        return None


def stale_photos(conn, ids=None, base: Path | None = None):
    """Photos locales à (re)hacher (absentes de listing_photo, ou fichier changé de taille/date) et lignes
    listing_photo obsolètes (photo retirée de `photos` ou fichier disparu), pour `ids` (toutes si None)."""
    base = Path(base or photo_dir())
    flt, args = ("WHERE id = ANY(%s)", (list(ids),)) if ids is not None else ("", ())
    rows = conn.execute(f"SELECT id, photos FROM listing {flt}", args).fetchall()
    known = {}
    for r in conn.execute("SELECT listing_id, idx, path, file_size, file_mtime FROM listing_photo "
                          + ("WHERE listing_id = ANY(%s)" if ids is not None else ""), args).fetchall():
        known.setdefault(r["listing_id"], {})[r["idx"]] = (r["path"], r["file_size"], r["file_mtime"])
    todo, drop = [], []
    for r in rows:
        mine, keep = known.get(r["id"], {}), set()
        for rel in r["photos"] or []:
            try:
                idx = int(Path(rel).stem)
                st = (base / rel).stat()
            except (ValueError, OSError):
                continue
            keep.add(idx)
            if mine.get(idx) == (rel, st.st_size, st.st_mtime):
                continue
            todo.append((r["id"], idx, rel, str(base / rel), st.st_size, st.st_mtime))
        drop += [(r["id"], i) for i in mine if i not in keep]
    return todo, drop


def ensure_hashes(conn, ids=None, base: Path | None = None, workers: int = 1, decode=None) -> int:
    """Hache les photos locales nouvelles ou modifiées ; une photo inchangée n'est jamais redécodée."""
    todo, drop = stale_photos(conn, ids, base)
    for lid, idx in drop:
        conn.execute("DELETE FROM listing_photo WHERE listing_id=%s AND idx=%s", (lid, idx))
    if decode is None and workers > 1 and len(todo) > 50:
        with multiprocessing.Pool(workers) as pool:
            results = pool.map(_hash_file, [t[3] for t in todo], chunksize=16)
    else:
        f = decode or image_hashes
        results = []
        for t in todo:
            try:
                results.append(f(Path(t[3]).read_bytes()))
            except Exception:
                results.append(None)
    n = 0
    for (lid, idx, rel, _, size, mtime), h in zip(todo, results):
        if h:
            store_hash(conn, lid, idx, rel, h, size, mtime)
            n += 1
    return n


# ---------- annonces comparées ----------
def _num(v):
    return float(v) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v else None


def _own(row: dict, k: str):
    """Valeur propre de l'annonce (colonne surchargée par son enrichissement), sans le groupe."""
    enr = row.get("enrichment") or {}
    if k in OVERRIDABLE and enr.get(k) is not None:
        return enr[k]
    return row.get(k)


def _words(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(text))


def is_newbuild(row: dict) -> bool:
    if (row.get("raw") or {}).get("newProperty") is True or row.get("new_property") is True:
        return True
    return bool(NEWBUILD.search(norm(f"{_own(row, 'title') or ''} {row.get('description') or ''}")))


def item_of(row: dict) -> dict:
    """Résumé d'une annonce pour la comparaison (valeurs propres effectives)."""
    words = _words(row.get("description"))
    return {
        "id": row["id"], "source": row["source"], "group": row.get("merged_into") or row["id"],
        "price": _num(_own(row, "price")), "surface": _num(_own(row, "surface_m2")),
        "land": _num(_own(row, "land_m2")), "rooms": _num(row.get("rooms")), "bedrooms": _num(_own(row, "bedrooms")),
        "city_key": city_key(_own(row, "city"), row.get("postal_code")) if _own(row, "city") else None,
        "lat": row.get("lat"), "lon": row.get("lon"), "newbuild": is_newbuild(row), "words": words,
        "title": _own(row, "title"), "url": row.get("url"), "status": row.get("status"),
        "agency": row.get("agency"), "city": _own(row, "city"), "photos": row.get("photos") or [],
        "photo_urls": row.get("photo_urls") or [], "dedupe_sig": row.get("dedupe_sig"),
        "precise": row.get("geo_precision") == "exact",
    }


def _rel(x, y):
    return abs(x - y) / min(x, y) if x and y else None


def km(a: dict, b: dict):
    if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return None
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def hard_veto(a: dict, b: dict) -> str | None:
    """Vétos « durs » : jamais de fusion automatique ni de suggestion (design D7)."""
    s = _rel(a["surface"], b["surface"])
    if s is not None and s > SURFACE_VETO:
        return "surface"
    if a["bedrooms"] is not None and b["bedrooms"] is not None and abs(a["bedrooms"] - b["bedrooms"]) >= 2:
        return "chambres"
    d = km(a, b)
    if d is not None and d > DIST_VETO_KM:
        return "distance"
    return None


def address_conflict(a: dict, b: dict) -> bool:
    """Deux positions « exactes » (adresses) à plus de 500 m. Indice seulement : une adresse « probable » de
    l'agent peut être fausse, donc ≥ 2 photos communes l'emportent ; ne rend jamais une photo générique."""
    d = km(a, b)
    return bool(d is not None and a.get("precise") and b.get("precise") and d > EXACT_VETO_KM)


def soft_conflict(a: dict, b: dict) -> str | None:
    """Incompatibilité qui empêche un jumeau (fusion sans photo commune) de rejoindre un groupe : chaque membre
    doit rester compatible, sinon les fusions dérivent de proche en proche (A≈B, B≈C, mais A≠C)."""
    land = _rel(a["land"], b["land"])
    if land is not None and land > LAND_TWIN:
        return "terrain"
    if a["bedrooms"] is not None and b["bedrooms"] is not None and a["bedrooms"] != b["bedrooms"]:
        return "chambres"
    s = _rel(a["surface"], b["surface"])
    if s is not None and s > SURFACE_AUTO:
        return "surface"
    p = _rel(a["price"], b["price"])
    if p is not None and p > PRICE_AUTO_PHOTO:
        return "prix"
    if address_conflict(a, b):
        return "adresses différentes"
    return None


def _gap(a: dict, b: dict) -> float:
    return (_rel(a["price"], b["price"]) or 0) + (_rel(a["surface"], b["surface"]) or 0)


def containment(a: dict, b: dict) -> float | None:
    """Part des 5-grammes de la description la plus courte présents dans l'autre (≥ 60 mots des deux côtés)."""
    if min(len(a["words"]), len(b["words"])) < TEXT_MIN_WORDS:
        return None
    sa = a.get("_sh") or a.setdefault("_sh", {" ".join(a["words"][i:i + SHINGLE]) for i in range(len(a["words"]) - SHINGLE + 1)})
    sb = b.get("_sh") or b.setdefault("_sh", {" ".join(b["words"][i:i + SHINGLE]) for i in range(len(b["words"]) - SHINGLE + 1)})
    return len(sa & sb) / min(len(sa), len(sb)) if sa and sb else None


def _twin(a: dict, b: dict, d) -> bool:
    if not ((a["city_key"] and a["city_key"] == b["city_key"]) or (d is not None and d <= DIST_TWIN_KM)):
        return False
    p, s = _rel(a["price"], b["price"]), _rel(a["surface"], b["surface"])
    if p is None or s is None or p > PRICE_TWIN or (s > 0.01 and abs(a["surface"] - b["surface"]) > TWIN_SURFACE_M2):
        return False
    if a["rooms"] is not None and b["rooms"] is not None and abs(a["rooms"] - b["rooms"]) > 1:
        return False
    if a["bedrooms"] is not None and b["bedrooms"] is not None and a["bedrooms"] != b["bedrooms"]:
        return False
    land = _rel(a["land"], b["land"])
    return land is None or land <= LAND_TWIN


def decide(a: dict, b: dict, photos: int = 0, rejected: bool = False) -> tuple[str, dict]:
    """Décision pour une paire : ('auto' | 'suggest' | 'none', preuves). `photos` = photos communes non génériques."""
    d = km(a, b)
    ev = {"photos": photos, "surface": [a["surface"], b["surface"]], "price": [a["price"], b["price"]],
          "distance_km": round(d, 1) if d is not None else None}
    if rejected:
        return "none", {**ev, "rule": "refus utilisateur"}
    veto = hard_veto(a, b)
    if veto:
        return "none", {**ev, "veto": veto}
    s, p = _rel(a["surface"], b["surface"]), _rel(a["price"], b["price"])
    text = containment(a, b) if (s is not None and p is not None and s <= SURFACE_TWIN and p <= PRICE_AUTO_TEXT) else None
    if text is not None:
        ev["text"] = round(text, 2)
    rule = None
    if photos >= 2:
        rule = f"{photos} photos communes"
    elif photos == 1 and (s is not None or p is not None) and (s is None or s <= SURFACE_AUTO) \
            and (p is None or p <= PRICE_AUTO_PHOTO):
        rule = "1 photo commune + surface/prix compatibles"
    elif text is not None and text >= TEXT_CONTAINMENT:
        rule = "texte quasi identique"
    elif _twin(a, b, d):
        rule = "caractéristiques jumelles"      # fusion auto sans photo commune (décision du 2026-11-01)
    newbuild = a["newbuild"] or b["newbuild"]
    if address_conflict(a, b):
        ev["veto"] = "adresses différentes"
        if photos >= 2:
            return "auto", {**ev, "rule": f"{rule} (adresses différentes : adresse probable à revoir)"}
        if photos == 1:
            return "suggest", {**ev, "rule": "1 photo commune, adresses différentes"}
        return "none", ev
    if rule and not newbuild:
        return "auto", {**ev, "rule": rule}
    if rule:
        return "suggest", {**ev, "rule": f"{rule} (programme neuf)"}
    if photos >= 1:
        return "suggest", {**ev, "rule": "1 photo commune, caractéristiques à vérifier"}
    return "none", ev


def signature(it: dict, hashes: list) -> str:
    """Empreinte des valeurs comparées : une annonce n'est recomparée que si elle change (design D11)."""
    payload = [sorted(hashes), it["price"], it["surface"], it["land"], it["rooms"], it["bedrooms"], it["city_key"],
               round(it["lat"], 4) if it["lat"] is not None else None,
               round(it["lon"], 4) if it["lon"] is not None else None, it["newbuild"],
               hashlib.md5(" ".join(it["words"]).encode()).hexdigest()]
    return hashlib.md5(json.dumps(payload).encode()).hexdigest()


# ---------- analyse (lecture seule) ----------
LOAD_COLS = ("id, source, url, title, description, price, surface_m2, land_m2, rooms, bedrooms, city, postal_code, "
             "lat, lon, geo_precision, agency, status, enrichment, raw->'newProperty' AS new_property, merged_into, "
             "photos, photo_urls, dedupe_sig, cluster->'fill' AS fill")
GROUP_FILL = {"surface": "surface_m2", "land": "land_m2", "rooms": "rooms", "bedrooms": "bedrooms"}


def load_items(conn) -> dict[int, dict]:
    """Annonces du périmètre (actives + disparues depuis ≤ 90 j) et membres de tous les groupes existants."""
    rows = conn.execute(f"SELECT {LOAD_COLS}, {SCOPE_SQL} AS in_scope FROM listing WHERE {SCOPE_SQL} "
                        "OR merged_into IS NOT NULL OR id IN (SELECT merged_into FROM listing WHERE merged_into IS NOT NULL)"
                        ).fetchall()
    out, fills = {}, {r["id"]: r["fill"] or {} for r in rows if r["fill"]}
    for r in rows:
        it = item_of(r)
        it["in_scope"] = r["in_scope"]
        fill = fills.get(it["group"], {})          # valeurs du bien (autres sources) pour les champs vides
        for k, col in GROUP_FILL.items():
            if it[k] is None and fill.get(col) is not None:
                it[k] = _num(fill[col])
        out[r["id"]] = it
    return out


def load_hashes(conn, ids, extra=None):
    """Tableaux numpy des empreintes des annonces `ids` (+ `extra` = {lid: [(idx, ph, dh, std)]} non stockées)."""
    rows = conn.execute("SELECT listing_id, idx, phash, dhash, std, generic FROM listing_photo WHERE listing_id = ANY(%s) "
                        "ORDER BY listing_id, idx", (list(ids),)).fetchall()
    lids = [r["listing_id"] for r in rows]
    idxs = [r["idx"] for r in rows]
    ph = [from_db(r["phash"]) for r in rows]
    dh = [from_db(r["dhash"]) for r in rows]
    gen = [bool(r["generic"]) for r in rows]
    for lid, lst in (extra or {}).items():
        for idx, p, d, std in lst:
            lids.append(lid); idxs.append(idx); ph.append(p); dh.append(d); gen.append(std < UNIFORM_STD)
    return {"lid": np.array(lids, dtype=np.int64), "idx": np.array(idxs, dtype=np.int64),
            "ph": np.array(ph, dtype=np.uint64), "dh": np.array(dh, dtype=np.uint64),
            "generic": np.array(gen, dtype=bool)}


def _by_listing(H) -> dict[int, list[int]]:
    by_lid: dict[int, list[int]] = {}
    for k, lid in enumerate(H["lid"].tolist()):
        by_lid.setdefault(lid, []).append(k)
    return by_lid


def signatures(items, H, by_lid=None) -> dict[int, str]:
    """Signatures actuelles des annonces du périmètre."""
    by_lid = by_lid if by_lid is not None else _by_listing(H)
    return {i: signature(it, [(int(H["ph"][k]), int(H["dh"][k])) for k in by_lid.get(i, [])])
            for i, it in items.items() if it["in_scope"]}


def photo_matches(H, query) -> list[tuple[int, int]]:
    """Paires (q, j) d'indices de photos identiques, q dans `query`, j d'une autre annonce (comparaison exhaustive)."""
    out = []
    query = np.asarray(sorted(query), dtype=np.int64)
    for start in range(0, len(query), 256):
        q = query[start:start + 256]
        pd = np.bitwise_count(H["ph"][q, None] ^ H["ph"][None, :])
        cand = pd <= PHASH_MAX
        if not cand.any():
            continue
        dd = np.bitwise_count(H["dh"][q, None] ^ H["dh"][None, :])
        ok = cand & ((pd <= PHASH_SURE) | (dd <= DHASH_MAX)) & (H["lid"][q, None] != H["lid"][None, :])
        for qi, j in zip(*np.nonzero(ok)):
            out.append((int(q[qi]), int(j)))
    return out


def analyze(conn, changed_only: bool = True, extra_hashes=None, ids=None) -> dict:
    """Calcule les décisions sans rien écrire. changed_only : seules les annonces dont la signature a changé
    sont comparées à l'ensemble ; sinon tout le périmètre (analyse initiale)."""
    t0 = time.monotonic()
    items = load_items(conn)
    scope = [i for i, it in items.items() if it["in_scope"]]
    H = load_hashes(conn, list(items), extra_hashes)
    by_lid = _by_listing(H)
    sigs = signatures(items, H, by_lid)
    if ids is not None:
        changed = [i for i in ids if i in sigs]
    else:
        changed = [i for i in scope if not changed_only or sigs[i] != items[i]["dedupe_sig"]]
    res = {"items": items, "hashes": H, "changed": changed, "sigs": {i: sigs[i] for i in changed},
           "ops": [], "generic": set(), "decisions": [], "elapsed": 0.0}
    if not changed:
        res["elapsed"] = time.monotonic() - t0
        return res
    chg = set(changed)
    query = [k for i in changed for k in by_lid.get(i, [])]
    matches = photo_matches(H, query)

    # photos génériques : unies, ou reliant des annonces incompatibles (bandeau d'agence, rendu de programme)
    generic = set(np.nonzero(H["generic"])[0].tolist())
    linked: dict[int, set[int]] = {}
    for q, j in matches:
        linked.setdefault(q, set()).add(j)
    for q, js in linked.items():
        ks = {q} | js
        lids = {int(H["lid"][k]) for k in ks}
        if ks & generic or any(hard_veto(items[x], items[y]) for x in lids for y in lids if x < y):
            generic |= ks
    res["generic"] = {(int(H["lid"][k]), int(H["idx"][k])) for k in generic if not H["generic"][k]}

    # photos communes non génériques par paire d'annonces (photos distinctes du plus petit côté)
    common: dict[tuple[int, int], tuple[set, set]] = {}
    for q, j in matches:
        if q in generic or j in generic:
            continue
        a, b = int(H["lid"][q]), int(H["lid"][j])
        key = (min(a, b), max(a, b))
        sa, sb = common.setdefault(key, (set(), set()))
        first, second = (q, j) if a == key[0] else (j, q)     # chaque paire est vue dans les deux sens
        sa.add(first)
        sb.add(second)
    pairs = {k: min(len(v[0]), len(v[1])) for k, v in common.items()}

    # candidats par caractéristiques (jumeaux, texte) : surface ≤ 3 %, prix ≤ 10 %, distance ≤ 10 km
    sc = np.array(scope, dtype=np.int64)
    pr = np.array([items[i]["price"] or np.nan for i in scope])
    sf = np.array([items[i]["surface"] or np.nan for i in scope])
    with np.errstate(invalid="ignore", divide="ignore"):
        for a in changed:
            ia = items[a]
            if not (ia["price"] and ia["surface"]):
                continue
            mp = np.abs(pr - ia["price"]) / np.minimum(pr, ia["price"]) <= PRICE_AUTO_TEXT
            ms = np.abs(sf - ia["surface"]) / np.minimum(sf, ia["surface"]) <= SURFACE_TWIN
            for b in sc[mp & ms].tolist():
                if b != a:
                    pairs.setdefault((min(a, b), max(a, b)), 0)

    status = {(r["id_a"], r["id_b"]): r["status"] for r in conn.execute("SELECT id_a, id_b, status FROM duplicate_pair")}
    rejected = {k for k, s in status.items() if s == "rejected"}
    members: dict[int, set[int]] = {}
    for i, it in items.items():
        members.setdefault(it["group"], set()).add(i)

    def groups_rejected(ga, gb):
        return any((min(x, y), max(x, y)) in rejected for x in members[ga] for y in members[gb])

    decisions = []
    for (a, b), n in pairs.items():
        if a not in items or b not in items or not (a in chg or b in chg):
            continue
        ga, gb = items[a]["group"], items[b]["group"]
        if ga == gb:
            continue
        kind, ev = decide(items[a], items[b], n, rejected=groups_rejected(ga, gb))
        if kind != "none":
            decisions.append((a, b, kind, ev))
    res["decisions"] = decisions
    res["ops"] = plan(decisions, items, members, rejected, status)
    res["elapsed"] = time.monotonic() - t0
    return res


def plan(decisions, items, members, rejected, status) -> list[tuple]:
    """Groupes par union-find, avec contrôle sur tout le groupe (pas de chaînage incompatible, design D8).
    Renvoie les opérations ('merge', a, b, preuves) puis ('suggest', a, b, preuves)."""
    parent = {g: g for g in members}
    comp = {g: set(m) for g, m in members.items()}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ops, suggest = [], []
    autos = sorted((d for d in decisions if d[2] == "auto"),    # les preuves fortes, puis les jumeaux les plus proches
                   key=lambda d: (-d[3]["photos"], -(d[3].get("text") or 0), _gap(items[d[0]], items[d[1]]), d[0], d[1]))
    for a, b, _, ev in autos:
        ga, gb = find(items[a]["group"]), find(items[b]["group"])
        if ga == gb:
            continue
        clash = next((f"{x}/{y}: {v}" for x in comp[ga] for y in comp[gb]
                      if (v := hard_veto(items[x], items[y]))), None)
        if not clash and ev.get("rule") == "caractéristiques jumelles":
            clash = next((f"{x}/{y}: {v}" for x in comp[ga] for y in comp[gb]
                          if (v := soft_conflict(items[x], items[y]))), None)
        if any((min(x, y), max(x, y)) in rejected for x in comp[ga] for y in comp[gb]):
            continue
        if clash:
            suggest.append((a, b, {**ev, "rule": f"{ev['rule']} — veto dans le groupe ({clash})"}))
            continue
        parent[gb] = ga
        comp[ga] |= comp.pop(gb)
        ops.append(("merge", a, b, ev))
    for a, b, kind, ev in decisions:
        if kind == "suggest":
            suggest.append((a, b, ev))
    seen = set()
    for a, b, ev in suggest:
        key = (min(a, b), max(a, b))
        if key in seen or find(items[a]["group"]) == find(items[b]["group"]) or status.get(key) in ("rejected", "merged"):
            continue
        seen.add(key)
        ops.append(("suggest", key[0], key[1], ev))
    return ops


# ---------- écritures (sous verrou) ----------
def _lock(conn):
    conn.execute(LOCK_SQL)


def _rank(r: dict) -> tuple:
    """Ordre de choix de l'annonce principale (spec merged-listings)."""
    enr = r.get("enrichment") or {}
    return (0 if enr.get("address_method") == "user" else 1,
            0 if (r.get("reaction") or r.get("user_status") or r.get("user_note")) else 1,
            0 if r.get("enrichment_status") == "done" else 1,
            SOURCE_RANK.get(r.get("source"), 2), -len(r.get("photos") or []),
            r.get("first_seen") or datetime.max.replace(tzinfo=timezone.utc), r["id"])


def principal_of(conn, lid: int) -> int:
    r = conn.execute("SELECT merged_into FROM listing WHERE id=%s", (lid,)).fetchone()
    if not r:
        raise LookupError(lid)
    return r["merged_into"] or lid


def group_ids(conn, pid: int) -> list[int]:
    return [pid] + [r["id"] for r in conn.execute("SELECT id FROM listing WHERE merged_into=%s ORDER BY id", (pid,))]


def _fold_user_data(conn, keep: int, ids: list[int]) -> None:
    """Report des données utilisateur sur la principale : 👍 > 👎, à visiter, notes réunies, dates les plus anciennes."""
    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, reaction, user_status, user_note, seen_at, alerted_at FROM listing WHERE id = ANY(%s)", (ids,))}
    order = [rows[keep]] + [rows[i] for i in ids if i != keep and i in rows]
    reacts = [r["reaction"] for r in order]
    reaction = "like" if "like" in reacts else "dislike" if "dislike" in reacts else None
    status = "to_visit" if any(r["user_status"] == "to_visit" for r in order) else rows[keep]["user_status"]
    notes = []
    for r in order:                 # par ligne : une note déjà reportée n'est pas recopiée à la fusion suivante
        for n in (r["user_note"] or "").splitlines():
            if n.strip() and n.strip() not in notes:
                notes.append(n.strip())
    seen = min((r["seen_at"] for r in order if r["seen_at"]), default=None)
    alerted = min((r["alerted_at"] for r in order if r["alerted_at"]), default=None)
    conn.execute("UPDATE listing SET reaction=%s, user_status=%s, user_note=%s, seen_at=%s, alerted_at=%s WHERE id=%s",
                 (reaction, status, "\n".join(notes) or None, seen, alerted, keep))


def _pair(conn, a, b, status, by, evidence=None):
    lo, hi = min(a, b), max(a, b)
    conn.execute(
        "INSERT INTO duplicate_pair (id_a, id_b, status, decided_by, evidence, decided_at) "
        "VALUES (%s, %s, %s, %s, %s, CASE WHEN %s = 'suggested' THEN NULL ELSE now() END) "
        "ON CONFLICT (id_a, id_b) DO UPDATE SET status=EXCLUDED.status, decided_by=EXCLUDED.decided_by, "
        "evidence=CASE WHEN EXCLUDED.evidence = '{}'::jsonb THEN duplicate_pair.evidence ELSE EXCLUDED.evidence END, "
        "decided_at=EXCLUDED.decided_at", (lo, hi, status, by, Jsonb(evidence or {}), status))


def merge(conn, a: int, b: int, method: str = "user", evidence: dict | None = None) -> int:
    """Fusionne les groupes de a et b ; renvoie l'annonce principale. Une fusion manuelle lève les refus entre eux."""
    _lock(conn)
    pa, pb = principal_of(conn, a), principal_of(conn, b)
    if pa == pb:
        return pa
    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, source, merged_into, enrichment, enrichment_status, reaction, user_status, user_note, photos, "
        "first_seen FROM listing WHERE id = ANY(%s) OR merged_into = ANY(%s)", ([pa, pb], [pa, pb]))}
    ma = [i for i, r in rows.items() if r["merged_into"] == pa]
    mb = [i for i, r in rows.items() if r["merged_into"] == pb]
    if ma and not mb:
        keep = pa                                  # une annonce qui rejoint un groupe ne change pas sa principale
    elif mb and not ma:
        keep = pb
    else:
        keep = min((pa, pb), key=lambda i: _rank(rows[i]))
    other = pb if keep == pa else pa
    moved = [other] + (mb if other == pb else ma)
    kept = [keep] + (ma if keep == pa else mb)
    conn.execute("UPDATE listing SET merged_into=%s, merged_at=now(), merge_method=%s, cluster=NULL WHERE id = ANY(%s)",
                 (keep, method, moved))
    _fold_user_data(conn, keep, kept + moved)
    if method == "user":
        conn.execute("DELETE FROM duplicate_pair WHERE status='rejected' AND ((id_a = ANY(%s) AND id_b = ANY(%s)) "
                     "OR (id_a = ANY(%s) AND id_b = ANY(%s)))", (kept, moved, moved, kept))
    _pair(conn, a, b, "merged", method, evidence)
    group = kept + moved
    conn.execute("DELETE FROM duplicate_pair WHERE status='suggested' AND id_a = ANY(%s) AND id_b = ANY(%s)", (group, group))
    refresh_cluster(conn, keep)
    return keep


def split(conn, principal: int, member: int) -> None:
    """Sépare une annonce secondaire de son groupe ; le refus avec le groupe est mémorisé."""
    _lock(conn)
    r = conn.execute("SELECT merged_into FROM listing WHERE id=%s", (member,)).fetchone()
    if not r or r["merged_into"] != principal:
        raise LookupError(f"{member} n'est pas une source fusionnée de {principal}")
    conn.execute("UPDATE listing SET merged_into=NULL, merged_at=NULL, merge_method=NULL, cluster=NULL, "
                 "dedupe_sig=NULL WHERE id=%s", (member,))
    rest = group_ids(conn, principal)
    conn.execute("UPDATE duplicate_pair SET status='rejected', decided_by='user', decided_at=now() "
                 "WHERE (id_a=%s AND id_b = ANY(%s)) OR (id_b=%s AND id_a = ANY(%s))", (member, rest, member, rest))
    _pair(conn, member, principal, "rejected", "user")
    refresh_cluster(conn, principal)


def reject(conn, a: int, b: int) -> None:
    """« Pas un doublon » : refus mémorisé, suggestions entre les deux groupes retirées."""
    _lock(conn)
    ga, gb = group_ids(conn, principal_of(conn, a)), group_ids(conn, principal_of(conn, b))
    conn.execute("UPDATE duplicate_pair SET status='rejected', decided_by='user', decided_at=now() "
                 "WHERE status='suggested' AND ((id_a = ANY(%s) AND id_b = ANY(%s)) OR (id_a = ANY(%s) AND id_b = ANY(%s)))",
                 (ga, gb, gb, ga))
    _pair(conn, a, b, "rejected", "user")


def suggest(conn, a: int, b: int, evidence: dict) -> bool:
    """Enregistre un doublon possible (sans effet sur une paire refusée ou fusionnée)."""
    lo, hi = min(a, b), max(a, b)
    cur = conn.execute("SELECT status FROM duplicate_pair WHERE id_a=%s AND id_b=%s", (lo, hi)).fetchone()
    if cur and cur["status"] != "suggested":
        return False
    _pair(conn, lo, hi, "suggested", "auto", evidence)
    return not cur


def apply(conn, res: dict, update_sigs: bool = True) -> dict:
    """Applique le résultat d'`analyze` sous verrou : photos génériques, fusions, suggestions, signatures."""
    _lock(conn)
    for lid, idx in res["generic"]:
        conn.execute("UPDATE listing_photo SET generic=true WHERE listing_id=%s AND idx=%s", (lid, idx))
    merged = suggested = 0
    for op, a, b, ev in res["ops"]:
        if op == "merge":
            if principal_of(conn, a) != principal_of(conn, b):
                merge(conn, a, b, method="auto", evidence=ev)
                merged += 1
        elif principal_of(conn, a) != principal_of(conn, b):
            suggested += suggest(conn, a, b, ev)
    if update_sigs:
        for i, sig in res["sigs"].items():
            conn.execute("UPDATE listing SET dedupe_sig=%s WHERE id=%s", (sig, i))
    return {"compared": len(res["changed"]), "merged": merged, "suggested": suggested,
            "generic": len(res["generic"]), "elapsed_s": round(res["elapsed"], 2)}


def process(conn, touched=None, base: Path | None = None) -> dict:
    """Étape du collecteur : empreintes des photos touchées, comparaison des annonces nouvelles ou modifiées,
    fusions/suggestions, puis rafraîchissement des groupes. Inactive tant que l'analyse initiale n'a pas été appliquée."""
    t0 = time.monotonic()
    if not conn.execute("SELECT 1 FROM listing WHERE dedupe_sig IS NOT NULL LIMIT 1").fetchone():
        return {"skipped": "analyse initiale (scripts/dedupe_backfill.py --apply) jamais appliquée"}
    hashed = ensure_hashes(conn, touched, base)
    res = analyze(conn, changed_only=True)
    stats = apply(conn, res)
    refresh_clusters(conn)
    return {**stats, "hashed": hashed, "elapsed_s": round(time.monotonic() - t0, 2)}


# ---------- vue du groupe (cluster) ----------
def _js(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def offer_status(r: dict, now: datetime | None = None) -> str:
    if r["status"] != "active":
        return "gone"
    now = now or datetime.now(timezone.utc)
    if r["source"] in EMAIL_SOURCES and r["last_seen"] and r["last_seen"] < now - timedelta(days=STALE_DAYS):
        return "stale"
    return "active"


def _fill_rank(r: dict) -> tuple:
    return (SOURCE_RANK.get(r["source"], 2), 0 if r.get("enrichment_status") == "done" else 1,
            -len(r.get("photos") or []), r.get("first_seen") or datetime.max.replace(tzinfo=timezone.utc), r["id"])


def _ts(v):
    """datetime UTC depuis une date Python ou une chaîne ISO (price_history est en JSON)."""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return None


def _history(r: dict, price) -> list[dict]:
    """Prix successifs d'une offre : price_history = anciens prix, datés du moment du changement."""
    pts, start = [], _ts(r.get("first_seen"))
    for h in r.get("price_history") or []:
        pts.append({"date": start, "price": h.get("price")})
        start = _ts(h.get("date"))
    pts.append({"date": start, "price": price})
    return pts


def build_cluster(p: dict, members: list[dict], hashes: dict[int, dict], now: datetime | None = None) -> dict | None:
    """Vue matérialisée d'un groupe (design D2). `hashes` = {listing_id: {chemin relatif: (pHash, dHash)}}."""
    if not members:
        return None
    now = now or datetime.now(timezone.utc)
    group = [p] + members
    offers = []
    for r in group:
        offers.append({"id": r["id"], "source": r["source"], "agency": r.get("agency"), "url": r["url"],
                       "title": _own(r, "title"), "price": _own(r, "price"), "status": offer_status(r, now),
                       "first_seen": _js(r.get("first_seen")), "last_seen": _js(r.get("last_seen")),
                       "principal": r["id"] == p["id"]})
    offers.sort(key=lambda o: (o["status"] != "active", o["price"] is None, o["price"] or 0, o["id"]))
    priced = [o for o in offers if o["price"]]
    pool = [o for o in priced if o["status"] == "active"] or priced
    best = min(pool, key=lambda o: o["price"]) if pool else None

    fill, ordered = {}, sorted(members, key=_fill_rank)
    for k in FILL_FIELDS:
        if _own(p, k) is None:
            v = next((_own(m, k) for m in ordered if _own(m, k) is not None), None)
            if v is not None:
                fill[k] = _js(v)
    if p.get("lat") is None or p.get("geo_precision") in (None, "city"):
        mine = PRECISION.get(p.get("geo_precision"), 0) if p.get("lat") is not None else 0
        cands = [m for m in ordered if m.get("lat") is not None and PRECISION.get(m.get("geo_precision"), 0) > mine]
        if cands:
            m = max(cands, key=lambda m: PRECISION.get(m.get("geo_precision"), 0))
            fill.update(lat=m["lat"], lon=m["lon"], geo_precision=m.get("geo_precision"))
            if m.get("address"):
                fill["address"] = m["address"]
            if m.get("commute"):
                fill["commute"] = m["commute"]

    hist = []
    for r in group:
        for pt in _history(r, _own(r, "price")):
            if pt["price"]:
                hist.append({**pt, "source": r["source"], "offer_id": r["id"]})
    floor = datetime.min.replace(tzinfo=timezone.utc)
    hist.sort(key=lambda h: (h["date"] or floor, h["offer_id"]))
    dedup = []
    for h in hist:                      # un prix inchangé (même repris par une autre source) n'est pas répété
        if not dedup or dedup[-1]["price"] != h["price"]:
            dedup.append({**h, "date": _js(h["date"])})
    drop = len(dedup) > 1 and dedup[-1]["price"] < max(h["price"] for h in dedup[:-1])

    shown, seen_h = [], []
    for r in group:
        local = r.get("photos") or []
        if r is p and not local:
            shown += list(r.get("photo_urls") or [])
            continue
        for rel in local:
            h = hashes.get(r["id"], {}).get(rel)
            if h and any(same_photo(h, x) for x in seen_h):
                continue
            if r is not p and not h:
                continue
            shown.append(f"/photos/{rel}")
            if h:
                seen_h.append(h)
    return {"members": [m["id"] for m in members], "offers": offers,
            "price": {"value": best["price"], "offer_id": best["id"]} if best else None,
            "fill": fill, "active": any(r["status"] == "active" for r in group),
            "price_history": dedup, "price_drop": drop, "photos": shown,
            "sources": sorted({r["source"] for r in group}), "refreshed_at": now.isoformat()}


CLUSTER_COLS = ("id, source, url, title, price, surface_m2, land_m2, rooms, bedrooms, city, postal_code, insee, dpe, "
                "ges, dpe_value, dpe_date, year_built, has_garage, has_garden, has_bathtub, property_type, works, "
                "address, lat, lon, geo_precision, commute, agency, status, first_seen, last_seen, price_history, "
                "photos, photo_urls, enrichment, enrichment_status, merged_into")


def refresh_clusters(conn, principals=None) -> int:
    """Recalcule `cluster` de tous les groupes (ou des principales données) ; efface celui des annonces isolées."""
    one = principals is not None
    args = {"p": list(principals or [])} if one else None
    flt = "AND (id = ANY(%(p)s) OR merged_into = ANY(%(p)s))" if one else ""
    rows = conn.execute(f"SELECT {CLUSTER_COLS} FROM listing WHERE (merged_into IS NOT NULL OR id IN "
                        f"(SELECT merged_into FROM listing WHERE merged_into IS NOT NULL)) {flt}", args).fetchall()
    groups: dict[int, dict] = {}
    for r in rows:
        g = groups.setdefault(r["merged_into"] or r["id"], {"p": None, "m": []})
        if r["merged_into"]:
            g["m"].append(r)
        else:
            g["p"] = r
    ids = [r["id"] for r in rows]
    hashes: dict[int, dict] = {}
    for h in conn.execute("SELECT listing_id, path, phash, dhash FROM listing_photo WHERE listing_id = ANY(%s)", (ids,)):
        hashes.setdefault(h["listing_id"], {})[h["path"]] = (from_db(h["phash"]), from_db(h["dhash"]))
    now = datetime.now(timezone.utc)
    for pid, g in groups.items():
        if g["p"] is None:
            continue
        conn.execute("UPDATE listing SET cluster=%s WHERE id=%s",
                     (Jsonb(build_cluster(g["p"], sorted(g["m"], key=lambda m: m["id"]), hashes, now)), pid))
    stale = "AND id = ANY(%(p)s)" if one else ""
    conn.execute(f"UPDATE listing SET cluster=NULL WHERE cluster IS NOT NULL AND (merged_into IS NOT NULL OR "
                 f"NOT EXISTS (SELECT 1 FROM listing m WHERE m.merged_into = listing.id)) {stale}", args)
    return len(groups)


def refresh_cluster(conn, pid: int) -> None:
    refresh_clusters(conn, [pid])
