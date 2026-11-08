# Design

## Context

- L'UI est une page unique sans framework (`app/static/app.js`). Elle charge `/api/listings` une fois, filtre et cherche côté navigateur, puis ouvre la fiche par `GET /api/listings/{id}`. Le site n'a pas d'authentification : il n'est joignable que depuis le LAN et le VPN. Seules les routes de l'agent exigent l'en-tête `X-Token` (`_auth`).
- Le schéma est appliqué au démarrage du web et du collecteur par `db.init_schema()`, avec des `CREATE … IF NOT EXISTS` et des `ALTER … ADD COLUMN IF NOT EXISTS` dans `db/schema.sql`. Le web cède au bout de 500 ms sur un verrou.
- Les biens fusionnés reposent sur `merged_into` (groupes plats). `dedupe.principal_of()` et `dedupe.group_ids()` donnent la principale et le groupe. `_fold_user_data()` reporte réaction, statut et notes à la fusion, et `_rank()` choisit la principale.
- La colonne `user_note` n'est utilisée par aucun écran. Ses 182 valeurs sont deux messages automatiques (voir proposal.md, Why).
- L'agent lit les files par `scripts/maison_cli.py` (`maison pending`, `maison show`). Ce script réduit les entrées à une liste blanche de clés avant de les copier dans `/tmp/pending*.json`, et deux copies à jour vivent hors du dépôt.
- Le change `improve-adjacency-detection` (20 tâches sur 33) modifie aussi `web.py`, `app.js`, `style.css`, `schema.sql` et la skill. Il n'est pas déployé : l'image en service date du 2026-11-01 et n'expose pas `/api/tools/adjacency`.

## Goals / Non-Goals

**Goals:**
- Un modèle de données qui garde l'origine de chaque commentaire, afin qu'une séparation de sources reste réversible sans déplacer de données.
- Pas de requête supplémentaire par carte : la liste reçoit le compteur et le texte cherchable dans la réponse de `/api/listings`.
- Rafraîchir le bloc de la fiche sans la reconstruire, pour garder le carrousel, le défilement et la mini-carte.

**Non-Goals:**
- Pas d'identité vérifiée ni de droits par auteur : n'importe quel navigateur du LAN peut modifier ou supprimer n'importe quel commentaire, comme il peut déjà changer une réaction.
- Pas d'historique des versions d'un commentaire, ni de corbeille.
- Pas de pagination du fil : quelques commentaires par bien au plus.

## Decisions

### D1. Table dédiée `listing_comment`, `user_note` laissée telle quelle
```sql
CREATE TABLE IF NOT EXISTS listing_comment (
    id         BIGSERIAL PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    author     TEXT,
    body       TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS listing_comment_listing_idx ON listing_comment (listing_id, created_at);
```
- *Pourquoi* : un fil daté, signé et modifiable ligne par ligne ne tient pas dans un TEXT, et `user_note` est déjà polluée par des messages automatiques.
- *Alternatives écartées* : un tableau JSONB dans `listing.enrichment`, exposé à la fusion `enrichment || body` de l'agent et donc à une perte de commentaires ; une reprise de `user_note` dans le fil, qui y aurait importé 182 messages automatiques.
- `author` reste un TEXT libre en base. La validation contre la liste configurée se fait à l'écriture. Ainsi, renommer un auteur dans la configuration n'invalide pas les anciens commentaires.

### D2. Rattachement à la principale à l'écriture, lecture par groupe
- `POST /api/listings/{id}/comments` range le commentaire sur `principal_of(id)`. Les lectures (fiche, liste, files de l'agent) réunissent les commentaires de `group_ids(principal)`.
- La fusion ne déplace aucune ligne : une secondaire garde les commentaires écrits sur elle avant la fusion. La séparation n'a rien à faire non plus : la source séparée retrouve ses propres commentaires, et ceux écrits pendant la fusion restent sur la principale (spec `merged-listings`).
- *Alternative écartée* : déplacer les commentaires vers la principale dans `_fold_user_data()`, comme les notes. Une séparation ne pourrait alors plus rendre ses commentaires à la source, faute de connaître leur origine.

### D3. Routes de l'API
| Route | Rôle |
|---|---|
| `GET /api/listings/{id}/comments` | fil du groupe, ordre `created_at, id` |
| `POST /api/listings/{id}/comments` | `{author, body}` → 201 et le commentaire créé |
| `PATCH /api/comments/{cid}` | `{body}` → commentaire mis à jour, `updated_at = now()` |
| `DELETE /api/comments/{cid}` | 204 |

- Validation : `body.strip()` de 1 à 2 000 caractères, sinon 400. Un `author` hors de `comment_authors` donne 400 quand la liste n'est pas vide ; un `author` non vide alors que la liste est vide donne aussi 400. Une annonce ou un commentaire inconnu donne 404.
- Garde contre l'agent : les trois routes d'écriture répondent 403 si l'en-tête `X-Token` est présent. Le CLI `maison` l'envoie sur toutes ses requêtes, donc un agent qui passerait par lui, ou qui copierait son en-tête dans un `curl`, est bloqué. Ce n'est pas une sécurité contre un client malveillant du LAN, qui n'est pas dans le modèle de menace (voir Non-Goals).
- `GET /api/listings/{id}` ajoute `comments` (même forme que la route du fil). Pour une secondaire, la réponse courte actuelle reste inchangée, et l'UI redirige vers la principale.

### D4. Liste : compteur et texte agrégés en une requête
`/api/listings` joint un agrégat calculé par principale :
```sql
cm AS (SELECT coalesce(l.merged_into, l.id) AS cm_lid, count(*) AS comments_n,
              string_agg(c.body, ' ' ORDER BY c.created_at) AS comments_text
       FROM listing_comment c JOIN listing l ON l.id = c.listing_id GROUP BY 1)
```
- Il se branche sur le même modèle que `DUP_PENDING_CTE`. `comments_n` vaut 0 et `comments_text` vaut null pour un bien sans commentaire.
- *Pourquoi* le texte complet plutôt qu'une recherche côté serveur : la recherche actuelle est entièrement côté navigateur (`filtered()`), et le volume reste faible (quelques dizaines de biens commentés, 2 000 caractères au plus par commentaire).

### D5. Auteurs dans `config/criteria.yaml`
- Clé `comment_authors: [Alexandre, Marie]`, lue par `settings.load_criteria()` et exposée par `/api/criteria`. `criteria.example.yaml` reçoit un exemple neutre.
- *Pourquoi* : le fichier réel est déjà hors git et contient les données du foyer. Une variable d'environnement aurait demandé de redémarrer le conteneur à chaque changement, et l'UI la lit déjà par `/api/criteria`.
- Navigateur : `localStorage["maison.author"]`. Une valeur mémorisée qui n'est plus dans la liste est ignorée.

### D6. UI
- Fiche : un bloc « 💬 Commentaires » dans la colonne de gauche, juste après le bloc prix, rendu par une fonction `commentsBox(l)`. Après chaque action, seul ce bloc est remplacé (`outerHTML`). La fiche n'est pas rouverte par `reopen()`, qui remettrait le carrousel à zéro et recréerait la mini-carte.
- Formulaire : un `textarea`, des boutons d'auteur à bascule (même style `.seg` que les filtres), « Publier », et `Ctrl/⌘+Entrée` pour publier. Chaque commentaire a ✏️ (édition en place, « Enregistrer » ou « Annuler ») et 🗑 (`confirm()`).
- Rendu : tout passe par `esc()`, et le CSS `white-space: pre-wrap` garde les retours à la ligne sans `innerHTML` de texte utilisateur. Les dates sont en `toLocaleString("fr-FR")`, avec « modifié le … » si `updated_at` existe.
- Liste : un tag `💬 N` dans `.tags`, dont le `title` montre le début du texte des commentaires (`comments_text`, 160 caractères) ; un chip `data-chip="comments"` ; `comments_text` ajouté à la chaîne normalisée de `filtered()`. Après une action dans la fiche, l'entrée de `state.all` (`comments_n`, `comments_text`) est mise à jour, et la carte est re-rendue si elle est visible.
- Versions `?v=` d'`app.js` et de `style.css` incrémentées dans `index.html`, au-delà de celles que pose le change de mitoyenneté.

### D7. Agent
- `/api/enrichment/pending` et `/api/enrichment/type_checks` ajoutent `user_comments` (liste de `{author, body, created_at, updated_at}`) à chaque entrée. Les commentaires sont chargés en une requête pour tous les identifiants du lot (`listing_id = ANY(...)`, puis regroupement par principale), sans requête par annonce.
- `scripts/maison_cli.py` ajoute `user_comments` à la liste blanche de `pending`. `maison show` passe par `GET /api/listings/{id}`, qui contient déjà `comments`. Les deux copies hors dépôt sont mises à jour dans la même fenêtre que le déploiement.
- Skill `maison-enrichment` : un commentaire est une indication de l'utilisateur, au même rang que `address_request.note`. Il oriente la recherche (repère, rue, défaut vu en visite) mais ne prouve aucun fait publié. Un fait vu en visite et rapporté par l'utilisateur (« pas de garage en fait ») va dans `red_flags` ou `summary_fr` avec la mention « selon votre commentaire ». Il est interdit d'appeler les routes de commentaires.

### D8. Choix de la principale
`_rank()` lit un booléen `has_comments` que les requêtes de `merge()` ajoutent aux lignes candidates (`EXISTS (SELECT 1 FROM listing_comment …)`). Le critère 2 devient « réaction, statut, note ou commentaire ». Les autres appelants de `_rank`, s'il y en a, reçoivent la même colonne.

## Risks / Trade-offs

- [Suppression ou modification involontaire par l'autre personne, sans identité vérifiée] → confirmation avant suppression, mention « modifié » visible ; les sauvegardes restic nocturnes de Postgres permettent une récupération manuelle.
- [Conflits de fusion avec `improve-adjacency-detection`, qui touche les mêmes fichiers] → développement sur le code actuel et déploiement avec ou après ce change (choix d'Alexandre). Les ajouts restent localisés : nouvelles routes, nouveau bloc, nouvelle CTE, sans réécriture des fonctions existantes.
- [Données personnelles dans un dépôt public] → les commentaires restent en base et dans `data/`. Les tests utilisent des textes génériques, et seuls les prénoms déjà publiés apparaissent dans `criteria.example.yaml` et les tests (accord du 2026-10-28).
- [Copies du CLI désynchronisées : l'agent ne verrait pas les commentaires] → mise à jour des deux copies dans la tâche de déploiement, puis contrôle par `diff` et par `maison pending --standard 1`.
- [Poids de `/api/listings` avec le texte des commentaires] → borné par 2 000 caractères par commentaire et par le petit nombre de biens commentés ; à mesurer au déploiement.
- [Un commentaire ne relance pas l'agent] → limite voulue (proposal.md, hors périmètre) : le bouton « Investiguer » reste le moyen explicite.

## Migration Plan

1. Migration additive : la table et l'index sont créés au démarrage par `init_schema()`. Aucune donnée existante n'est modifiée, `user_note` comprise.
2. Déploiement groupé avec `improve-adjacency-detection` (sa tâche 9.1) ou juste après : `docker compose build`, puis `docker compose up -d` du web et du collecteur (`app/dedupe.py` change aussi pour le collecteur). Dans la même fenêtre : mise à jour de la skill et des copies du CLI.
3. Retour arrière : redéployer l'image précédente. La table `listing_comment` reste en place sans effet sur l'ancien code, qui l'ignore. La supprimer seulement si l'on abandonne la fonction (`DROP TABLE listing_comment`).
