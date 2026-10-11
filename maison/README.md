# Maison — recherche automatisée

Stack privée (LAN + VPN uniquement, pas de proxy host NPM) : http://192.168.1.55:8090

| Service | Rôle |
|---------|------|
| `maison-web` | FastAPI : UI (liste filtrable, carte, fiche) + API (`/api/listings`, `/api/enrichment/*`) |
| `maison-collector` | Boucle ~horaire : Bien'ici JSON, notaires JSON, Maes (WordPress), agences Netty (`netty_agencies` dans criteria.yaml), petites agences à parseur dédié (`app/sources/agences.py` : Référence Immobilière, Immobilière de Croix, Dreossi, Immobilier & Compagnie), **PAP.fr** (`app/sources/pap.py`, curl_cffi + JSON-LD des fiches), alertes e-mail IMAP → score → photos/géo/trajets/DPE→adresse → alerte Discord |

- Base `maison` (rôle `maison`) sur `common-postgres`, réseau `common-network`. Tests : base `maison_test`.
- Critères modifiables : `config/criteria.yaml` (privé, git-ignoré ; modèle `config/criteria.example.yaml`) (pris en compte au cycle suivant). Ajouter une agence Netty : trouver son `username`
  dans les URL d'images `img.netty.immo/productcw/<username>/…` de son site, ajouter une ligne `netty_agencies`
  (même `group` si elle partage le portefeuille d'une autre), tester avec `python scripts/netty_dryrun.py`. Ajouter une ville :
  l'ajouter à `preferred_cities`/`other_cities` puis relancer `scripts/resolve_zones.py`.
- Photos : `data/photos/<id>/NN.jpg` (git-ignoré).
- Enrichissement (adresse, analyse photos, fiches e-mail) : cron Hermes `maison-enrichment`
  (`20 * * * *`) + skill du même nom → `POST /api/listings/{id}/enrichment` (jeton `ENRICH_TOKEN`).
- leboncoin / SeLoger / Jinka : jamais scrapés directement ; uniquement via leurs alertes e-mail sur un Gmail
  dédié (`IMAP_USER`, `IMAP_PASSWORD` = mot de passe d'application).
- PAP.fr : indexé directement — recherche maison par ville (`/json/ac-geo` → `-g<ID>`, pagination
  `/proximite/…-g<ID>-<N>`), fiches JSON-LD (prix, surface, pièces, photos, description, **rue**).
  curl_cffi (`impersonate="chrome"`) passe le contrôle Cloudflare ; 1,2 s entre requêtes ; les
  résultats « élargis » hors zone (codes postaux/villes inconnus) sont écartés ; la fiche n'est
  re-téléchargée que pour les nouvelles annonces ou après 3 jours. Diagnostic : `python scripts/pap_dryrun.py`.

## .env
`DATABASE_URL`, `TEST_DATABASE_URL`, `DISCORD_WEBHOOK_URL`, `ENRICH_TOKEN`, `PUBLIC_BASE_URL`,
`IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `TZ` (modèle : `.env.example`).

## Commandes
```bash
cd /volume1/docker/maison
docker compose build && docker compose up -d
docker compose logs -f collector
docker compose run --rm -v "$PWD":/app web pytest -q          # tests
docker compose run --rm collector python -m app.collector --once   # un cycle immédiat
```

## Score (/100)
Ville (25 → 11 selon rang, 5 si « autre ») · prix (≤400k 15, ≤450k 12, ≤500k 5) · type (indiv. 12,
semi 7) · terrain (≥400 m² 12, ≥250 8, ≥150 4 ; −3 jardin en longueur) · DPE (A/B 8 … F/G −5) ·
travaux (aucun 8, léger 3, lourd −8) · baignoire 3 · chambres (≥3 : 3, grande chambre ≥14 m² : 4) ·
trajets voiture cumulés vers les destinations de `commute` (≤30 min 8, ≤40 5, ≤50 2) · style cubique −4.
Exclusions : ville exclue / hors zone, prix > max, garage ou jardin **confirmés** absents.
Alerte Discord : score ≥ 75, prix ≤ 450 k€, garage ET jardin confirmés, une seule fois par bien.
