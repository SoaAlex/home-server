# Tasks

**Prérequis** : le change `improve-adjacency-detection` est en cours et non déployé, et il touche les mêmes fichiers (`web.py`, `app.js`, `style.css`, `index.html`, `schema.sql`, skill). On développe sur le code actuel du dépôt, sans branche, et le déploiement (groupe 6) attend que ce change soit prêt (sa tâche 9.1) ou fait.

**Tests** : `mrun pytest -q` depuis Hermes, ou `docker compose run --rm --no-deps -v "$PWD":/app web pytest -q` (base `maison_test`, vidée par les tests : jamais pendant une répétition sur une copie de la prod). Les tests versionnés n'utilisent que des textes génériques et les prénoms déjà publiés.

## 1. Schéma et configuration

- [x] 1.1 Ajouter à `db/schema.sql` la table `listing_comment` et son index (design D1), de façon idempotente. Vérifier par un test (`tests/test_comments.py`) que `init_schema` passe deux fois de suite sur `maison_test`, que la contrainte refuse un `body` vide ou de 2 001 caractères, et que la suppression d'une annonce supprime ses commentaires (`ON DELETE CASCADE`).
- [x] 1.2 Ajouter `comment_authors` à `config/criteria.example.yaml` (exemple) et à `config/criteria.yaml` (`[Alexandre, Marie]`), puis exposer la clé dans `/api/criteria` (liste vide si absente). Vérifier par un test de `tests/test_web.py` la réponse de `/api/criteria` avec et sans la clé, et par `git status` que `criteria.yaml` reste ignoré.

## 2. API des commentaires

- [x] 2.1 Implémenter `GET`/`POST /api/listings/{id}/comments`, `PATCH`/`DELETE /api/comments/{cid}`, avec la validation, le rattachement à la principale et la garde `X-Token` de D2 et D3. Vérifier par `tests/test_comments.py`, un test par scénario de la spec `listing-comments` côté API :
  - ajout (201, texte nettoyé des espaces) ; texte vide ou de 2 001 caractères → 400 ; annonce inconnue → 404 ;
  - auteur hors liste → 400 ; liste vide → auteur null accepté, auteur fourni → 400 ;
  - modification : auteur et `created_at` inchangés, `updated_at` posé ; commentaire inconnu → 404 ;
  - suppression → 204, puis absent du fil ;
  - `X-Token` présent sur les trois écritures → 403, aucune ligne créée ou modifiée ;
  - commentaire envoyé sur une secondaire → rangé sur la principale.
- [x] 2.2 Ajouter `comments` à `GET /api/listings/{id}` (fil du groupe, ordre `created_at, id`). Vérifier par des tests : fiche sans commentaire → liste vide ; groupe de deux annonces commentées → fil réuni et trié ; la réponse courte d'une secondaire reste inchangée.
- [x] 2.3 Ajouter `comments_n` et `comments_text` à `GET /api/listings` par la CTE de D4. Vérifier par des tests : 0 et null sans commentaire ; un bien fusionné compte les commentaires de toutes ses annonces une seule fois ; une secondaire n'apparaît toujours pas dans la liste.
- [x] 2.4 Vérifier que `POST /api/listings/{id}/enrichment` ne touche pas aux commentaires et qu'un commentaire ne change ni `score`, ni `reaction`, ni `user_status`, ni `enrichment_status`. Vérifier par un test qui compare ces colonnes avant et après un ajout, puis le fil avant et après un enrichissement posté.

## 3. Fusion et séparation

- [x] 3.1 Faire compter un commentaire comme donnée utilisateur dans `_rank()` (design D8), en ajoutant `has_comments` aux lignes candidates de `merge()` et des autres appelants. Vérifier par un test de `tests/test_dedupe_db.py` le scénario « Annonce commentée » : une annonce commentée l'emporte sur une annonce enrichie, et les tests de fusion existants restent verts.
- [x] 3.2 Vérifier sans modifier `split()` les scénarios de la spec `merged-listings` : deux annonces commentées fusionnées → fil de trois commentaires et `comments_n` = 3 ; après une séparation, la source séparée retrouve son commentaire d'origine et la principale garde celui écrit pendant la fusion. Vérifier par des tests de `tests/test_comments.py` qui passent par les routes `/merge` et `/split`.

## 4. UI

- [x] 4.1 Ajouter le bloc « 💬 Commentaires » de la fiche (`commentsBox(l)`, design D6) : fil, formulaire, choix de l'auteur mémorisé dans `localStorage["maison.author"]`, demande de l'auteur avant la première publication, `Ctrl/⌘+Entrée`, édition en place, suppression confirmée, rendu par `esc()` et `white-space: pre-wrap`, remplacement du seul bloc après chaque action. Vérifier dans le navigateur sur un serveur de test (`docker compose run --rm --no-deps -p 8091:8090 web …` sur `maison_test`, avec une capture par étape) :
  - ajout, modification (mention « modifié »), suppression confirmée puis annulée ;
  - auteur présélectionné à la fiche suivante, et demandé dans un profil de navigateur vierge ;
  - un commentaire `<img src=x onerror=alert(1)>` s'affiche tel quel, sans exécution ;
  - le carrousel reste sur la même photo après une publication.
- [x] 4.2 Ajouter le badge `💬 N` des cartes, le chip « 💬 Commentées » dans `index.html`, la recherche sur `comments_text` dans `filtered()`, la mise à jour de `state.all` et de la carte après une action, puis incrémenter les `?v=` d'`app.js` et de `style.css`. Vérifier dans le navigateur de test : badge absent puis « 💬 1 » après le premier commentaire, sans recharger ; filtre combiné avec « 👍 Aimées » ; recherche « humidite » qui trouve un commentaire « Humidité au sous-sol » ; aucune erreur dans la console.

## 5. Agent Hermes

- [x] 5.1 Ajouter `user_comments` à chaque entrée de `/api/enrichment/pending` et de `/api/enrichment/type_checks`, chargés en une requête par lot (design D7). Vérifier par des tests de `tests/test_web.py` : liste vide sans commentaire, commentaires du groupe sinon, et toujours aucune secondaire dans la file.
- [x] 5.2 Ajouter `user_comments` à la liste blanche de `pending` dans `scripts/maison_cli.py`. Vérifier par un test du découpage (fonction extraite si besoin) ou, à défaut, en lançant le script contre le serveur de test avec `MAISON_URL` : la clé figure dans la sortie et dans la copie `/tmp/pending*.json`.
- [x] 5.3 Mettre à jour la skill `maison-enrichment` après en avoir copié la version actuelle dans une sauvegarde datée hors de son dossier : `user_comments` est une indication de l'utilisateur (même rang que `address_request.note`), jamais une preuve, à citer « selon votre commentaire » ; interdiction d'appeler les routes de commentaires. Vérifier par `skill_view` que la règle figure dans la procédure et dans les consignes des sous-agents, et que le reste de la skill est inchangé (`diff` avec la sauvegarde).
- [x] 5.4 Ajouter une section « Commentaires » au `README.md` : usage dans l'UI, configuration `comment_authors`, routes, lecture par l'agent, refus de `X-Token`. Vérifier que les commandes et les routes citées correspondent au code (`grep` sur `web.py`) et que le README ne contient aucun commentaire réel.

## 6. Déploiement et contrôle (avec ou après `improve-adjacency-detection`)

- [x] 6.1 Une fois `improve-adjacency-detection` prêt à déployer, ou déjà déployé, lancer la suite complète (`mrun pytest -q`, verte), puis `docker compose build` et `docker compose up -d` (web et collecteur). Dans la même fenêtre, copier `scripts/maison_cli.py` vers `~/.local/bin/maison` et `~/.hermes/sandboxes/docker/default/workspace/bin/maison`. Vérifier :
  - `\d listing_comment` dans `common-postgres` ;
  - `/openapi.json` liste les quatre routes ;
  - `diff` vide entre le script du dépôt et ses deux copies ;
  - `maison pending --standard 1` contient `user_comments` ;
  - `docker logs --tail 50 maison-web` et `maison-collector` sans erreur.
- [x] 6.2 Contrôle en production avec Alexandre : un commentaire de test publié depuis son navigateur puis depuis celui de Marie (auteurs mémorisés distincts), visible dans la liste (badge, filtre, recherche) et dans `maison show <id>`, puis supprimé. Mesurer la taille de la réponse de `/api/listings` avant et après (`curl -s … | wc -c`). Vérifier que la base ne garde aucun commentaire de test et que l'écart de taille est noté dans le bilan.
- [x] 6.3 Faire un commit local (auteur SoaAlex) du code, des tests, du README, de `criteria.example.yaml` et des artefacts OpenSpec, sans `criteria.yaml` et sans push sans accord explicite. Vérifier `git log -1 --stat` et `git status` (aucun fichier de `config/criteria.yaml`, `data/` ni `tests/fixtures/` suivi).

## Workflow follow-up

- Archiver le change (`openspec archive add-listing-comments`) après validation d'Alexandre, ce qui crée la spec principale `listing-comments` et met à jour `merged-listings`.
- Vérifier ensuite `openspec validate --specs` et relire les deux specs principales.
