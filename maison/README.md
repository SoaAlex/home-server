# Maison — recherche automatisée

Stack privée (LAN + VPN uniquement, pas de proxy host NPM) : http://192.168.1.55:8090

| Service | Rôle |
|---------|------|
| `maison-web` | FastAPI : UI (liste filtrable, carte, fiche) + API (`/api/listings`, `/api/enrichment/*`) |
| `maison-collector` | Boucle ~horaire : Bien'ici JSON, notaires JSON, Maes (WordPress), agences Netty (`netty_agencies` dans criteria.yaml), petites agences à parseur dédié (`app/sources/agences.py` : Référence Immobilière, Immobilière de Croix, Dreossi, Immobilier & Compagnie), **PAP.fr** (`app/sources/pap.py`, curl_cffi + JSON-LD des fiches), alertes e-mail IMAP → photos/géo/trajets/DPE→adresse → fusion des doublons → score → alerte Discord |

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

## Doublons (`app/dedupe.py`)
Un même bien arrive souvent par plusieurs canaux (Bien'ici, site d'agence, notaires, cartes e-mail SeLoger/LeBonCoin
qui changent d'identifiant à chaque envoi). Les doublons sont **fusionnés** en une seule annonce ; rien n'est supprimé.
- **Empreintes photo** : pHash + dHash (64 bits) de chaque photo locale, calculés au téléchargement (table
  `listing_photo`). Deux photos sont « identiques » malgré recompression, redimensionnement, filigrane ou léger
  recadrage. Les photos génériques (image unie, bandeau d'agence, rendu partagé par des biens incompatibles) ne
  comptent pas.
- **Décision** par paire : *fusion automatique* (≥ 2 photos communes ; 1 photo + surface/prix compatibles ; texte
  identique à 80 % ; caractéristiques jumelles : même commune, prix à 1 %, surface à 1 m², pièces/chambres/terrain
  compatibles), *doublon possible* à valider (1 photo douteuse, programme neuf), ou rien. Vétos : surface à plus de
  12 %, 2 chambres d'écart, plus de 10 km, adresses précises à plus de 500 m (sauf ≥ 2 photos), refus de
  l'utilisateur. Les jumeaux sans photo commune se trompent environ une fois sur dix : « Séparer » les corrige.
- **Fusion** : les secondaires pointent vers l'annonce principale (`merged_into`) et sortent de la liste, du score,
  des alertes et de la file de l'agent. La principale porte la vue du bien (`cluster`) : offres (lien, source, prix,
  statut — une carte e-mail non revue depuis 45 jours est « non confirmée »), **prix retenu = le plus bas des offres
  actives**, champs vides complétés par les autres sources, photos sans doublon visuel, historique de prix fusionné.
  👍/👎, « à visiter », notes et dates sont reportés sur la principale.
- **À chaque cycle** du collecteur, après le téléchargement des photos et avant score/alertes : seules les annonces
  dont la signature (`dedupe_sig` : photos, prix, surface, commune, position, texte…) a changé sont comparées.
- **UI** : menu « Annonces ↗ » des liens et prix, section « Sources fusionnées » (bouton *Séparer*), bloc
  « Doublon possible » (*Fusionner* / *Pas un doublon*), bouton « 🔗 Fusionner avec… » (doublon non détecté,
  numéro de l'autre annonce), puce « Doublons à valider ». Un refus est définitif ; seule une fusion manuelle le lève.

Analyse initiale (une fois, ou pour un nouveau rapport) — à blanc par défaut, rapport HTML autonome :
```bash
docker compose run --rm --no-deps collector python scripts/dedupe_backfill.py           # à blanc -> data/reports/
docker compose run --rm --no-deps collector python scripts/dedupe_backfill.py --apply   # applique (sans alerte Discord)
```
`--no-deps` : sans lui, `docker compose run collector` recrée `maison-web` dès que l'image a été reconstruite
(déploiement involontaire du web). `pytest` vide la base `maison_test` : ne pas y faire de répétition sur une copie
de la production pendant que des tests tournent.
Tant que `--apply` n'a jamais été lancé, l'étape du collecteur est ignorée (journal « doublons: étape ignorée »).
Retour arrière complet : `UPDATE listing SET merged_into = NULL, cluster = NULL` (les données de chaque source sont
restées dans leur ligne) puis redéploiement de l'image précédente.

## Mitoyenneté (`app/adjacency.py`, `scoring.resolve_type`)
Cinq catégories : individuelle (`detached`), accolée par le garage (`link_detached` : aucun mur habitable commun,
liaison par un garage ou une annexe), semi (`semi`, un voisin habité), mitoyenne (`terraced`, deux voisins habités ou
plus), inconnue.
- **Sources et ordre de priorité** (un seul type retenu, côté serveur, pour le score, les filtres, l'alerte, la fiche
  et l'agent) : 1. correction de l'utilisateur (fiche, menu « Corriger ») ; 2. agent avec preuves par côté
  (`type_evidence`) ; 3. carte à adresse exacte, validée ou de la source ; 4. ancien verdict de l'agent ; 5. carte à
  adresse probable ; 6. annonce : champ structuré (Netty), texte explicite, texte faible. La fiche affiche la source,
  la confiance, un badge « sources en désaccord » et le détail (« Pourquoi ? ») ; `type_info` dans l'API.
- **Texte** (`textparse.detect_type`) : « mitoyenne/jumelée par le garage » → par garage ; « semi-individuelle »,
  « mitoyenne d'un côté », « jumelée » → semi ; « mitoyenne des deux côtés », « en bande » → mitoyenne ;
  « individuelle » (sans « mitoyenne »), « 4 façades », « non mitoyenne » → individuelle ; « mitoyenne », « maison de
  ville », « bel-étage » seuls → mitoyenne (indice faible). « Indépendante » et « villa » ne comptent plus.
- **Calcul cartographique** : pour chaque bien à adresse précise, le collecteur interroge la Géoplateforme IGN
  (BAN → BAN PLUS → BD TOPO bâtiments + Parcellaire Express, boîte de ±60 m), mesure les murs communs (≥ 2 m) avec les
  bâtiments des parcelles voisines et distingue habitation et annexe (usage, construction légère, hauteur, surface).
  Résultat dans `listing.adjacency` (catégorie, côtés vus de la rue, avertissements, géométrie). Au plus 40 calculs
  par cycle, recalcul si l'adresse change, nouvel essai d'un échec après 6 h ; journal « mitoyenneté : … ».
- **Fiche** : mini-carte « Plan » / « Photo aérienne » (IGN) + surcouche « Cadastre » (choix mémorisé), maison,
  propriété, voisins et murs communs tracés (±3 m) ; vue de rue Panoramax orientée vers la maison (cache
  `data/streetview/`, 180 jours) ; liens Google satellite / Street View (nouvel onglet, aucun contenu Google chargé).
- **Vérification par l'agent** : file `GET /api/enrichment/type_checks` (`enrichment.type_request`, distincte de
  l'enrichissement), job Hermes `maison-type-check` (`40 8-22 * * *`, 8 demandes par passage), bouton « Faire
  vérifier » dans la fiche, demande « conflict » créée une fois quand la carte contredit l'agent sur un bien avec
  garage. Outils : `maison pending --types N`, `maison planche ID`, `maison mitoyennete ID`,
  `maison mitoyennete-adresse "ADRESSE"`, `maison rue ID`. L'API refuse (422) un `property_type` hors liste ou
  incohérent avec les côtés.
- **Rattrapage** (une fois, après la mise en service) — à blanc par défaut, rapport HTML autonome :
```bash
docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py                    # à blanc -> data/reports/
docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py --apply            # applique (sans alerte)
docker compose run --rm --no-deps collector python scripts/adjacency_backfill.py --request-checks   # demandes « garage »
```
  `--apply` est relançable (seuls les échecs sont recalculés) ; `--request-checks` est idempotent.
- **Retour arrière** : image précédente, `UPDATE listing SET adjacency = NULL`,
  `UPDATE listing SET enrichment = enrichment - 'type_request'`, job `maison-type-check` en pause, skill restaurée
  depuis sa sauvegarde. Les verdicts de l'agent restent dans `enrichment`.

## Commentaires
Chaque fiche a un fil de commentaires datés et signés (bloc « 💬 Commentaires ») : ajout (`Ctrl+Entrée`),
modification (mention « modifié »), suppression après confirmation. Texte brut de 1 à 2 000 caractères.
- **Auteur** : choisi parmi `comment_authors` de `config/criteria.yaml` (ex. `[Alexandre, Marie]`), mémorisé dans
  chaque navigateur (`localStorage["maison.author"]`). Sans liste, les commentaires n'ont pas d'auteur. Pas de
  connexion : n'importe quel navigateur du LAN/VPN peut modifier ou supprimer un commentaire.
- **Liste** : badge « 💬 N », puce « 💬 Commentées », la recherche porte aussi sur le texte des commentaires.
- **Biens fusionnés** : un commentaire est rangé sur l'annonce principale ; la fiche réunit ceux de toutes les sources
  du bien. Une source séparée retrouve les commentaires écrits sur elle avant la fusion. Une annonce commentée est
  prioritaire pour devenir principale. Aucun effet sur le score, la réaction ni la file de l'agent.
- **API** : `GET`/`POST /api/listings/{id}/comments` (`{author, body}`), `PATCH /api/comments/{cid}` (`{body}`),
  `DELETE /api/comments/{cid}`. La fiche (`GET /api/listings/{id}`) renvoie `comments`, la liste `comments_n` et
  `comments_text`. Table `listing_comment` (la colonne `user_note` n'est pas utilisée).
- **Agent Hermes** : lecture seule. `user_comments` figure dans `/api/enrichment/pending`,
  `/api/enrichment/type_checks` et `maison pending` ; la skill les traite comme des indices, jamais comme une preuve.
  Toute écriture de commentaire portant l'en-tête `X-Token` est refusée (403).

## Santé quotidienne (`ops/healthcheck.py`)
Contrôle en lecture seule lancé chaque matin (7 h 30) par un cron Hermes sans agent, message sur Discord :
une ligne ✅ si tout va bien, sinon la liste des problèmes (🚨) et des points d'attention (⚠️).
Vérifie conteneurs + DNS épinglé, API, relais BAN/ADEME/cadastre (via la commande `maison` des agents),
journaux du collecteur (sources/e-mails en échec, cycle en retard), fraîcheur de chaque source en base,
5xx du relais et POST d'enrichissement refusés, crons Hermes d'enrichissement (pause, tirs en échec,
réponses vides), et les analyses bloquées : fiche encore en attente depuis le contrôle précédent, demande
« Investiguer » de plus de 12 h, enrichissement à l'arrêt, fiches `failed` ou sans résumé.
```bash
python3 -I ops/healthcheck.py --no-state          # essai à blanc (n'avance pas l'état)
python3 -I ops/healthcheck.py --no-state --json   # faits collectés + verdict
python3 -m unittest discover -s ops               # tests (stdlib, sans Docker)
```
État entre deux contrôles et historique : `~/.local/state/maison-health/`.

## Score (/100)
Ville (25 → 11 selon rang, 5 si « autre ») · prix (≤400k 15, ≤450k 12, ≤500k 5) · type (individuelle 12,
accolée par le garage 10, semi 7, mitoyenne ou inconnue 2 ; type retenu, voir « Mitoyenneté ») · terrain (≥400 m² 12, ≥250 8, ≥150 4 ; −3 jardin en longueur) · DPE (A/B 8 … F/G −5) ·
travaux (aucun 8, léger 3, lourd −8) · baignoire 3 · chambres (≥3 : 3, grande chambre ≥14 m² : 4) ·
trajets voiture cumulés vers les destinations de `commute` (≤30 min 8, ≤40 5, ≤50 2) · style cubique −4.
Exclusions : ville exclue / hors zone, prix > max, garage ou jardin **confirmés** absents.
Alerte Discord : score ≥ 75, prix ≤ 450 k€, garage ET jardin confirmés, une seule fois par bien.
