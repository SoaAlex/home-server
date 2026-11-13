# Design

## Context

Voir proposal.md (Why). État actuel observé dans le code :

- Les paires vivent dans `duplicate_pair` (`id_a < id_b`, `status` = `suggested` | `merged` | `rejected`, `evidence` JSONB). Les paires suggérées relient des annonces, pas des biens : après les fusions automatiques, 400 paires suggérées se ramènent à 109 couples de principales (mesuré le 2026-11-13).
- `web.py` expose déjà `POST /api/listings/{lid}/merge` et `/not_duplicate` (`dedupe.merge`, `dedupe.reject`, qui résolvent eux-mêmes les principales et prennent le verrou de déduplication), `_summary()` (résumé d'un bien, photos de cluster comprises, coupé à 4 photos) et `_suggestions()` (preuves de la meilleure paire par autre bien, par nombre de photos communes). `DUP_PENDING_CTE` compte les suggestions par principale pour la puce « Doublons à valider ».
- L'UI est un seul `app.js` sans framework : routes par hash (`#/`, `#/map`, `#/l/<id>` ouvert par-dessus `state.lastRoute`), délégation de clics sur `document`, `keydown` global réservé à la fiche ouverte, rendu par `innerHTML`. `dupPane()` et `evidenceText()` existent déjà pour le bloc « Doublon possible » de la fiche.
- Aucun outillage de test JavaScript ; les tests Python visent l'API sur une base `maison_test` (fixtures `client`, `dburl`).

## Goals / Non-Goals

**Goals:**
- Une file calculée côté serveur, déjà dédoublonnée par couple de principales, que l'UI parcourt sans autre appel par couple.
- Des décisions qui ne dépendent pas de l'ordre ni de la fraîcheur de la file côté navigateur (le collecteur fusionne en parallèle).
- Réutiliser la logique de fusion, de refus et de rescore existante, sans la dupliquer.

**Non-Goals:**
- Pas de changement de schéma, de règles de détection, de collecteur ni d'agent.
- Pas de pagination : la file tient en une réponse (une centaine de couples).
- Pas de persistance des couples « passés » ni de journal d'annulation côté serveur.

## Decisions

### D1. Couples ramenés aux principales côté serveur
`GET /api/duplicates?all=0|1` lit les paires `suggested`, ramène chaque côté à `coalesce(merged_into, id)`, écarte les couples internes à un groupe, puis regroupe par `(least, greatest)`. Pour chaque couple : les deux résumés, la meilleure paire (plus de photos communes, puis la plus récente) et ses `evidence`, le nombre de paires sous-jacentes, `created_at` le plus ancien. Tri : `greatest(score)` décroissant `NULLS LAST`, puis `created_at`.
- *Alternative* : réutiliser `/api/listings?dups=1` puis `/api/listings/{id}` par bien. Rejeté : un aller-retour par couple, chaque couple vu deux fois, et la déduplication des couples à refaire en JavaScript.

### D2. Résumés enrichis, sans toucher à `_summary()` des fiches
Un paramètre `max_photos` (défaut 4, 6 pour la revue) et les champs nécessaires au tableau (`rooms`, `dpe`, `first_seen`, `agency`, `score`, `reaction`, `status`, `excluded_reason`, sources, offres avec liens). Les valeurs passent par `effective()` / cluster comme le reste de l'API, donc identiques aux cartes. `first_seen` et `active` d'un bien fusionné viennent de son cluster quand il existe.
- *Alternative* : renvoyer les lignes complètes de `_view()`. Rejeté : charge utile inutile (description, adjacency, enrichissement entier) pour 2 × 100 biens.

### D3. Périmètre par défaut calculé en SQL
Un côté est « visible » s'il est actif (`ACTIVE_SQL`) et non exclu (`excluded_reason IS NULL OR = 'à compléter'`). Par défaut, un couple est gardé si au moins un côté est visible ; `all=1` garde tout. La réaction 👎 n'écarte pas : un doublon d'un bien rejeté reste utile à fusionner (la fusion propage 👎, spec `merged-listings`).
- *Alternative* : appliquer les filtres du panneau. Rejeté : la file ne serait jamais vide de façon fiable, et un filtre oublié masquerait des doublons.

### D4. Route de décision dédiée, avec contrôle « encore en attente »
`POST /api/duplicates/decision` avec `{"a": id, "b": id, "action": "merge" | "reject"}`. Sous le verrou de déduplication : résoudre les principales ; si elles sont égales ou s'il n'existe plus de paire `suggested` entre les deux groupes → `409 {"detail": "déjà traité"}` ; annonce absente → 404. Sinon `dedupe.merge(..., method="user")` puis `_rescore`, ou `dedupe.reject`. La réponse renvoie la principale retenue.
- *Alternative* : appeler les routes existantes `/merge` et `/not_duplicate`. Rejeté : elles acceptent n'importe quelle paire (c'est voulu pour « Fusionner avec… ») ; un refus envoyé après une fusion automatique créerait un refus entre deux annonces du même bien. Les routes existantes restent inchangées.

### D5. Annulation par décision différée côté navigateur
La décision reste dans `state.dups.pending` pendant 5 s avec un bandeau « Annuler (Z) ». Elle part à l'expiration, à la décision suivante, au changement de route (`hashchange`) ou à la fermeture de la page (`pagehide`, `fetch(..., {keepalive: true})`). Le couple courant avance tout de suite (affichage optimiste).
- *Alternative* : annuler côté serveur (défaire une fusion = séparer chaque membre ; défaire un refus = supprimer la ligne `rejected`). Rejeté : un refus est défini comme définitif par `listing-deduplication`, et défaire une fusion perdrait le report des données utilisateur.
- Conséquence acceptée : une décision prise juste avant une coupure réseau ou un crash du navigateur peut être perdue ; le couple revient alors simplement dans la file.

### D6. File locale resynchronisée après chaque envoi
Après chaque décision enregistrée, l'UI recharge `GET /api/duplicates` (requête légère) et `load()` pour la liste, puis repositionne la revue sur le premier couple non encore vu de la nouvelle file, sans réafficher ceux décidés ou passés pendant la visite. Les couples passés, envoyés ou en attente sont suivis par deux identifiants d'annonce `{a, b}`, pas par la clé `min-max` des principales : une fusion peut garder l'autre côté comme principale et changer la clé (A–C devient B–C après la fusion de A dans B). Un couple de la file correspond à un couple suivi quand chaque côté compte l'une des deux annonces parmi ses offres. Les couples passés sont remis en fin de file. Une réponse de file périmée (case cochée entre-temps) est ignorée. Une coupure réseau remet le couple à l'écran avec le message d'échec, même si le rechargement de la liste échoue aussi. Un 409/404 retire le couple et affiche un message court non bloquant.
- *Alternative* : patcher la file localement après une fusion. Rejeté : la principale retenue et les couples devenus internes dépendent de règles serveur ; recharger évite de les réimplémenter.

### D7. Intégration à l'UI existante
- Onglet `<a href="#/doublons" data-route="#/doublons">` ; `render()` gère une troisième route qui cache liste et carte et affiche `#dups`. Le nombre de l'onglet vient de la file chargée au démarrage avec `load()`.
- Le panneau des filtres reste visible mais sans effet sur cette vue ; le compteur de la barre affiche « 12 / 87 couples ».
- Les liens vers les fiches utilisent `#/l/<id>` : la fiche s'ouvre par-dessus `#/doublons` (mécanisme `lastRoute` existant) et se referme sur le même couple.
- Le gestionnaire `keydown` existant ne traite que la fiche ouverte ; un second gestionnaire traite la revue quand la route est `#/doublons`, la fiche fermée et la cible hors `input`/`textarea`/`select`.
- Tableau comparatif : comparaison stricte pour ville, DPE, pièces, chambres ; prix à 1 % près, surface et terrain à 1 m² ou 1 % près (tolérance des jumeaux de `listing-deduplication`) ; texte normalisé (minuscules, sans accents) pour adresse et agence.
- Réutilisation de `evidenceText()`. La vue de revue a son propre rendu de côté (`dupSide()` : offres avec liens, état, six photos) ; `dupPane()` du bloc de la fiche reste inchangé.

## Risks / Trade-offs

- [Le collecteur fusionne ou suggère pendant la revue] → D4 renvoie 409 pour un couple périmé ; D6 recharge la file après chaque décision, les nouveaux couples arrivent en fin de visite.
- [Fusion par erreur au clavier] → Annulation de 5 s (D5) ; au-delà, « Séparer » dans la fiche reste disponible.
- [Refus par erreur au-delà des 5 s] → Définitif par spec ; seule « 🔗 Fusionner avec… » le lève. Le bouton « Pas un doublon » n'est pas mis en avant (style secondaire), « Fusionner » non plus : les deux ont le même poids visuel, « Passer » est l'action par défaut.
- [Rescore lent après une fusion (postprocess réseau)] → La décision part en arrière-plan ; l'UI n'attend pas la réponse pour avancer. Seul le rafraîchissement de la file attend.
- [Charge utile de la file] → ~100 couples × 2 résumés à 6 photos (URL locales) : quelques dizaines de ko ; seul le couple courant est rendu, les photos du suivant sont préchargées.
- [Rescore sous le verrou de déduplication] → `_rescore` tourne pendant que la transaction tient le verrou, comme la route `/merge` existante : le collecteur peut attendre quelques secondes. Accepté (même comportement qu'avant).
- [Décision perdue si la page est fermée dans les 5 s et que `keepalive` échoue] → Le couple revient dans la file, rien d'incohérent n'est écrit.

## Migration Plan

- Aucune migration de base ni de configuration.
- **Mise en service** : avant de reconstruire, comparer l'image en service avec l'arbre (skill `maison-enrichment`, `references/maintenance.md`, « Déploiement, tests, commit ») pour vérifier qu'aucun autre change non déployé n'a modifié `app/` ou `db/`. Puis étiqueter l'image en service (`docker tag $(docker inspect maison-web --format '{{.Image}}') maison:rollback-<date>`), reconstruire (`docker compose build -q`) et ne recréer que `web` (`docker compose up -d --no-deps web`). Le collecteur n'est pas recréé ; la nouvelle image ne change pas son code.
- **Retour arrière** : `docker tag maison:rollback-<date> maison:latest` puis `docker compose up -d --no-deps web`. Aucune donnée à défaire : les décisions prises dans l'onglet sont des fusions et des refus ordinaires.

## Open Questions

- Libellé exact de l'onglet sur mobile (« Doublons » ou une icône) si la barre du haut manque de place ; à trancher à la vérification visuelle, sans effet sur la spec.
