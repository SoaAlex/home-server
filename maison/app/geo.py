import time
from datetime import date, timedelta

BAN = "https://api-adresse.data.gouv.fr/search/"
OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}?overview=false"
ADEME = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines"


def geocode(client, q: str, citycode: str | None = None, kind: str | None = None):
    params = {"q": q, "limit": 1}
    if citycode:
        params["citycode"] = citycode
    if kind:
        params["type"] = kind
    f = client.get(BAN, params=params).json().get("features") or []
    if not f:
        return None
    lon, lat = f[0]["geometry"]["coordinates"]
    p = f[0]["properties"]
    return {"lat": lat, "lon": lon, "label": p["label"], "citycode": p.get("citycode"), "type": p.get("type")}


def osrm_minutes(client, lon1, lat1, lon2, lat2) -> int | None:
    r = client.get(OSRM.format(a=f"{lon1},{lat1}", b=f"{lon2},{lat2}"))
    routes = r.json().get("routes") or []
    return round(routes[0]["duration"] / 60) if routes else None


def commute(client, lat, lon, destinations: dict) -> dict:
    """Minutes en voiture vers chaque destination ; total_min = somme (un aller pour chacun)."""
    out = {}
    for k, d in destinations.items():
        out[k] = osrm_minutes(client, lon, lat, d["lon"], d["lat"])
        time.sleep(1)
    vals = [v for v in out.values() if v is not None]
    out["total_min"] = sum(vals) if len(vals) == len(destinations) else None
    return out


def _d(v):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def ademe_address(client, insee: str, dpe: str | None, surface: float | None,
                  dpe_date=None, dpe_value: int | None = None):
    """Cherche le DPE de la maison dans la base ADEME ; renvoie l'adresse si un seul candidat."""
    if not (dpe_date or dpe_value):
        return None
    dd = _d(dpe_date)
    # type_batiment est souvent absent des DPE récents : on ne l'exige que sans date (sinon trop de faux négatifs)
    q = [f"code_insee_ban:{insee}"] + ([] if dd else ["type_batiment:maison"])
    if dpe:
        q.append(f"etiquette_dpe:{dpe}")
    if dpe_value:   # les agences arrondissent (117,9 kWh affiché « 120 ») : ±5 %
        q.append(f"conso_5_usages_par_m2_ep:[{int(dpe_value * 0.95)} TO {int(dpe_value * 1.05) + 1}]")
    if surface:
        q.append(f"surface_habitable_logement:[{surface * 0.95:.1f} TO {surface * 1.05:.1f}]")
    if dd:
        q.append(f"date_etablissement_dpe:[{dd - timedelta(days=3)} TO {dd + timedelta(days=3)}]")
    r = client.get(ADEME, params={"qs": " AND ".join(q), "size": 5,
                                  "select": "adresse_ban,_geopoint,date_etablissement_dpe,surface_habitable_logement"})
    data = r.json()
    res = data.get("results") or []
    addrs = {x.get("adresse_ban") for x in res}
    if not res or len(addrs) != 1 or not res[0].get("_geopoint"):
        return None
    lat, lon = (float(x) for x in res[0]["_geopoint"].split(","))
    return {"address": res[0]["adresse_ban"], "lat": lat, "lon": lon, "method": "ademe_dpe"}
