# Tasks

**Prérequis** : `merge-duplicate-listings` est terminé et commité. Les deux changes touchent `scoring.py`, `web.py`, `collector.py`, `app.js`, `schema.sql` et `healthcheck.py`.

**Tests** : `docker compose run --rm -v "$PWD":/app web pytest -q`, ou `mrun pytest -q` depuis Hermes (base `maison_test`). Ils ne font aucun appel réseau : WFS, WMS, BAN et Panoramax sont simulés, comme les clients de `tests/test_web.py`. Les fixtures respectent D16 : elles vont dans `tests/fixtures/`, déjà ignoré par git, et le code de test versionné ne contient ni adresse ni coordonnée réelle.

## 1. Dépendances et schéma

- [x] 1.1 Ajouter `shapely==2.*` à `requirements.txt` et reconstruire l'image. Vérifier que `docker compose build` réussit, puis que `docker compose run --rm web python -c "import shapely; print(shapely.__version__)"` affiche une version 2.x.
- [x] 1.2 Étendre `db/schema.sql` avec des ajouts idempotents :
  - colonnes `adjacency JSONB` et `property_type_src TEXT` ;
  - index partiel sur `(enrichment ? 'type_request')`.

  Vérifier par un test que `init_schema` passe deux fois de suite sur `maison_test` et que la suite existante reste verte.

## 2. Détection dans le texte et provenance du type

- [x] 2.1 Réécrire `textparse.detect_type` pour qu'il renvoie `(type, force, expression)` selon l'ordre de D7, sans la règle `independante`. Vérifier par des tests de `tests/test_textparse.py` qui reprennent les scénarios de la spec :
  - « cuisine indépendante » → inconnu ;
  - « mitoyenne par le garage » et « jumelée par les garages » → `link_detached` ;
  - « individuelle mitoyenne d'un seul côté » → `semi` ;
  - « 4 façades » et « non mitoyenne » → `detached` explicite ;
  - « maison de ville » → `terraced` faible ;
  - « villa » → inconnu (aucune « villa » isolée dans le spike, voir D7).

  Les anciens tests doivent rester verts après adaptation.
- [x] 2.2 Faire écrire `property_type` et `property_type_src` par toutes les sources (`structured` pour le `type_house` de Netty, sinon la force du texte), et ajouter `property_type_src` aux `FIELDS` de `app/ingest.py`. Vérifier par les tests des sources :
  - `tests/test_netty.py` : VM1170 donne `detached` et VM1079 `terraced`, tous deux en `structured` ;
  - `test_agences`, `test_pap` et `test_email_alerts` : assertions mises à jour ;
  - un `upsert` conserve la provenance.

## 3. Calcul cartographique de la mitoyenneté

- [x] 3.1 Créer les fixtures de `tests/fixtures/adjacency/` (D16) à partir de réponses WFS réelles réduites à la boîte utile, en reprenant les cas du spike (`maison/.scratch/adjacency-spike/`) : rangée, semi, maison isolée avec son garage, liaison par le garage du voisin, abri contre abri, bâtiment de plusieurs logements, emprise de plus de 300 m², parcelle sans lien BAN PLUS, aucun bâtiment. Vérifier :
  - `git check-ignore tests/fixtures/adjacency/<fichier>` confirme qu'elles sont ignorées ;
  - les tests versionnés ne citent ni adresse ni coordonnée réelle.
- [x] 3.2 Implémenter `adjacency.analyse(ctx)`, fonction pure (D2, D3) :
  - bâtiment cible (lien, parcelle, proximité) et propriété ;
  - nature des volumes et contacts regroupés par parcelle ;
  - catégorie, côtés vus de la rue, avertissements ;
  - géométrie GeoJSON arrondie.

  Vérifier par un test par scénario de la spec `building-adjacency` : rangée à 9 m et 8 m, passage de 2 m, angle de 1 m, voisin à deux volumes, garage du voisin, garage propre contre la maison voisine, abri contre abri, quatre catégories, trois logements, emprise anormale, bâtiment introuvable, repli sur la parcelle.
- [x] 3.3 Implémenter `adjacency.fetch(client, point, address)` (D1) :
  - identifiant BAN (score, distance, avertissement `ban_mismatch`) ;
  - filtre CQL BAN PLUS ;
  - boîtes de ±60 m pour la BD TOPO et le Parcellaire Express ;
  - délais, deux nouvelles tentatives, User-Agent.

  Vérifier par des tests avec un client simulé :
  - seuls les domaines de l'IGN et de la BAN sont appelés ;
  - la `BBOX` est en ordre lat/lon ;
  - une erreur 504 est réessayée ;
  - un échec définitif produit `status: error`.
- [x] 3.4 Implémenter `adjacency.process(conn, client, limit=40)` (D4, D5) :
  - sélection des éligibles, position fusionnée comprise ;
  - signature d'entrée et nouvel essai d'une erreur après 6 h ;
  - commit par annonce et ordre de traitement.

  L'appeler dans `collector.run_once` après `dedupe.process` et avant `rescore`, avec une ligne de journal « mitoyenneté : n calculées, n échecs, n en attente ». Vérifier par des tests de `tests/test_collector.py` :
  - adresse exacte calculée ;
  - positions `city` et `street` en `not_applicable` ;
  - aucune requête sans changement ;
  - recalcul après une correction d'adresse ;
  - panne WFS : le cycle va jusqu'au score, et aucun nouvel essai n'a lieu avant 6 h ;
  - avec 600 annonces en attente, 40 sont calculées par cycle.
- [x] 3.5 Ajouter `GET /api/tools/adjacency`, avec jeton, par adresse ou par `lon`/`lat`, géométrie en option. Vérifier par des tests de `tests/test_web.py` : 401 sans jeton, aucune ligne `listing` modifiée, champs du résultat présents.
- [x] 3.6 Ajouter la vue aérienne annotée `GET /api/tools/aerial?listing_id=…|lon=&lat=` (D10) : requête WMS, tracés, légende, cache par `computed_at`, réponse « non applicable » pour une position imprécise. Vérifier par des tests avec un WMS simulé (JPEG généré) :
  - image de 700×700 px ;
  - couleur de la cible présente au centroïde attendu ;
  - second appel servi par le cache ;
  - réponse 404 pour une annonce `city`.

## 4. Arbitrage du type, score et alertes

- [x] 4.1 Implémenter `scoring.resolve_type(row)` et `type_info` (D6), retirer `property_type` de `OVERRIDABLE`, et faire utiliser le type arbitré par `effective()`. Appliquer le barème 12 / 10 / 7 / 2 / 2. Vérifier par des tests de `tests/test_scoring.py` qui reprennent les scénarios de la spec `property-type-resolution` :
  - carte exacte contre ancien verdict de l'agent ;
  - agent avec preuves contre carte ;
  - correction de l'utilisateur ;
  - carte probable seule (confiance moyenne) ;
  - texte faible sans désaccord ;
  - `link_detached` qui vaut +3 points par rapport à `semi`.
- [x] 4.2 Dans `dedupe.build_cluster`, traiter `unknown` comme vide pour `property_type` et ranger le meilleur verdict des secondaires avec sa provenance. Vérifier par un test : une principale SeLoger sans type et une secondaire Netty semi donnent `type_info` = `semi`, source « annonce ».
- [x] 4.3 Exposer `type_info` :
  - dans `/api/listings`, avec `adjacency` sans géométrie ;
  - dans `/api/listings/{id}`, avec la géométrie ;
  - dans `/api/enrichment/pending` ;
  - dans le JSON allégé de `maison pending`.

  Vérifier par des tests de `tests/test_web.py` (pas de géométrie dans la liste, géométrie présente dans la fiche), puis par `maison pending --standard 1` sur l'API locale.
- [x] 4.4 Ajouter `POST /api/listings/{id}/property_type` (cinq valeurs ou `null`, clé `enrichment.property_type_user`, recalcul immédiat du score), et retirer `property_type_user` des corps envoyés par l'agent. Vérifier par des tests :
  - le score renvoyé est mis à jour ;
  - un POST ultérieur de l'agent ne change pas le type retenu ;
  - `null` rend le type automatique ;
  - une valeur hors liste donne 400.
- [x] 4.5 Ajouter le champ « Type : <libellé> (<source>) » à l'embed Discord. Vérifier par un test du contenu de l'embed pour un bien `link_detached` venant de la carte.
- [ ] 4.6 Mettre à jour le `README.md` :
  - section « Score » : barème du type ;
  - nouvelle section « Mitoyenneté » : sources, ordre de priorité, calcul cartographique, rattrapage, file de vérification, retour arrière.

  Vérifier que les commandes documentées s'exécutent telles qu'écrites.

## 5. Vérification par l'agent : API et outils

- [x] 5.1 Implémenter la validation de D14 (énumération, schéma de `type_evidence`, cohérence entre côtés et type, erreur 422 avant toute écriture). Vérifier par des tests de `tests/test_post.py` :
  - « Maison mitoyenne » → 422 et enrichissement inchangé ;
  - sortie valide enregistrée ;
  - `terraced` avec un côté `free` → 422 qui cite `semi` ;
  - un côté `unknown` est accepté.
- [x] 5.2 Implémenter la file des vérifications (D13) :
  - `enrichment.type_request` ;
  - `GET /api/enrichment/type_checks`, avec jeton, trié par score puis par date de demande ;
  - `POST /api/listings/{id}/type_request` ;
  - POST d'enrichissement avec `_type_check` : seuls les champs du type sont fusionnés, la demande passe dans l'historique et le statut ne change pas ;
  - un enrichissement standard avec `type_evidence` solde la demande ;
  - demande `conflict` créée une seule fois par `adjacency.process`, seulement pour un garage confirmé.

  Vérifier par un test par scénario de la spec `photo-adjacency-review` (file, verdict posté, standard qui solde, désaccord unique, annonce sans garage ignorée).
- [x] 5.3 Ajouter `GET /api/listings/{id}/contact_sheet.jpg`, avec jeton (D11) : 12 photos au plus, photos du bien fusionné, numérotées, 960 px de large au plus. Vérifier par des tests sur des images de fixture : largeur, nombre de vignettes, 404 sans photo.
- [x] 5.4 Implémenter `app/streetview.py` et les routes `/api/listings/{id}/street` et `/street.jpg` (D9) :
  - recherche Panoramax et choix entre photo 360° et photo plane orientée ;
  - décodage à demi-résolution et projection ;
  - cache (clé de position, 180 jours) et verrou ;
  - réponse « aucune vue de rue ».

  Vérifier par des tests avec des réponses Panoramax simulées et une petite image équirectangulaire synthétique :
  - un repère coloré placé à l'azimut de la cible apparaît au centre de la vue ;
  - une photo plane hors champ est écartée ;
  - le second appel ne télécharge rien.
- [x] 5.5 Étendre `scripts/maison_cli.py` avec `pending --types N` (copie `/tmp/pending_types.json`), `mitoyennete ID`, `mitoyennete-adresse`, `planche ID` et `rue ID`. Les images vont dans `$TMPDIR`, sinon dans `/tmp`, et la commande affiche le chemin absolu ; le jeton reste dans l'en-tête. Mettre à jour les copies `~/.hermes/sandboxes/docker/default/workspace/bin/maison` et `~/.local/bin/maison`. Vérifier :
  - chaque commande fonctionne contre l'API locale et produit son fichier ;
  - `diff` entre le dépôt et les deux copies est vide ;
  - aucun jeton n'apparaît dans une URL construite (relecture du code).

## 6. Interface

- [ ] 6.1 Mini-carte de la fiche :
  - contrôle de couches « Plan » / « Photo aérienne » (WMTS IGN) et surcouche « Cadastre » ;
  - choix mémorisé dans `localStorage` et mentions de source ;
  - couche GeoJSON de l'adjacence avec sa légende ;
  - explication en l'absence de calcul, et mention « maison non localisée » pour une position imprécise.

  Vérifier dans le navigateur par des captures sur trois cas : mitoyenne à adresse exacte, accolée par le garage, carte e-mail `city`.
- [ ] 6.2 Bloc « Localisation » : vue de rue (image, date, auteur, lien Panoramax) et liens Google Maps satellite / Street View dans un nouvel onglet. Vérifier dans le navigateur :
  - les liens ouvrent les bonnes positions ;
  - le journal réseau (CDP) ne montre aucune requête vers un domaine Google à l'affichage de la fiche ;
  - la liste ne demande aucune vue de rue.
- [ ] 6.3 Ligne « Type » de la fiche :
  - libellé, pastille de source, badge de désaccord, « Pourquoi ? » ;
  - menu de correction, bouton « Faire vérifier » et badge « vérification demandée ».

  Filtre Tous / Individuelle / Par garage / Semi / Autre, `TYPES` avec `link_detached`, incrément du `?v=` de `app.js` et de `style.css`. Vérifier dans le navigateur :
  - une correction survit au rechargement de la page (relue par l'API) ;
  - le nombre d'annonces du filtre « Par garage » est égal au nombre renvoyé par l'API ;
  - captures bureau et mobile.

## 7. Rattrapage

- [ ] 7.1 Créer `scripts/adjacency_backfill.py`, à blanc par défaut (D12) : 4 requêtes en parallèle au plus, verdicts « annonce » recalculés sans écriture, rapport HTML autonome dans `/data/reports/`. Vérifier par un test sur `maison_test` avec une récupération simulée :
  - aucune ligne `listing` n'est modifiée ;
  - le rapport contient les effectifs, la confusion, les variations de score, les franchissements du seuil 75 et les désaccords illustrés.
- [ ] 7.2 Ajouter `--apply` (adjacence, `property_type` et `property_type_src` des sources non balayées, `rescore` avec `DISABLE_ALERTS=1`, résumé des biens alertables) et `--request-checks` (garage confirmé seulement, sauf type corrigé par l'utilisateur ou demande déjà en attente). Vérifier par des tests :
  - une seconde application ne recalcule que les échecs ;
  - `--request-checks` est idempotent et ignore les annonces sans garage confirmé.
- [ ] 7.3 Après un `pg_dump -t listing`, lancer l'analyse à blanc sur la base réelle. Vérifier :
  - durée inférieure à 30 min ;
  - rapport transmis à Alexandre.

  Recalibrer les seuils de D3 (annexe, contact minimal, emprise anormale) sur ses retours et sur les cas témoins du design : #15 `terraced`, #52425 `detached`, #108 `semi`, #259616 à trancher par les images. Mettre à jour `design.md` si les valeurs changent.

## 8. Skill, job Hermes et contrôle santé

- [ ] 8.1 Copier la skill `maison-enrichment` actuelle dans une sauvegarde datée, hors de son dossier, puis la mettre à jour (D15) :
  - section « Mitoyenneté » et section « Vérification du type » ;
  - schéma : `link_detached`, `type_evidence`, `_type_check` ;
  - pièges et interdiction des images Google ;
  - règles à recopier telles quelles dans le contexte des sous-agents.

  Vérifier par `skill_view`, puis par un passage manuel sur 2 annonces (planche, verdict, `maison post`) : réponse 200 et `type_info` mis à jour dans `maison show`.
- [ ] 8.2 Étendre `ops/healthcheck.py` :
  - sonde `maison mitoyennete-adresse` sur une adresse témoin publique (mairie) ;
  - taux d'échec des calculs depuis le contrôle précédent (au-delà de 20 % → ⚠️) ;
  - taille et ancienneté de la file des vérifications ;
  - état du cron `maison-type-check` ;
  - les demandes de vérification ne comptent pas comme des enrichissements bloqués.

  Vérifier que `python3 -m unittest discover -s ops` passe et que `python3 -I ops/healthcheck.py --no-state --json` affiche les nouveaux faits.
- [ ] 8.3 Créer le cron `maison-type-check` avec `cronjob_manage` (`40 8-22 * * *`, skill `maison-enrichment`, consigne de D13), d'abord en pause. Lancer un passage manuel sur 2 ou 3 demandes. Vérifier les POST, l'historique `type_checks` et la sortie de la file, puis activer le cron.

## 9. Mise en service et vérification d'ensemble

- [ ] 9.1 Déployer selon le Migration Plan : `docker compose build`, `up -d`, skill et copies du CLI à jour dans la même fenêtre. Vérifier :
  - la ligne « mitoyenneté » apparaît dans `docker compose logs collector` au cycle suivant ;
  - `maison mitoyennete <id>` fonctionne sur une annonce à adresse exacte connue ;
  - une fiche montre la photo aérienne, le cadastre, les bâtiments et la vue de rue.
- [ ] 9.2 Une fois le rapport validé par Alexandre, lancer `adjacency_backfill.py --apply`, puis `--request-checks`. Vérifier :
  - la répartition des types correspond au rapport ;
  - aucune alerte Discord n'est partie pendant l'application ;
  - environ 410 demandes sont créées ;
  - le lendemain, la file a reculé d'au moins 8 demandes par passage ;
  - le message de santé affiche la taille de la file.
- [ ] 9.3 Faire un commit local (auteur SoaAlex) du code, des tests, du README et des artefacts OpenSpec, sans push sans accord explicite. Vérifier `git log -1 --stat`, puis :
  - `git status` : aucun fichier de `data/`, de `tests/fixtures/` ni de `.scratch/`, aucun rapport ;
  - `git diff --cached` relu : aucune adresse ni coordonnée réelle dans les fichiers versionnés.

## Workflow follow-up

- Après l'écoulement de l'arriéré, relire un échantillon de désaccords entre la carte et l'agent, puis ajuster les constantes de D3 (questions ouvertes du design).
- Archiver le change (`openspec archive improve-adjacency-detection`) après validation en production. Vérifier ensuite que `openspec/specs/building-adjacency`, `property-type-resolution`, `photo-adjacency-review` et `listing-map-views` existent.
