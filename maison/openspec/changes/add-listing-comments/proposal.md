# Proposal

## Why

Alexandre et Marie trient les annonces dans l'UI Maison, mais n'ont aucun endroit où écrire ce qu'ils en pensent : « vue de la rue décevante », « Marie a appelé l'agence, visite samedi », « la cuisine est à refaire », etc. Ces remarques se perdent aujourd'hui dans des messages ou de mémoire, alors que la fiche est l'endroit naturel où les relire avant une décision ou une visite.

La colonne `listing.user_note` existe déjà, mais aucun écran ne l'affiche ni ne la modifie. Le 2026-11-07, ses 182 valeurs non vides se réduisent à deux textes automatiques (« Écartée automatiquement le 01/11/2026 : … »), et `POST /api/listings/{id}/user` l'écrase si on l'appelle sans `user_status`. Elle ne peut donc pas servir de fil de discussion à deux personnes.

## What Changes

- **Fil de commentaires par bien** : chaque fiche affiche les commentaires datés du bien, du plus ancien au plus récent, avec leur auteur. Un commentaire peut être ajouté, modifié (mention « modifié ») et supprimé (avec confirmation). Les commentaires vont dans une nouvelle table `listing_comment` et n'utilisent pas `user_note`.
- **Auteur sans connexion** : l'auteur se choisit parmi une liste configurée dans `config/criteria.yaml` (Alexandre, Marie). Le dernier choix est mémorisé dans chaque navigateur. Le site reste sans authentification (LAN et VPN uniquement).
- **Commentaires dans la liste** : un badge « 💬 N » sur la carte de l'annonce, un filtre rapide « 💬 Commentées », et une recherche qui porte aussi sur le texte des commentaires.
- **Biens fusionnés** : un commentaire est rattaché au bien affiché, c'est-à-dire à l'annonce principale. La fiche d'un bien fusionné montre les commentaires de toutes les annonces de son groupe. Une annonce commentée compte comme porteuse de données utilisateur pour le choix de la principale. Une source séparée reprend les commentaires écrits sur elle avant la fusion.
- **Lecture par l'agent Hermes** : la file d'enrichissement, la file des vérifications de type, `maison pending` et `maison show` exposent les commentaires du bien (`user_comments`). La skill `maison-enrichment` les traite comme des indices de l'utilisateur, jamais comme une preuve. L'agent n'écrit jamais de commentaire : l'API refuse toute écriture de commentaire faite avec le jeton de l'agent.
- Hors périmètre : notifications (Discord ou autres), réponses en fil imbriqué, pièces jointes ou photos, effet sur le score, remise en file de l'agent quand un commentaire est ajouté (le bouton « Investiguer » reste le moyen de le relancer), reprise ou nettoyage de `user_note`.

## Capabilities

### New Capabilities
- `listing-comments` : fil de commentaires de l'utilisateur sur un bien (création, modification, suppression, auteur, affichage dans la fiche et dans la liste, recherche, filtre, lecture seule par l'agent).

### Modified Capabilities
- `merged-listings` : les commentaires entrent dans les données utilisateur d'un groupe. Ils comptent pour le choix de l'annonce principale, sont réunis sur la fiche du bien fusionné et suivent la source séparée.

## Impact

- **Base** : nouvelle table `listing_comment` dans `db/schema.sql`, créée au démarrage par `db.init_schema()` (migration additive, aucune donnée existante modifiée).
- **API** (`app/web.py`) : `GET`/`POST /api/listings/{id}/comments`, `PATCH`/`DELETE /api/comments/{cid}`. Ajout de `comments` à `GET /api/listings/{id}`, de `comments_n` et `comments_text` à `GET /api/listings`, de `user_comments` à `/api/enrichment/pending` et `/api/enrichment/type_checks`, et des auteurs à `/api/criteria`.
- **Doublons** (`app/dedupe.py`) : prise en compte des commentaires dans `_rank`.
- **UI** (`app/static/app.js`, `style.css`, `templates/index.html`) : bloc « Commentaires » de la fiche, badge, filtre, recherche, versions `?v=` incrémentées.
- **CLI et agent** : `scripts/maison_cli.py` et ses copies (`~/.local/bin/maison`, `~/.hermes/sandboxes/docker/default/workspace/bin/maison`), skill `maison-enrichment`.
- **Configuration** : `config/criteria.yaml` (hors git) et `config/criteria.example.yaml` (clé `comment_authors`).
- **Livraison** : le change `improve-adjacency-detection`, en cours et non déployé, touche les mêmes fichiers. Les commentaires se développent sur le code actuel et partent en production avec ce change ou après lui, jamais avant.
