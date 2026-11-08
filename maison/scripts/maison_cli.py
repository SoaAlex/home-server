#!/usr/bin/env python3
"""Client de l'API locale Maison pour l'agent d'enrichissement (cron Hermes).

Évite curl + URL http + jeton en ligne de commande (bloqués par le scan de sécurité du cron).
  maison pending [--standard|--investigations|--types|--all] [N]
                                      -> liste JSON des annonces en attente (user_comments = commentaires
                                         de l'utilisateur : indices en lecture seule).
       --standard       file hors demandes d'investigation (job maison-enrichment) ; copie /tmp/pending.json
       --investigations demandes « Investiguer » (address_request), la plus ancienne d'abord (job
                        maison-address-investigation) ; copie /tmp/pending_investigations.json
       --types          demandes de vérification du type (job maison-type-check) ; copie /tmp/pending_types.json
       --all (défaut)   tout, comme avant (les investigations en tête) ; copie /tmp/pending.json
  maison show ID                      -> fiche JSON
  maison post ID FICHIER.json         -> POST d'enrichissement
  maison fail ID "message"            -> marque l'annonce en échec
  maison ademe "QS" [lon,lat,450m]    -> relais API DPE ADEME (qs Lucene, geo_distance optionnel)
  maison ban "adresse" [citycode]     -> relais BAN
  maison reverse LON LAT              -> adresses les plus proches d'un point (BAN reverse)
  maison cadastre LON LAT [RAYON_M] [MIN_M2 MAX_M2] -> parcelles cadastrales autour, filtrées par surface
  maison mitoyennete ID               -> type retenu + calcul cartographique ; vue aérienne annotée (fichier)
  maison mitoyennete-adresse "ADRESSE" | LON LAT
                                      -> mitoyenneté d'une adresse candidate (rien n'est enregistré) + vue aérienne
  maison planche ID                   -> planche des photos numérotées (fichier)
  maison rue ID                       -> vue de rue Panoramax orientée vers la maison (fichier + date, auteur)
Les images sont écrites dans $TMPDIR (sinon /tmp) ; la commande affiche leur chemin absolu (vision_analyze).

Un seul fichier pour les deux copies de l'agent : ~/.local/bin/maison (backend local, API sur
http://127.0.0.1:8090) et ~/.hermes/sandboxes/docker/default/workspace/bin/maison (sandbox Docker, API sur
http://host.docker.internal:8090). MAISON_URL surcharge l'adresse. Le jeton ENRICH_TOKEN est lu dans
/volume1/docker/maison/.env, passe dans l'en-tête X-Token et n'est jamais affiché ni mis dans une URL.
"""
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://host.docker.internal:8090" if os.path.exists("/.dockerenv") else "http://127.0.0.1:8090"
BASE = (os.environ.get("MAISON_URL") or DEFAULT_BASE).rstrip("/")
ENV = "/volume1/docker/maison/.env"
FETCH_MAX = 250   # --standard / --investigations : l'API trie les investigations en tête, on filtre ici
PENDING_MODES = ("--standard", "--investigations", "--types", "--all")
PENDING_COPY = {"all": "/tmp/pending.json", "standard": "/tmp/pending.json",
                "investigations": "/tmp/pending_investigations.json",   # fichiers distincts : les jobs ne s'écrasent pas
                "types": "/tmp/pending_types.json"}

# Le cron peut tourner sans LANG : forcer l'UTF-8 (accents des annonces).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def token():
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("ENRICH_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("ENRICH_TOKEN introuvable")


def call(method, path, params=None, body=None, raw=False):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Token": token(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # jamais de proxy pour l'API locale
    try:
        with opener.open(req, timeout=120) as r:
            content = r.read()
            return content if raw else json.loads(content or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"Connexion impossible à {BASE} : {e.reason}")


def write_pending_copy(slim, path):
    """Copie de la liste via fichier temporaire + rename (ne suit pas un lien symbolique posé à l'avance)."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".pending.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def is_investigation(x):
    return bool((x.get("enrichment") or {}).get("address_request"))


def adjacency_summary(a):
    """Résumé du calcul cartographique de la mitoyenneté (sans géométrie) pour le JSON allégé."""
    if not a:
        return None
    t = a.get("target") or {}
    return {"status": a.get("status"), "type": a.get("type"), "usable": a.get("usable"),
            "warnings": a.get("warnings"), "reason": a.get("reason"), "error": a.get("error"),
            "neighbours": [{k: n.get(k) for k in ("kind", "side", "length_m", "ours", "theirs")}
                           for n in a.get("neighbours") or []],
            "target": {k: t.get(k) for k in ("method", "area_m2", "height_m", "floors", "dwellings", "year")} if t else None}


def pending(args):
    mode, n = "all", 6
    for arg in args:
        if arg in PENDING_MODES:
            mode = arg[2:]
        else:
            n = int(arg)
    if mode == "types":
        out = call("GET", "/api/enrichment/type_checks", {"limit": n})
    else:
        out = call("GET", "/api/enrichment/pending", {"limit": n if mode == "all" else FETCH_MAX})
    if mode == "investigations":
        out = sorted((x for x in out if is_investigation(x)),
                     key=lambda x: x["enrichment"]["address_request"].get("requested_at") or "")
    elif mode == "standard":
        waiting = sum(is_investigation(x) for x in out)
        out = [x for x in out if not is_investigation(x)]
        if waiting:
            print(f"# {waiting} investigation(s) d'adresse en attente : traitées par le job "
                  "maison-address-investigation, ne pas les traiter ici", file=sys.stderr)
    slim = [slim_entry(x, mode) for x in out[:n]]
    write_pending_copy(slim, PENDING_COPY[mode])
    print(json.dumps(slim, ensure_ascii=False, indent=1))


def slim_entry(x, mode="all"):
    """Entrée allégée de la file : champs utiles à l'agent, photos tronquées, commentaires de l'utilisateur."""
    keep = {k: x.get(k) for k in ("id", "source", "url", "title", "description", "price", "city",
                                  "postal_code", "surface_m2", "land_m2", "rooms", "bedrooms", "dpe",
                                  "lat", "lon", "geo_precision", "address", "excluded_reason",
                                  "has_garage", "has_garden", "blur", "ges", "year_built",
                                  "published_at", "agency")}
    keep["address_request"] = (x.get("enrichment") or {}).get("address_request")
    keep["address_investigations"] = (x.get("enrichment") or {}).get("address_investigations")
    keep["photo_urls"] = (x.get("photo_urls") or [])[:8]
    keep["type_info"] = x.get("type_info")
    keep["adjacency"] = adjacency_summary(x.get("adjacency"))
    keep["user_comments"] = x.get("user_comments") or []    # indices de l'utilisateur, jamais une preuve
    if mode == "types":
        enr = x.get("enrichment") or {}
        keep.update(type_request=enr.get("type_request"), type_checks=enr.get("type_checks"),
                    type_evidence=enr.get("type_evidence"), enrichment_status=x.get("enrichment_status"))
    return keep


def save_image(content, name):
    """Écrit une image dans $TMPDIR (sinon /tmp) et renvoie son chemin absolu."""
    path = os.path.abspath(os.path.join(os.environ.get("TMPDIR") or "/tmp", name))
    with open(path, "wb") as f:
        f.write(content)
    return path


def mitoyennete(lid):
    x = call("GET", f"/api/listings/{lid}")
    out = {"id": x.get("id"), "address": x.get("address"), "geo_precision": x.get("geo_precision"),
           "type_info": x.get("type_info"), "adjacency": adjacency_summary(x.get("adjacency"))}
    a = x.get("adjacency") or {}
    if a.get("status") in ("ok", "no_building"):
        out["aerial"] = save_image(call("GET", "/api/tools/aerial", {"listing_id": x["id"]}, raw=True),
                                   f"maison_aerial_{x['id']}.jpg")
    else:
        out["aerial"] = None
    print(json.dumps(out, ensure_ascii=False, indent=1))


def mitoyennete_adresse(args):
    params = {"lon": args[0], "lat": args[1]} if len(args) == 2 else {"address": args[0]}
    res = call("GET", "/api/tools/adjacency", params)
    inp = res.get("input") or {}
    out = {**adjacency_summary(res), "input": inp, "orientation": res.get("orientation")}
    if res.get("status") in ("ok", "no_building") and inp.get("lon") is not None:
        tag = f"{float(inp['lon']):.5f}_{float(inp['lat']):.5f}"
        out["aerial"] = save_image(call("GET", "/api/tools/aerial", params, raw=True), f"maison_aerial_{tag}.jpg")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def rue(lid):
    meta = call("GET", f"/api/listings/{lid}/street")
    if meta.get("available"):
        meta["file"] = save_image(call("GET", meta["image_url"].split("?")[0], raw=True), f"maison_rue_{lid}.jpg")
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def main(a):
    if not a:
        sys.exit(__doc__)
    cmd = a[0]
    if cmd == "pending":
        pending(a[1:])
    elif cmd == "show":
        print(json.dumps(call("GET", f"/api/listings/{int(a[1])}"), ensure_ascii=False, indent=1))
    elif cmd == "post":
        body = json.load(open(a[2], encoding="utf-8"))
        print(json.dumps(call("POST", f"/api/listings/{int(a[1])}/enrichment", body=body)))
    elif cmd == "fail":
        print(json.dumps(call("POST", f"/api/listings/{int(a[1])}/enrichment",
                              body={"_failed": True, "error": a[2] if len(a) > 2 else ""})))
    elif cmd == "ademe":
        p = {"qs": a[1], "size": 50}
        if len(a) > 2:
            p["geo_distance"] = a[2]
        print(json.dumps(call("GET", "/api/tools/ademe", p), ensure_ascii=False, indent=1))
    elif cmd == "ban":
        p = {"q": a[1]}
        if len(a) > 2:
            p["citycode"] = a[2]
        print(json.dumps(call("GET", "/api/tools/ban", p), ensure_ascii=False, indent=1))
    elif cmd == "reverse":
        print(json.dumps(call("GET", "/api/tools/reverse", {"lon": a[1], "lat": a[2]}), ensure_ascii=False, indent=1))
    elif cmd == "mitoyennete":
        mitoyennete(int(a[1]))
    elif cmd == "mitoyennete-adresse":
        mitoyennete_adresse(a[1:])
    elif cmd == "planche":
        lid = int(a[1])
        print(save_image(call("GET", f"/api/listings/{lid}/contact_sheet.jpg", raw=True), f"maison_planche_{lid}.jpg"))
    elif cmd == "rue":
        rue(int(a[1]))
    elif cmd == "cadastre":
        p = {"lon": a[1], "lat": a[2], "radius_m": a[3] if len(a) > 3 else 300}
        if len(a) > 5:
            p.update(min_m2=a[4], max_m2=a[5])
        print(json.dumps(call("GET", "/api/tools/cadastre", p), ensure_ascii=False, indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
