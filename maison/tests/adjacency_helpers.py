"""Scènes synthétiques pour les tests de mitoyenneté : rectangles en mètres autour d'une origine arbitraire,
en mer (aucune adresse réelle). Repère : x vers l'est, y vers le nord ; la rue longe le sud (y < -1).

Disposition par défaut : maison « T » de 6 m de façade sur 9 m de profondeur (x 0..6, y 0..9), sur la parcelle
« P0 » (x 0..6, y -1..30), avec une parcelle derrière ; point d'adresse sur le trottoir devant (3, -2).
"""
from app import adjacency as adj

ORIGIN = (-5.0, 46.0)
KX, KY = adj._scale(ORIGIN[1])


def ll(x, y):
    return [ORIGIN[0] + x / KX, ORIGIN[1] + y / KY]


def rect(x0, y0, x1, y1):
    return [[ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1), ll(x0, y0)]]


def building(bid, x0, y0, x1, y1, h=6.0, usage="Résidentiel", logts=1, legere=False, etat="En service"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": rect(x0, y0, x1, y1)},
            "properties": {"cleabs": bid, "usage_1": usage, "hauteur": h, "nombre_de_logements": logts,
                           "construction_legere": legere, "etat_de_l_objet": etat, "nombre_d_etages": 2,
                           "date_d_apparition": "1931-01-01", "identifiants_rnb": f"RNB-{bid}"}}


def parcel(idu, x0, y0, x1, y1):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": rect(x0, y0, x1, y1)},
            "properties": {"idu": idu, "contenance": round((x1 - x0) * (y1 - y0))}}


def house(**kw):
    return building("T", 0, 0, 6, 9, **kw)


def plot(idu="P0", x0=0, x1=6):
    """Parcelle de façade sur rue, avec la parcelle de fond correspondante (la limite arrière n'est pas sur rue)."""
    return [parcel(idu, x0, -1, x1, 30), parcel(f"{idu}-fond", x0, 30, x1, 60)]


def scene(buildings, parcels, link=True, address_xy=(3, -2), confidence="exact", **extra):
    """Contexte au format de `adjacency.fetch()`."""
    lon, lat = ll(*address_xy)
    ctx = {"lon": lon, "lat": lat, "address": "1 rue de l'Exemple 00000 Nulle-Part",
           "ban": {"id": "00000_0000_00001", "label": "1 rue de l'Exemple", "lon": lon, "lat": lat, "score": 0.97,
                   "distance_m": 0.0},
           "warnings": [], "links": [{"id_adr": "00000_0000_00001", "id_bat": "T", "idu": "P0"}] if link else [],
           "buildings": buildings, "parcels": parcels, "confidence": confidence}
    ctx.update(extra)
    return ctx
