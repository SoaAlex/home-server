"""Capture des fixtures réelles pour les tests des collecteurs (à relancer si un site change)."""
import json
import httpx
from app.settings import USER_AGENT, load_criteria

crit = load_criteria()
zones = [z for c in crit["preferred_cities"] for z in crit["zones"][c]["zone_ids"]]
h = {"User-Agent": USER_AGENT}
f = {"size": 20, "from": 0, "filterType": "buy", "propertyType": ["house"], "maxPrice": 550000,
     "page": 1, "sortBy": "publicationDate", "sortOrder": "desc", "onTheMarket": [True],
     "zoneIdsByTypes": {"zoneIds": zones}}
with httpx.Client(headers=h, timeout=30, follow_redirects=True) as c:
    r = c.get("https://www.bienici.com/realEstateAds.json", params={"filters": json.dumps(f)})
    print("bienici", r.status_code); open("tests/fixtures/bienici.json", "w").write(r.text)
    r = c.get("https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces",
              params=dict(page=1, parPage=50, prixMax=550000, typeBiens="MAI",
                          typeTransactions="VENTE", departements=59))
    print("notaires", r.status_code); open("tests/fixtures/notaires.json", "w").write(r.text)
    r = c.get("https://www.maesimmobilier.fr/wp-json/wp/v2/biens", params={"per_page": 100})
    print("maes list", r.status_code); open("tests/fixtures/maes_list.json", "w").write(r.text)
    r = c.get("https://www.maesimmobilier.fr/bien-immobilier/maison-semi-individuelle-garage-jardin-parking/")
    print("maes detail", r.status_code); open("tests/fixtures/maes_detail.html", "w").write(r.text)
