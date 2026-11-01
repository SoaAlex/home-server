# Proposal

## Why

Un même bien arrive aujourd'hui par plusieurs canaux : Bien'ici, le site de l'agence (Netty, Maes, agences), les notaires, et plusieurs cartes d'alertes e-mail SeLoger/LeBonCoin qui changent d'identifiant à chaque envoi et ne disparaissent jamais. La seule protection actuelle est l'empreinte `fingerprint` (ville | prix/10 k€ | surface/5 m² | terrain/50 m²), qui ne sert qu'à masquer côté navigateur. Elle a deux défauts :

- elle rate les vrais doublons dont la ville ou le prix diffère, par exemple la même agence qui publie une maison sous deux communes ou à deux prix ;
- elle regroupe des biens différents qui ont les mêmes chiffres arrondis.

Sur 1 730 annonces actives, elle forme 188 groupes, soit 446 annonces. Un échantillon aléatoire de 300 annonces contient pourtant une vingtaine de paires qui partagent des photos identiques, et beaucoup ne sont pas dans le même groupe d'empreinte. Ces doublons ont plusieurs effets : ils encombrent la liste, coûtent un enrichissement Hermes par copie, font revenir sous un autre identifiant un bien déjà écarté (👎) et dispersent le prix et les liens entre plusieurs fiches.

## What Changes

- **Empreintes photo** : chaque photo téléchargée reçoit un pHash et un dHash de 64 bits, calculés au téléchargement depuis les octets déjà en mémoire, puis stockés en base. Les photos génériques (bandeau d'agence, rendu de programme partagé, image quasi uniforme) sont repérées et ne servent pas de preuve.
- **Détection des doublons** : chaque paire candidate reçoit une décision à trois niveaux, calculée à partir des photos communes, de la compatibilité des caractéristiques (surface, chambres, distance, prix) et de la similarité du texte :
  - **fusion automatique** pour les cas sûrs ;
  - **doublon possible**, à valider dans l'UI ;
  - **rien**.

  Des vétos empêchent toute fusion automatique : surface incompatible, communes trop éloignées, refus de l'utilisateur, programme neuf.
- **Fusion non destructive** : les lignes `listing` restent en base, car le collecteur les réinsérerait. Une annonce secondaire pointe vers une annonce principale via `merged_into` et disparaît de la liste, de la file d'enrichissement, du score et des alertes. Une table de paires conserve les preuves, les suggestions et les refus.
- **Une seule fiche par bien** :
  - les offres de toutes les sources (lien, source, agence, prix, statut) apparaissent dans un menu déroulant « Annonces ↗ » ;
  - le prix retenu pour le score, le tri, les filtres et les alertes est **le plus bas parmi les offres encore actives**, et tous les prix restent visibles ;
  - l'historique des prix est fusionné et les photos sont réunies sans doublon visuel ;
  - les champs vides de l'annonce principale sont complétés par les autres sources ;
  - les données utilisateur (👍/👎, à visiter, note, vue, alertée) sont reportées sur l'annonce principale.
- **Actions dans l'UI** :
  - « Fusionner » et « Pas un doublon » sur les doublons possibles ;
  - « Séparer » sur chaque source fusionnée ;
  - un filtre « Doublons à valider ».
- **Grosse analyse initiale** : un script hache toutes les photos locales, compare toutes les annonces actives et récentes et produit un rapport HTML autonome avec les vignettes côte à côte (mode à blanc par défaut), puis applique les fusions avec `--apply`.
- **Traitement continu** : à chaque cycle du collecteur, les annonces nouvelles ou modifiées sont comparées après le téléchargement des photos et avant le score et les alertes. Une carte e-mail déjà connue est donc fusionnée avant d'entrer dans la file de l'agent. Les cartes « à compléter » reçoivent désormais leur vignette pour pouvoir être comparées.
- **BREAKING (UI/API interne)** :
  - `/api/listings` ne renvoie plus que les annonces principales, enrichies des offres ;
  - le regroupement client par `fingerprint` et la case « Afficher les doublons » sont remplacés par la fusion côté serveur ;
  - une fiche secondaire ouverte par un ancien lien (`#/l/<id>`, Discord) renvoie vers l'annonce principale.

## Capabilities

### New Capabilities

- `listing-deduplication` : détecter les annonces qui décrivent le même bien. Couvre les empreintes photo, la recherche de candidats, les règles de décision (automatique / possible / rien), les vétos, la mémoire des refus, l'analyse initiale avec rapport, le traitement incrémental au collecteur et les contraintes de performance.
- `merged-listings` : présenter et exploiter un bien multi-sources comme une seule annonce. Couvre le choix de l'annonce principale, les offres (liens et prix), le prix retenu, la fusion des champs, des photos et des données utilisateur, l'exclusion des secondaires du score, des alertes et de l'enrichissement, ainsi que les actions Fusionner, Séparer et Pas un doublon.

### Modified Capabilities

(aucune : le projet n'a pas encore de spec OpenSpec)

## Impact

- **Code** :
  - `app/photos.py` : hachage au téléchargement ;
  - nouveau `app/dedupe.py` : empreintes, candidats, règles, fusion et séparation ;
  - `app/collector.py` : étape de déduplication avant `rescore`, vignettes des cartes « à compléter » ;
  - `app/scoring.py` (`effective` : prix minimal, champs complétés), `app/alerts.py` ;
  - `app/web.py` : liste, fiche, nouvelles routes merge/split/not_duplicate, file `pending` ;
  - `app/static/app.js`, `style.css`, `templates/index.html`.
- **Base** (`db/schema.sql`, ajouts idempotents seulement) :
  - colonnes `merged_into`, `merged_at` et `cluster` (JSONB matérialisé) sur `listing` ;
  - tables `listing_photo` (empreintes) et `duplicate_pair` (preuves, suggestions, refus).
- **Dépendances** : `pillow` et `numpy` dans `requirements.txt`, sans `imagehash` ni `scipy` (DCT faite avec numpy).
- **Scripts et exploitation** :
  - nouveau `scripts/dedupe_backfill.py` ;
  - `ops/healthcheck.py` : exclure les secondaires des contrôles « en attente », compter les doublons à valider ;
  - mises à jour du README et de la skill Hermes `maison-enrichment` (règles « doublon »).
- **Hors périmètre** :
  - suppression physique des lignes ;
  - comparaison par IA de vision ;
  - téléchargement de nouvelles photos LeBonCoin ou SeLoger au-delà des vignettes déjà fournies par les e-mails, et aucun appel à leurs pages ;
  - correction du bug existant de `photos.download` qui conserve d'anciens fichiers `NN.jpg` après un remplacement de `photo_urls` (seulement contourné, voir design).
