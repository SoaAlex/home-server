#!/usr/bin/env python3
"""Client de l'API locale Maison pour l'agent d'enrichissement (cron Hermes).

Évite curl + URL http + jeton en ligne de commande (bloqués par le scan de sécurité du cron).
  maison pending [--standard|--investigations|--all] [N]
                                      -> liste JSON des annonces en attente.
       --standard       file hors demandes d'investigation (job maison-enrichment) ; copie /tmp/pending.json
       --investigations demandes « Investiguer » (address_request), la plus ancienne d'abord (job
                        maison-address-investigation) ; copie /tmp/pending_investigations.json
       --all (défaut)   tout, comme avant (les investigations en tête) ; copie /tmp/pending.json
  maison show ID                      -> fiche JSON
  maison post ID FICHIER.json         -> POST d'enrichissement
  maison fail ID "message"            -> marque l'annonce en échec
  maison ademe "QS" [lon,lat,450m]    -> relais API DPE ADEME (qs Lucene, geo_distance optionnel)
  maison ban "adresse" [citycode]     -> relais BAN
  maison reverse LON LAT              -> adresses les plus proches d'un point (BAN reverse)
  maison cadastre LON LAT [RAYON_M] [MIN_M2 MAX_M2] -> parcelles cadastrales autour, filtrées par surface
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://host.docker.internal:8090"
ENV = "/volume1/docker/maison/.env"
FETCH_MAX = 250   # --standard / --investigations : l'API trie les investigations en tête, on filtre ici
PENDING_MODES = ("--standard", "--investigations", "--all")
PENDING_COPY = {"all": "/tmp/pending.json", "standard": "/tmp/pending.json",
                "investigations": "/tmp/pending_investigations.json"}   # fichier distinct : les 2 jobs ne s'écrasent pas


def token():
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("ENRICH_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("ENRICH_TOKEN introuvable")


def call(method, path, params=None, body=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Token": token(), "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # pas de proxy egress
    try:
        with opener.open(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read()[:500].decode(errors='replace')}")


def is_investigation(x):
    return bool((x.get("enrichment") or {}).get("address_request"))


def pending(args):
    mode, n = "all", 6
    for arg in args:
        if arg in PENDING_MODES:
            mode = arg[2:]
        else:
            n = int(arg)
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
    slim = []
    for x in out[:n]:
        keep = {k: x.get(k) for k in ("id", "source", "url", "title", "description", "price", "city",
                                      "postal_code", "surface_m2", "land_m2", "rooms", "bedrooms", "dpe",
                                      "lat", "lon", "geo_precision", "address", "excluded_reason",
                                      "has_garage", "has_garden", "blur", "ges", "year_built",
                                      "published_at", "agency")}
        keep["address_request"] = (x.get("enrichment") or {}).get("address_request")
        keep["address_investigations"] = (x.get("enrichment") or {}).get("address_investigations")
        keep["photo_urls"] = (x.get("photo_urls") or [])[:8]
        slim.append(keep)
    json.dump(slim, open(PENDING_COPY[mode], "w"), ensure_ascii=False)
    print(json.dumps(slim, ensure_ascii=False, indent=1))


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
    elif cmd == "cadastre":
        p = {"lon": a[1], "lat": a[2], "radius_m": a[3] if len(a) > 3 else 300}
        if len(a) > 5:
            p.update(min_m2=a[4], max_m2=a[5])
        print(json.dumps(call("GET", "/api/tools/cadastre", p), ensure_ascii=False, indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
