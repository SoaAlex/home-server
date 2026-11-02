import re
from app.textparse import city_key, detect_type, norm

VIAGER = re.compile(r"\bviager\b|vente a terme|nue[- ]propriete|\bbouquet\b")

# property_type n'est plus surchargé directement par l'enrichissement : il est arbitré par resolve_type().
OVERRIDABLE = ("has_garage", "has_garden", "has_bathtub", "works",
               "land_m2", "dpe", "bedrooms", "price", "surface_m2", "city")


POSITION = ("lat", "lon", "geo_precision", "address", "commute")
FILL_INTERNAL = ("agent_type", "property_type_from")    # verdicts des secondaires, lus par resolve_type seulement


def merged_columns(row: dict) -> dict:
    """Colonnes de l'annonce principale complétées par les autres sources de son groupe (`cluster.fill`) :
    champs vides seulement ; la position (lat/lon/précision/adresse/trajets) remplace un simple centroïde."""
    fill = (row.get("cluster") or {}).get("fill") or {}
    if not fill:
        return row
    out = dict(row)
    for k, v in fill.items():
        if k not in POSITION and k not in FILL_INTERNAL and out.get(k) is None:
            out[k] = v
    if fill.get("lat") is not None and (row.get("lat") is None or row.get("geo_precision") in (None, "city")):
        out.update({k: fill[k] for k in POSITION if k in fill})
    elif fill.get("address") and not row.get("address"):
        out["address"] = fill["address"]
    return out


# ---------- type de maison (spec property-type-resolution, design D6) ----------
TYPES = ("detached", "link_detached", "semi", "terraced")      # verdicts possibles : « unknown » n'en est pas un
TYPE_LABELS = {"detached": "Individuelle", "link_detached": "Accolée par le garage", "semi": "Semi-individuelle",
               "terraced": "Mitoyenne", "unknown": "Inconnu"}
TYPE_POINTS = {"detached": 12, "link_detached": 10, "semi": 7}            # mitoyenne ou inconnue : 2
SOURCE_LABELS = {"user": "utilisateur", "agent": "agent", "map": "carte", "listing": "annonce"}
_SIDE = {"left": "gauche", "right": "droite", "rear": "arrière", "front": "avant", "unknown": "côté ?"}
_EVIDENCE = {"house": "maison", "garage": "garage", "annex": "annexe", "free": "libre", "unknown": "?"}
_SEEN = {"listing_photos": "photos de l'annonce", "street_view": "vue de rue", "aerial": "vue aérienne",
         "map": "carte", "text": "texte"}
_WARN = {"multi_dwelling": "plusieurs logements", "large_footprint": "bâtiment très grand",
         "no_building": "bâtiment introuvable", "fallback_target": "bâtiment déduit de la parcelle",
         "ban_mismatch": "adresse non retrouvée dans la BAN", "probable_address": "adresse probable"}


def _m(x) -> str:
    return f"{round(x)} m"


def _map_detail(adj: dict) -> str:
    walls = [n for n in adj.get("neighbours") or [] if n["kind"] == "living"]
    links = [n for n in adj.get("neighbours") or [] if n["kind"] == "annex_link"]
    parts = []
    if walls:
        parts.append(f"{len(walls)} mur{'s' if len(walls) > 1 else ''} : " + ", ".join(_m(n["length_m"]) for n in walls))
    if links:
        parts.append("liaison par annexe : " + ", ".join(_m(n["length_m"]) for n in links))
    return " · ".join(parts) or "aucun mur commun"


def _agent_detail(ev) -> str:
    if not isinstance(ev, dict):
        return "ancien verdict, sans preuves"
    parts = [f"gauche : {_EVIDENCE.get(ev.get('left'), '?')}", f"droite : {_EVIDENCE.get(ev.get('right'), '?')}"]
    if ev.get("photos"):
        parts.append("photos " + ", ".join(str(i) for i in ev["photos"]))
    seen = [_SEEN.get(x, x) for x in ev.get("seen_on") or []]
    if seen:
        parts.append(" + ".join(seen))
    return " · ".join(parts)


def _verdicts(row: dict) -> list[dict]:
    """Verdicts disponibles avec leur rang de priorité (1 = le plus fort ; None = information seulement)."""
    enr = row.get("enrichment") or {}
    fill = (row.get("cluster") or {}).get("fill") or {}
    out = []
    user = enr.get("property_type_user")
    if user in TYPES or user == "unknown":           # choix explicite de l'utilisateur, « inconnu » compris
        out.append({"source": "user", "type": user, "rank": 1})
    agent, ev, frm = enr.get("property_type"), enr.get("type_evidence"), None
    fa = fill.get("agent_type") or {}
    if agent not in TYPES and fa.get("type") in TYPES:                 # verdict de l'agent sur une secondaire
        agent, ev, frm = fa["type"], fa.get("evidence"), fa.get("from")
    if agent in TYPES:
        has_ev = isinstance(ev, dict)
        out.append({"source": "agent", "type": agent, "rank": 2 if has_ev else 4, "evidence": has_ev,
                    "_ev": ev, "from": frm})
    adj = row.get("adjacency") or {}
    if adj.get("status") == "ok" and adj.get("type") in TYPES:
        conf = (adj.get("input") or {}).get("confidence")
        usable = bool(adj.get("usable"))
        out.append({"source": "map", "type": adj["type"], "rank": (5 if conf == "probable" else 3) if usable else None,
                    "usable": usable, "address": conf, "_adj": adj})
    col, src, frm = row.get("property_type"), row.get("property_type_src"), None
    if col not in TYPES and fill.get("property_type") in TYPES:          # type déclaré par une secondaire
        col, src, frm = fill["property_type"], fill.get("property_type_src"), fill.get("property_type_from")
    if col in TYPES:
        out.append({"source": "listing", "type": col, "rank": 6, "strength": src, "from": frm})
    return out


def _counts(v: dict) -> bool:
    """Verdict qui crée un désaccord s'il diffère du type retenu (un texte faible n'en crée pas)."""
    if v["source"] == "map":
        return v["usable"]
    if v["source"] == "listing":
        return v.get("strength") in ("structured", "text_explicit")
    return v["source"] == "agent"


def _confidence(v: dict | None) -> str:
    if not v:
        return "low"
    if v["source"] == "user" or (v["source"] == "agent" and v["evidence"]) or (v["source"] == "map" and v["rank"] == 3):
        return "high"
    if v["source"] in ("agent", "map") or v.get("strength") in ("structured", "text_explicit"):
        return "medium"
    return "low"


def resolve_type(row: dict, explain: bool = False) -> dict:
    """Type retenu : premier verdict dans l'ordre de priorité, avec source, confiance et désaccord.
    `explain=True` ajoute la preuve courte de chaque verdict (expression du texte comprise)."""
    vs = _verdicts(row)
    used = min((v for v in vs if v["rank"]), key=lambda v: v["rank"], default=None)
    typ = used["type"] if used else "unknown"
    disagreement = any(v is not used and v["type"] != typ and _counts(v) for v in vs)
    info = {"type": typ, "label": TYPE_LABELS[typ], "source": used and used["source"],
            "confidence": _confidence(used), "disagreement": disagreement}
    if not explain:
        return info
    phrase = None
    if any(v["source"] == "listing" and (v.get("strength") or "").startswith("text") and not v.get("from") for v in vs):
        t, _, phrase = detect_type(f"{row.get('title') or ''} {row.get('description') or ''}")
        phrase = phrase if t == row.get("property_type") else None
    verdicts = []
    for v in vs:
        if v["source"] == "map":
            adj = v["_adj"]
            detail = _map_detail(adj)
            warns = [_WARN.get(w, w) for w in adj.get("warnings") or [] if w != "ban_mismatch" or not v["usable"]]
            if warns:
                detail += " (" + ", ".join(warns) + ")"
            if not v["usable"]:
                detail += " — non utilisée"
        elif v["source"] == "agent":
            detail = _agent_detail(v["_ev"])
        elif v["source"] == "listing":
            strength = v.get("strength")
            detail = ("champ structuré" if strength == "structured" else f"« {phrase} »" if phrase
                      else "texte" if strength else "ancien calcul")
            if strength == "text_weak":
                detail += " (indice faible)"
        else:
            detail = "correction manuelle"
        if v.get("from"):
            detail += f" — annonce #{v['from']} du même bien"
        verdicts.append({"source": v["source"], "source_label": SOURCE_LABELS[v["source"]], "type": v["type"],
                         "label": TYPE_LABELS[v["type"]], "used": v is used, "detail": detail,
                         "disagrees": v is not used and v["type"] != typ and _counts(v)})
    used_v = next((x for x in verdicts if x["used"]), None)
    info["verdicts"] = verdicts
    info["summary"] = (f"{info['label']} · {SOURCE_LABELS[used['source']]} ({used_v['detail']})" if used
                       else info["label"])
    return info


def type_info(row: dict) -> dict:
    return resolve_type(row, explain=True)


# ---------- contrôle des champs du type envoyés par l'agent (design D14) ----------
SIDES = ("house", "garage", "annex", "free", "unknown")
SEEN_ON = ("listing_photos", "street_view", "aerial", "map", "text")
EVIDENCE_KEYS = ("left", "right", "seen_on", "photos", "notes")
NOTES_MAX = 300


def type_from_sides(left: str, right: str) -> str | None:
    """Type qu'impliquent deux côtés qualifiés (None si l'un est inconnu)."""
    if "unknown" in (left, right):
        return None
    houses = (left, right).count("house")
    if houses:
        return "terraced" if houses == 2 else "semi"
    return "link_detached" if {left, right} & {"garage", "annex"} else "detached"


def check_type_fields(body: dict) -> str | None:
    """Message d'erreur (réponse 422) si `property_type` / `type_evidence` sont invalides, sinon None."""
    typ = body.get("property_type")
    if typ is not None and typ not in TYPES:
        return (f"property_type = {' | '.join(TYPES)} (reçu {typ!r}) ; si le type n'est pas établi, "
                "ne pas envoyer property_type")
    if "type_evidence" not in body or body["type_evidence"] is None:
        return None
    ev = body["type_evidence"]
    if not isinstance(ev, dict):
        return "type_evidence = objet {left, right, seen_on, photos, notes}"
    extra = sorted(set(ev) - set(EVIDENCE_KEYS))
    if extra:
        return f"type_evidence : clés inconnues {extra} (attendues : {', '.join(EVIDENCE_KEYS)})"
    for side in ("left", "right"):
        if ev.get(side) not in SIDES:
            return f"type_evidence.{side} = {' | '.join(SIDES)} (reçu {ev.get(side)!r})"
    seen = ev.get("seen_on", [])
    if not isinstance(seen, list) or any(x not in SEEN_ON for x in seen):
        return f"type_evidence.seen_on = liste parmi {', '.join(SEEN_ON)} (reçu {seen!r})"
    photos = ev.get("photos", [])
    if not isinstance(photos, list) or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in photos):
        return f"type_evidence.photos = liste d'indices de photos (entiers ≥ 0) (reçu {photos!r})"
    notes = ev.get("notes", "")
    if not isinstance(notes, str) or len(notes) > NOTES_MAX:
        return f"type_evidence.notes = texte de {NOTES_MAX} caractères au plus"
    implied = type_from_sides(ev["left"], ev["right"])
    if typ and implied and typ != implied:
        return (f"property_type {typ!r} incohérent avec les côtés (gauche : {ev['left']}, droite : {ev['right']}) "
                f"qui correspondent à {implied!r}")
    return None


def effective(row: dict) -> dict:
    """Champs de l'annonce (complétés par son groupe), surchargés par l'enrichissement (agent Hermes) ;
    pour un bien multi-sources, le prix retenu est le plus bas des offres actives (`cluster.price`).
    Le type de maison est celui que retient l'arbitrage entre sources (resolve_type)."""
    enr = row.get("enrichment") or {}
    out = {**merged_columns(row), **{k: enr[k] for k in OVERRIDABLE if enr.get(k) is not None}}
    out["property_type"] = resolve_type(row)["type"]
    cp = ((row.get("cluster") or {}).get("price") or {}).get("value")
    if cp is not None:
        out["price"] = cp
    return out


def score_listing(row: dict, crit: dict) -> tuple[int, dict, str | None]:
    r = effective(row)
    enr = row.get("enrichment") or {}
    if not r.get("city"):
        return 0, {}, "à compléter"
    sale = ((row.get("raw") or {}).get("sale_status") or "").lower()
    if sale in ("vendu", "sous compromis"):
        return 0, {}, sale
    if VIAGER.search(norm(f"{r.get('title') or ''} {r.get('description') or ''}")):
        return 0, {}, "viager / vente à terme"
    ck = city_key(r.get("city"), r.get("postal_code"))
    pref = [city_key(c) for c in crit["preferred_cities"]]
    other = {city_key(c) for c in crit.get("other_cities", [])}
    excl = {city_key(c) for c in crit.get("excluded_cities", [])}

    if ck in excl:
        return 0, {}, "ville exclue"
    if ck not in pref and ck not in other:
        return 0, {}, "hors zone"
    price = r.get("price")
    if price and price > crit["price"]["max"] and not enr.get("user_added"):   # ajout manuel : budget assumé
        return 0, {}, "prix > max"
    if crit["require"].get("garage") and r.get("has_garage") is False:
        return 0, {}, "pas de garage"
    if crit["require"].get("garden") and r.get("has_garden") is False:
        return 0, {}, "pas de jardin"

    d = {}
    d["ville"] = 25 - pref.index(ck) if ck in pref else 5
    d["prix"] = (0 if not price else 15 if price <= 400000 else
                 12 if price <= crit["price"]["target"] else 5 if price <= 500000 else 0)
    d["type"] = TYPE_POINTS.get(r.get("property_type"), 2)
    land = float(r.get("land_m2") or 0)
    d["jardin"] = 12 if land >= 400 else 8 if land >= 250 else 4 if land >= 150 else 1
    if enr.get("garden_shape") == "long":
        d["jardin"] -= 3
    d["dpe"] = {"A": 8, "B": 8, "C": 6, "D": 3, "E": 0, "F": -5, "G": -5}.get(
        (r.get("dpe") or "").upper(), 0)
    d["travaux"] = {"none": 8, "light": 3, "heavy": -8}.get(r.get("works"), 0)
    d["baignoire"] = 3 if r.get("has_bathtub") else 0
    d["chambres"] = (3 if (r.get("bedrooms") or 0) >= 3 else 0) + \
                    (4 if (enr.get("largest_bedroom_m2") or 0) >= 14 else 0)
    total_min = (r.get("commute") or {}).get("total_min")
    d["trajet"] = (0 if total_min is None else 8 if total_min <= 30 else
                   5 if total_min <= 40 else 2 if total_min <= 50 else 0)
    d["style"] = -4 if enr.get("style") == "cubic" else 0
    return max(0, min(100, sum(d.values()))), d, None


def fingerprint(r: dict) -> str | None:
    if not (r.get("price") and r.get("surface_m2")):
        return None
    return "|".join([city_key(r.get("city"), r.get("postal_code")),
                     str(round(float(r["price"]) / 10000)),
                     str(round(float(r["surface_m2"]) / 5)),
                     str(round(float(r.get("land_m2") or 0) / 50))])
