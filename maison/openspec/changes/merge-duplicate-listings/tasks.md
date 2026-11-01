# Tasks

Tests : `docker compose run --rm -v "$PWD":/app web pytest -q`, ou `mrun pytest -q` depuis Hermes (base `maison_test`). Ces tests ne font aucun appel réseau : les clients HTTP sont simulés, comme dans `tests/test_web.py`.

## 1. Dépendances et schéma

- [x] 1.1 Ajouter `pillow` et `numpy>=2` à `requirements.txt`, et `openspec/` à `.dockerignore`. Vérifier que `docker compose build` réussit, puis que `docker compose run --rm web python -c "import PIL, numpy; numpy.bitwise_count"` sort sans erreur.
- [x] 1.2 Étendre `db/schema.sql` avec des ajouts idempotents :
  - colonnes `merged_into` (FK `ON DELETE SET NULL`), `merged_at`, `merge_method`, `cluster JSONB` et `dedupe_sig` sur `listing`, plus un index sur `merged_into` ;
  - tables `listing_photo` et `duplicate_pair` (design D2, D3 et D8).

  Vérifier par un test que `init_schema` passe deux fois de suite sur `maison_test` et que la suite existante reste verte.
- [x] 1.3 Conserver `newProperty` dans le `raw` de `app/sources/bienici.py`. Vérifier par une assertion dans `tests/test_bienici.py` (la fixture contient `newProperty: false`).

## 2. Empreintes photo

- [x] 2.1 Créer `app/dedupe.py` avec le calcul, à partir d'octets JPEG ou PNG, du pHash (DCT numpy), du dHash et de l'écart type, en décodant par `draft` (design D3), plus la conversion uint64 ↔ BIGINT. Vérifier par des tests sur des images Pillow synthétiques :
  - une même image redimensionnée, recompressée, marquée d'un filigrane ou recadrée de 3 % correspond (D4) ;
  - deux images différentes ne correspondent pas ;
  - une image unie a un `std` inférieur à 12.
- [x] 2.2 Faire renvoyer les empreintes calculées en mémoire par `photos.download`, et les écrire dans `listing_photo` depuis `postprocess`. Télécharger aussi les photos des annonces « à compléter ». Vérifier par un test du collecteur avec un client simulé :
  - la vignette d'une carte SeLoger sans commune est téléchargée et hachée ;
  - le client ne reçoit aucune requête autre que les `photo_urls`.
- [x] 2.3 Implémenter `ensure_hashes(conn, ids)` pour les fichiers locaux non hachés ou modifiés (taille et date). Vérifier par un test avec une fonction de décodage espionnée :
  - un second appel ne décode rien ;
  - la réécriture d'un fichier déclenche un nouveau calcul.

## 3. Règles de décision

- [x] 3.1 Implémenter la compatibilité et les vétos (surface, chambres, distance, prix, terrain, valeurs inconnues) selon le tableau D7. Vérifier par les scénarios de la spec : 100 et 101 m² sont compatibles, 100 et 130 m² font l'objet d'un veto, des communes voisines à moins de 10 km ne bloquent pas.
- [x] 3.2 Implémenter la containment des 5-grammes sur les descriptions et la détection d'un programme neuf (`raw.newProperty` et expression régulière, D7). Vérifier par des tests unitaires sur des textes réels anonymisés (fixtures) : copie d'agence, carte courte, programme Bouygues.
- [x] 3.3 Implémenter l'appariement des photos en mémoire (`np.bitwise_count`) et le marquage `generic` : uniformité, ou photo commune à des annonces incompatibles (D5 et D6). Vérifier par deux tests :
  - un bandeau partagé par des viagers de surfaces incompatibles est marqué générique ;
  - une photo de couverture commune à 1 annonce Bien'ici et 4 cartes SeLoger compatibles ne l'est pas.
- [x] 3.4 Implémenter `decide(a, b)`, qui renvoie `auto`, `suggest` ou `none` avec les preuves en JSON. Vérifier par un test par scénario de la spec : 2 photos communes, carte à une vignette, même agence à 205 et 190 k€, programme neuf proposé, jumeaux sans photo fusionnés (décision 1b du 2026-11-01), paire refusée sans effet.

## 4. Fusion, groupes et vue matérialisée

- [x] 4.1 Implémenter `merge`, `split` et `reject` sous `pg_advisory_xact_lock`, avec des groupes plats, le choix de la principale (D9) et l'union-find contrôlé sur tout le groupe (D8). Vérifier par des tests en base :
  - pas de chaînage A ~ B ~ C ;
  - les secondaires sont repointées lors de la fusion de deux groupes ;
  - un refus bloque la fusion automatique de deux groupes ;
  - une fusion manuelle lève le refus.
- [x] 4.2 Reporter les données utilisateur sur la principale (D9). Vérifier par des tests :
  - 👍 l'emporte sur 👎 ;
  - « à visiter » est repris et les notes sont réunies ;
  - `seen_at` et `alerted_at` prennent la valeur la plus ancienne ;
  - la secondaire garde ses valeurs et les retrouve après séparation.
- [x] 4.3 Implémenter `refresh_cluster` et `refresh_clusters` (D2) : offres et statuts `active`, `gone` et `stale` (45 jours), prix retenu, `fill` par priorité de source, `active`, historique fusionné, photos sans doublon. Vérifier par les scénarios de la spec :
  - 235 et 241,5 k€ ;
  - offre la moins chère retirée ;
  - carte SeLoger périmée à 230 k€ face à 245 k€ ;
  - terrain de 367 m² complété ;
  - adresse validée prioritaire ;
  - vignette déjà présente affichée une seule fois ;
  - principale disparue avec une secondaire active.
- [x] 4.4 Faire lire `cluster` par `scoring.effective` (prix retenu en premier, `fill` en dernier recours). Vérifier par des tests dans `tests/test_scoring.py` : score calculé sur le prix minimal et sur le terrain complété.

## 5. Collecteur, alertes et file d'enrichissement

- [x] 5.1 Intégrer `dedupe.process(conn, touched)` et `refresh_clusters` dans `collector.run_once`, entre `postprocess` et `rescore`. `rescore` ne traite que les principales, avec le filtre `cluster.active`. Vérifier par des tests dans `tests/test_collector.py` :
  - une carte e-mail d'un bien Bien'ici connu est fusionnée dans le même cycle, sans alerte ;
  - une remise en vente qui correspond à une annonce disparue depuis moins de 90 jours est fusionnée.
- [x] 5.2 Faire ignorer les secondaires par `alerts.should_alert`. `build_embed` affiche le prix retenu, l'URL de l'offre active la moins chère et un champ « Sources ». Vérifier par des tests : une nouvelle source qui rejoint un groupe déjà alerté n'envoie rien, et le contenu de l'embed est conforme.
- [x] 5.3 Exclure les secondaires de `/api/enrichment/pending`. `POST /enrichment` sur une secondaire range l'enrichissement sur cette ligne, recalcule le groupe et rescore la principale. Vérifier par des tests dans `tests/test_web.py` et `tests/test_post.py` : file sans secondaire, enrichissement de la principale inchangé.
- [x] 5.4 Ajouter un test de coût : 12 000 empreintes synthétiques en base et une nouvelle annonce de 10 photos, comparées en moins de 5 s. Journaliser au collecteur la durée de l'étape de déduplication et les nombres de fusions et de suggestions, et vérifier la ligne dans `docker compose logs collector` après un `--once`.

## 6. API et UI

- [x] 6.1 `GET /api/listings` ne renvoie que les principales, avec `cluster` et `dup_pending`. `GET /api/listings/{id}` ajoute `members` et `suggestions`, et renvoie `merged_into` pour une secondaire. Vérifier par des tests de la liste, de la fiche principale et de la fiche secondaire.
- [x] 6.2 Ajouter les routes `POST /api/listings/{id}/merge`, `/split` et `/not_duplicate` (D12). Vérifier par des tests :
  - une annonce séparée réapparaît avec son propre prix, et le refus est enregistré ;
  - « Pas un doublon » masque la suggestion ;
  - une fusion manuelle lève un refus.
- [x] 6.3 Liste et carte de l'UI :
  - retirer le regroupement par `fingerprint` et `#f-dups` ;
  - ajouter la puce « Doublons à valider » et les badges « N sources » et « Doublon ? » ;
  - aligner l'ordre de `eff()` sur D2 ;
  - incrémenter `?v=` de `app.js` et `style.css`.

  Vérifier dans le navigateur, sur les données de test (capture bureau et mobile) : nombre d'annonces, badges, filtre.
- [x] 6.4 Fiche de l'UI :
  - menu déroulant « Annonces ↗ » des offres (statuts « retirée » et « non confirmée ») ;
  - autres prix et historique fusionné ;
  - carrousel tiré de `cluster.photos` ;
  - section « Sources fusionnées » avec « Séparer » ;
  - bloc « Doublon possible » en côte à côte avec « Fusionner » et « Pas un doublon » ;
  - redirection de `#/l/<secondaire>`.

  Vérifier dans le navigateur, par une capture de chaque état et un clic sur chaque bouton dont le résultat est relu par l'API.
- [x] 6.5 Ajouter au `README.md` une section « Doublons » : fonctionnement, analyse initiale, rapport, Séparer et retour arrière. Vérifier que les commandes documentées s'exécutent telles qu'écrites.

## 7. Analyse initiale

- [x] 7.1 Créer `scripts/dedupe_backfill.py`, à blanc par défaut (D13) : hachage en parallèle, vignettes des cartes « à compléter », décisions, groupes, rapport HTML autonome dans `/data/reports/`. Vérifier par un test sur `maison_test` avec des images de fixture : aucune ligne `listing` modifiée, rapport produit avec les groupes attendus.
- [x] 7.2 Ajouter `--apply` : fusions et suggestions sous verrou, `refresh_clusters`, puis `rescore` avec `DISABLE_ALERTS=1`, et un résumé des biens qui deviennent alertables. Vérifier par un test : une seconde application ne crée ni fusion ni suggestion.
- [x] 7.3 Après un `pg_dump -t listing`, lancer l'analyse à blanc sur la base réelle. Vérifier :
  - durée inférieure à 15 min ;
  - nombres de groupes « auto », de suggestions et de photos génériques ;
  - le rapport transmis à Alexandre.

  Recalibrer les seuils D4 et D7 sur ses retours (cas témoins du design : #402/#403 et #545/#575 fusionnés, #353/#356 et #560/#563 non fusionnés), et mettre à jour `design.md` si les valeurs changent.

## 8. Exploitation

- [x] 8.1 `ops/healthcheck.py` : ajouter `merged_into IS NULL` à `PENDING_SQL_FILTER`, ajouter le fait `dup_pending` et une ligne ⚠️ pour les suggestions de plus de 7 jours. Vérifier que `python3 -m unittest discover -s ops` passe et que `python3 -I ops/healthcheck.py --no-state --json` affiche le nouveau fait.

## 9. Mise en service et vérification d'ensemble

- [x] 9.1 Déployer selon le Migration Plan : `docker compose build`, `up -d`, puis `dedupe_backfill.py --apply` une fois le rapport validé. Vérifier en production :
  - la liste a perdu autant d'annonces que de secondaires créées ;
  - une fiche fusionnée montre le menu des liens et des prix ;
  - un « Séparer » suivi d'un « Fusionner » sur un cas réel fonctionne ;
  - `maison pending --standard 15` ne renvoie aucune secondaire ;
  - le journal du collecteur montre l'étape de déduplication au cycle suivant.
- [x] 9.2 Faire un commit local (auteur SoaAlex) du code, des tests, du README et des artefacts OpenSpec, sans push sans accord explicite. Vérifier `git log -1 --stat`, puis vérifier avec `git status` qu'aucun fichier `data/` ni rapport n'est suivi.

## Workflow follow-up

- Mettre à jour la skill Hermes `maison-enrichment`, hors de ce dépôt : remplacer les pièges « Doublon d'un bien déjà traité », « Deux cartes de même agence… » et « Doublon inter-agences » par la règle du design D14, et indiquer que `maison show` d'une secondaire renvoie `merged_into`.
- Archiver le changement (`openspec archive merge-duplicate-listings`) après validation en production, puis vérifier que `openspec/specs/listing-deduplication` et `openspec/specs/merged-listings` existent.
