# Proposal

## Why

Le type de maison (individuelle, semi, mitoyenne) pèse jusqu'à 10 points du score. Pourtant, le 2026-11-01, il est inconnu pour 46 % des annonces actives non exclues (433 sur 951), et il est souvent faux quand il est renseigné :

- **Texte** : « cuisine indépendante » est lu comme « maison indépendante », donc individuelle. Dans l'échantillon du spike, 43 des 81 « individuelles » qui ne contiennent pas le mot « individuelle » viennent de là. Le mot « individuelle » seul ne concorde avec le cadastre que 7 fois sur 10.
- **Agent Hermes** : il juge sur 4 à 8 photos choisies sans tri, souvent sans façade. Par exemple, #15 n'a aucune photo de façade : l'annonce a été classée individuelle alors que la maison est dans une rangée. De plus, la règle « mitoyenne sans nombre de côtés = omettre » laisse beaucoup d'annonces sans type.
- **Garage** : aucune source ne repère la maison reliée au voisin par son seul garage. Alexandre veut pourtant la noter mieux qu'une semi.

Les données publiques de l'IGN permettent de combler ce manque. La BD TOPO, le cadastre et les liens BAN PLUS (adresse → bâtiment → parcelle) donnent l'emprise de chaque bâtiment, et donc ses murs communs. Spike du 2026-11-01 sur les 626 annonces à adresse exacte ou probable :

- le bâtiment est trouvé pour 623 annonces, dont 598 par un lien BAN PLUS direct ;
- le calcul prend environ 7 s par annonce ;
- il concorde à 77 % avec les verdicts actuels de l'agent. Les désaccords examinés montrent des erreurs des deux côtés : l'agent est trompé par le texte, et le cadastre ne voit pas un garage dessiné dans le même polygone que la maison.

Enfin, Panoramax (vues 360° de la MEL, septembre 2026) montre la façade et les maisons voisines quand les photos de l'annonce ne les montrent pas.

## What Changes

- **Mitoyenneté calculée sur la carte** : pour chaque annonce à adresse précise (exacte, probable ou validée par l'utilisateur), le système :
  - identifie le bâtiment, par la BAN puis BAN PLUS et la BD TOPO, avec un repli sur la parcelle cadastrale ;
  - mesure les murs communs avec les bâtiments des parcelles voisines ;
  - distingue habitation et annexe (usage, construction légère, hauteur, surface) ;
  - rend un verdict avec ses détails (côtés, longueurs, bâtiment, avertissements de qualité).

  Le résultat est stocké sur l'annonce. Le collecteur le calcule pour chaque adresse nouvelle ou modifiée, et un script de rattrapage traite les annonces existantes, d'abord à blanc puis avec `--apply`.
- **Nouvelle catégorie « Accolée par le garage »** (`link_detached`, 10 points, choix d'Alexandre) : maison sans mur habitable commun, reliée au voisin seulement par un garage ou une annexe. Nouveau barème du critère « type » : individuelle 12, accolée par le garage 10, semi 7, mitoyenne ou inconnue 2.
- **Arbitrage entre les sources** : le type retenu suit un ordre fixe :
  1. correction de l'utilisateur ;
  2. vérification de l'agent selon le nouveau protocole ;
  3. carte, avec une adresse exacte ;
  4. ancien verdict de l'agent ;
  5. carte, avec une adresse probable ;
  6. annonce (champ structuré ou texte).

  La source retenue est affichée, et les désaccords entre sources sont signalés. Ce type sert au score, aux filtres, aux alertes et à l'agent.
- **Détection dans le texte corrigée** :
  - « indépendante » seul ne vaut plus individuelle ;
  - « mitoyenne par le garage », « jumelée par les garages », « mitoyenne des deux côtés », « 4 façades » et « non mitoyenne » sont reconnus ;
  - les annonces que leurs sources ne renvoient plus sont recalculées une fois.
- **Vérification par l'agent, photos à l'appui** (skill `maison-enrichment` et nouveaux outils) :
  - une planche des photos numérotées pour repérer les vues extérieures en un seul appel ;
  - une question par côté de la maison : habitation, garage, annexe ou libre ;
  - une vue aérienne IGN annotée (bâtiment, voisins, murs communs) ;
  - une vue de rue Panoramax recadrée vers la maison ;
  - une sortie `property_type` + `type_evidence` contrôlée par l'API.

  Cette vérification s'applique :
  - aux nouvelles annonces ;
  - à toutes les annonces actives avec un garage confirmé (environ 410), par une file dédiée et un nouveau job Hermes ;
  - à ces mêmes annonces quand un nouveau désaccord apparaît plus tard.
- **Fiche de l'annonce** :
  - mini-carte avec photo aérienne IGN, cadastre et bâtiments (cible, voisins, murs communs) ;
  - vue de rue Panoramax et liens manuels Google Maps / Street View ;
  - type affiché avec sa source, ses preuves et un badge en cas de désaccord ;
  - correction manuelle, protégée contre l'agent, et bouton « Faire vérifier ».

  S'y ajoutent un filtre « Par garage » et le type dans l'alerte Discord.
- **Google** : aucune image Google n'est téléchargée ni analysée. Les conditions de Google Maps Platform (3.2.3) interdisent l'extraction, la mise en cache et la création de contenu dérivé, et l'API demande une clé payante. Seuls des liens d'ouverture manuelle sont proposés.
- **BREAKING (API de l'agent)** :
  - `property_type` accepte la valeur `link_detached` ;
  - une valeur hors liste pour `property_type` ou `type_evidence` est refusée (HTTP 422) au lieu d'être enregistrée ;
  - les clients de `/api/listings` doivent lire le type arbitré (`type_info`).

## Capabilities

### New Capabilities

- `building-adjacency` : calcul de la mitoyenneté à partir des données IGN. Couvre l'identification du bâtiment, les murs communs, la nature des volumes, le verdict et les avertissements, le stockage, le recalcul, le rattrapage et les outils de l'agent (point quelconque, vue aérienne annotée).
- `property-type-resolution` : choix du type retenu. Couvre les catégories et le barème (dont « accolée par le garage »), l'ordre de priorité des sources, les désaccords, la détection dans le texte, la correction manuelle et l'usage dans le score, les filtres et les alertes.
- `photo-adjacency-review` : vérification du type par l'agent Hermes. Couvre la planche de photos, le protocole par côté, la vue de rue, la vue aérienne, le format de sortie validé, la file de vérification et le périmètre de re-vérification.
- `listing-map-views` : vues cartographiques de la fiche. Couvre les couches aérienne et cadastrale, les bâtiments et murs communs sur la carte, la vue de rue, les liens manuels Google Maps / Street View et les mentions de source.

### Modified Capabilities

(aucune : aucune spec n'est encore archivée. Les specs du change en cours `merge-duplicate-listings` ne changent pas, car l'arbitrage lit simplement les verdicts de toutes les sources d'un bien.)

## Impact

- **Code** :
  - nouveaux modules `app/adjacency.py` (mitoyenneté) et `app/streetview.py` (Panoramax et projection) ;
  - modules modifiés : `app/textparse.py`, `app/scoring.py`, `app/collector.py`, `app/web.py`, `app/alerts.py`, `app/ingest.py`, `app/dedupe.py` (« unknown » compte comme une valeur vide pour le complément de groupe) et `app/sources/*.py` (type et provenance : champ structuré Netty ou texte) ;
  - interface : `app/static/app.js`, `style.css`, `templates/index.html` ;
  - scripts et exploitation : `scripts/maison_cli.py` (commandes `mitoyennete`, `planche`, `rue` et `pending --types`), nouveau `scripts/adjacency_backfill.py`, `ops/healthcheck.py`.
- **Base** : colonnes `adjacency JSONB` et `property_type_src TEXT`, plus un index partiel sur les demandes de vérification, tous ajoutés de façon idempotente.
- **Dépendance** : `shapely` 2.x (numpy et Pillow sont déjà présents).
- **Services externes, sans clé** :
  - Géoplateforme IGN : WFS BD TOPO, Parcellaire Express et BAN PLUS ; WMS/WMTS orthophoto et cadastre ;
  - BAN ;
  - Panoramax.

  Volume : environ 4 requêtes par adresse nouvelle ; le rattrapage d'environ 600 annonces prend une vingtaine de minutes.
- **Hermes** : skill `maison-enrichment` (protocole de mitoyenneté, schéma, pièges), nouveau cron `maison-type-check`, contrôle santé quotidien.
- **Séquencement** : à appliquer après la clôture de `merge-duplicate-listings`, qui modifie les mêmes fichiers.
- **Hors périmètre** :
  - API Google ;
  - estimation de la mitoyenneté sans adresse précise (flou Bien'ici, rue seule) : l'indice de voisinage est reporté ;
  - modèle de vision local ;
  - autres critères du score ;
  - usage des autres attributs de la BD TOPO (année, nombre de logements, matériaux) au-delà de l'affichage et des avertissements.
