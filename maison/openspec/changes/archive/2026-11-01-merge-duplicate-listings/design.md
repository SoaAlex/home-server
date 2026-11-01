# Design

## Context

Les motivations sont dans proposal.md, les comportements attendus dans `specs/listing-deduplication` et `specs/merged-listings`.

**État actuel** (lu dans le code et la base le 2026-11-01) :

- **Collecteur** : `app/collector.py` fait une boucle horaire. `run_once` enchaîne `upsert` par `(source, external_id)`, `mark_gone` (sources balayées en entier uniquement), puis `postprocess` (photos, géocodage, ADEME, trajets) et `rescore`, qui calcule le score et envoie les alertes Discord.
- **Cartes e-mail** (SeLoger, LeBonCoin) : `mark_gone` ne les traite jamais. Elles restent donc `active` indéfiniment, et chaque renvoi crée un nouvel `external_id`.
- **Photos** : `postprocess` télécharge au plus 10 photos par annonce dans `data/photos/<id>/NN.jpg`, mais seulement pour les annonces non exclues. Il sort tôt pour toute exclusion, y compris « à compléter ».
  - Base au 2026-11-01 : 1 730 annonces actives et 217 disparues, environ 11 300 `photo_urls` actives et environ 7 600 fichiers locaux pour les actives ; 716 annonces n'ont qu'une vignette (cartes e-mail de 500×370 px).
  - Bug existant : `photos.download` saute un `NN.jpg` déjà présent. Quand l'agent remplace `photo_urls`, le dossier mélange donc d'anciennes et de nouvelles photos (par exemple #144868 : `00.jpg` est la vignette du 16/10, `01` à `09` les photos du miroir du 18/10).
- **Empreinte actuelle** : `scoring.fingerprint`, de la forme `ville|prix/10k|surface/5|terrain/50`. Elle sert à deux choses : la garde anti-réalerte dans `collector.rescore` et le regroupement côté navigateur dans `app.js` (`filtered()`, case `#f-dups`).
- **Valeurs effectives** : `scoring.effective` (Python) et `eff()` (JS) superposent `enrichment` aux colonnes. `enrich` (POST de l'agent) écrit certaines colonnes directement (liste `DIRECT`).
- **Schéma** : appliqué au démarrage par le web, avec `lock_timeout` de 500 ms à cause d'un ancien interblocage, et par le collecteur. Les migrations doivent donc rester additives et idempotentes.
- **Contraintes externes** : aucune page ni API LeBonCoin ou SeLoger n'est appelée, seules les vignettes déjà présentes dans les e-mails sont utilisées. Les cron Hermes `maison-enrichment` (à :20) et `maison-address-investigation` lisent `/api/enrichment/pending`.

**Spike de mesure** (lecture seule, conteneur jetable `python:3.12-slim` avec Pillow et numpy, photos montées en lecture seule) :

- **Coût** : 20 ms par photo pour un pHash et un dHash, JPEG Bien'ici de 2 Mo en 1920×1440 compris (décodage en mode `draft`).
- **Paires aléatoires** (3 000 paires entre 250 annonces) : distance minimale au 1er centile de 16 en pHash et de 15 en dHash, médiane de 24 et 23. Les trois paires à 0 étaient de vrais doublons.
- **Vrais doublons** : pHash entre 0 et 8. Un cas Bien'ici ↔ vignette SeLoger donne p = 6 et d = 16 : le dHash est plus sensible au recadrage de la vignette.
- **Faux positifs d'une photo seule** :
  - #353/#356 : bandeau « Renée Costes » en dernière photo de deux viagers différents ;
  - #560/#563 : rendu commun à deux lots d'un programme Bouygues.
- **Doublons ratés par l'empreinte actuelle** :
  - #402/#403 : agence Deleu, Villeneuve-d'Ascq contre Wasquehal, 205 k€ contre 190 k€, cinq photos identiques ;
  - #545/#575 : Marcq-en-Barœul contre Wasquehal.
- **Faux groupe de l'empreinte** : dans le groupe d'empreinte de Croix à 85 m², certaines vignettes n'ont aucune photo commune.
- **Doublon sans photo commune** : #163 (Bien'ici) n'a que des photos d'intérieur dans ses 10 premières, alors que la carte SeLoger #110008 montre l'extérieur. Il faut donc aussi un indice non photographique.

## Goals / Non-Goals

**Goals:**
- Tenir le traitement incrémental en quelques millisecondes par photo et l'analyse initiale en quelques minutes sur le NAS, sans index ni service supplémentaire.
- Rendre toute fusion réversible sans perte : les données de chaque source restent dans sa ligne.
- Garder inchangés le collecteur (`upsert` et `mark_gone` par source) et le contrat de l'API d'enrichissement de l'agent.

**Non-Goals:**
- Pas d'IA de vision ni d'embeddings : le hachage perceptuel suffit, et un recadrage fort ou une photo en miroir ne seront pas reconnus.
- Pas de « fin de vie » des cartes e-mail dans la liste : leur visibilité reste celle d'aujourd'hui, et seule la fixation du prix les déclare « non confirmées ».
- Pas de correction du bug `photos.download` décrit ci-dessus. Les empreintes suivent les fichiers réels (taille et date) : le mélange de photos reste celui d'un même bien, donc sans effet sur la déduplication.

## Decisions

### D1 — Fusion par pointeur `merged_into`, sans suppression
`listing.merged_into BIGINT NULL REFERENCES listing(id) ON DELETE SET NULL`, avec `merged_at` et `merge_method` (`auto` | `user`). Les groupes sont plats : une secondaire pointe toujours vers la principale, et une principale n'a jamais de `merged_into`.

Alternatives écartées :
- (a) Supprimer les doublons : le collecteur les réinsère au cycle suivant, puisque `upsert` travaille par `(source, external_id)`, et la suppression n'est pas réversible.
- (b) Une table `listing_source` qui remplacerait l'unicité `(source, external_id)` : il faudrait réécrire `upsert`, `mark_gone`, les dictionnaires `known` des sources et l'API de l'agent.

### D2 — Vue du groupe matérialisée sur l'annonce principale (`listing.cluster JSONB`)
`dedupe.refresh_cluster(conn, principal_id)` recalcule un objet qui contient :
- `members` ;
- `offers` : identifiant, source, agence, URL, prix effectif, statut (`active` | `gone` | `stale`), `first_seen` et `last_seen`, triées par prix ;
- `price` : valeur et identifiant de l'offre ;
- `fill` : champs vides de la principale, complétés selon la priorité des sources ;
- `active` : vrai si au moins une offre a `status = 'active'` ;
- `price_history` : historique fusionné, avec la source de chaque entrée ;
- `photos` : photos de la principale, puis photos des secondaires non identiques à une photo déjà retenue (comparaison des empreintes).

Le recalcul a lieu après chaque fusion ou séparation et à la fin de chaque cycle du collecteur, pour tous les groupes : quelques centaines de lignes, en quelques millisecondes.

Ordre de résolution des valeurs, identique dans `scoring.effective` et dans `eff()` côté JS :
1. `enrichment[k]` ;
2. sinon la colonne de la principale ;
3. sinon `cluster.fill[k]`.

Pour le prix seulement, `cluster.price` passe en premier quand il existe. Il inclut déjà le prix effectif de la principale, correction de l'agent comprise.

Les cartes e-mail (`source IN ('seloger','leboncoin')`) dont `last_seen` date de plus de 45 jours sont des offres `stale` : elles ne fixent pas `cluster.price`, sauf s'il n'existe aucune offre active non `stale`.

Alternative écartée : calculer la vue à la lecture, par jointure dans `/api/listings`, `rescore` et les alertes. Cela demanderait une jointure à chaque usage, et la logique serait dupliquée entre SQL, Python et JS.

### D3 — Empreintes pHash et dHash avec Pillow et numpy
- **pHash** : niveaux de gris en 32×32, DCT-II par une matrice numpy précalculée, bloc 8×8 de basses fréquences, seuil à la médiane (sans le coefficient DC).
- **dHash** : 9×8, comparaison horizontale des voisins.
- **Mesure d'uniformité** : écart type des niveaux de gris en 16×16 (`std`).

Le décodage passe par `Image.draft("L", (64, 64))` : avec la mise à l'échelle DCT du JPEG, il est environ 8 fois plus rapide. Le collecteur hache les octets en mémoire, juste avant d'écrire le fichier dans `photos.download`.

Table `listing_photo` :
- colonnes `listing_id`, `idx`, `path`, `phash BIGINT`, `dhash BIGINT`, `std REAL`, `file_size`, `file_mtime`, `generic BOOL DEFAULT false`, `hashed_at` ;
- clé primaire `(listing_id, idx)`, `ON DELETE CASCADE` ;
- un `uint64` est stocké en `BIGINT` signé (complément à deux).

Alternatives écartées :
- `imagehash` : il tire `scipy`, environ 40 Mo, pour une DCT de 32×32 ;
- CLIP ou des embeddings : trop lourd pour le NAS sans GPU, et inutile pour des copies quasi exactes ;
- aHash : moins robuste aux variations de luminosité et de compression.

### D4 — Même photo : pHash ≤ 6, ou pHash ≤ 10 et dHash ≤ 10
Ces seuils viennent du spike : les non-doublons commencent vers 14-16, les doublons s'arrêtent vers 8. Le pHash est le critère principal, le dHash confirme les cas limites. Ce sont des constantes de `app/dedupe.py`, recalibrées sur le rapport à blanc (tâche dédiée).

### D5 — Comparaison exhaustive en mémoire avec numpy, sans index
Le périmètre comprend les annonces actives et les annonces disparues depuis 90 jours au plus, soit environ 12 000 photos. Leurs empreintes tiennent dans deux tableaux `uint64` d'environ 200 Ko, chargés à chaque appel.

- **Incrémental** : pour chaque nouvelle photo, `np.bitwise_count(arr ^ h)` sur le tableau, soit environ 0,1 ms par photo.
- **Analyse initiale** : comparaison N×N par blocs, environ 144 millions de comparaisons, une à deux secondes.

Il faut numpy 2.0 ou plus récent pour `bitwise_count`.

Alternatives écartées : multi-index hashing, BK-tree, extension Postgres. Elles sont plus complexes et sans gain à cette échelle. À reconsidérer au-delà d'environ 200 000 photos.

Les candidats « jumeaux » sans photo commune sont cherchés en Python, par regroupement sur `city_key` et sur des tranches de prix et de surface. Le volume, environ 2 000 lignes, le permet.

### D6 — Photos génériques : uniformité et incompatibilité
Une photo est marquée `generic` dans deux cas :
- `std < 12` : fond uni, logo ;
- une même photo relie des annonces incompatibles entre elles (veto de surface, de chambres ou de distance), comme un bandeau d'agence ou un rendu de programme.

Le marquage est recalculé à l'analyse initiale et lors de chaque nouvel appariement.

Alternative écartée : une liste noire par fréquence, où toute photo vue dans N annonces serait générique. Un vrai bien peut légitimement apparaître cinq fois (une annonce Bien'ici et quatre cartes SeLoger) avec la même photo de couverture.

### D7 — Règles de décision
Seuils de compatibilité :

| Critère | Compatible (auto) | Jumeau (fusion auto sans photo) | Veto |
|---|---|---|---|
| Surface | ≤ 5 % (≤ 3 % pour le texte) | ≤ 1 m² ou 1 % | > 12 % |
| Prix (écart rapporté au plus bas) | ≤ 15 % (1 photo), ≤ 10 % (texte) | ≤ 1 % | — |
| Chambres | — | égales si connues | écart ≥ 2 |
| Pièces | — | écart ≤ 1 si connues | — |
| Distance (positions, centroïdes compris) | — | même `city_key` ou ≤ 3 km | > 10 km, ou > 500 m entre deux positions `exact` (adresses différentes) |
| Terrain | — | ≤ 5 % si connu | — (terrain et jardin se confondent sur SeLoger) |

Les valeurs vides d'une annonce déjà fusionnée sont complétées par celles de son groupe (`cluster.fill`) avant la comparaison : un terrain connu d'une seule source compte pour tout le bien.

Calibrage sur la copie de la base du 2026-11-01 (1 947 annonces, 9 102 photos) :
- **Seuils jumeaux** : avec la première version à 3 % pour le prix et la surface, 767 paires « jumelles » étaient proposées, dont beaucoup de maisons différentes de même gabarit. La version à 1 % et 1 m² en propose 383, dont 284 avec au moins une annonce visible ; sur un échantillon de 12, une dizaine étaient bien le même bien.
- **Fusions automatiques** : sur un échantillon de 14 fusions « 1 photo commune », les 14 montrent la même photo réelle.
- **Décision d'Alexandre (2026-11-01, après le rapport à blanc)** : les jumeaux sont fusionnés automatiquement plutôt que proposés, en acceptant une erreur estimée à environ 10 %, rattrapable par « Séparer » (refus mémorisé).

Le **texte quasi identique** se mesure par la containment des 5-grammes de mots, normalisés par `textparse.norm`. Elle doit atteindre 0,8 au moins sur la description la plus courte, qui doit compter au moins 60 mots.

Le **programme neuf** se reconnaît de deux façons : `raw.newProperty` de Bien'ici (à ajouter aux clés conservées par `sources/bienici.py`), ou une expression régulière sur le titre et la description (`programme neuf|vefa|psla|frais de notaire reduits|livraison[^.]{0,30}20\d\d`). C'est un veto « doux » : la paire reste proposable comme doublon possible.

**Photos communes** : on compte les photos non génériques distinctes du plus petit ensemble qui ont au moins un équivalent de l'autre côté.

**Fusion automatique**, s'il n'y a aucun veto ni refus :
- au moins 2 photos communes ;
- ou 1 photo commune, avec la surface ou le prix compatible et aucun des deux incompatible ;
- ou un texte quasi identique, avec une surface à 3 % près et un prix à 10 % près ;
- ou des caractéristiques jumelles (colonne « Jumeau » du tableau), même sans photo commune.

Deux positions `exact` à plus de 500 m (adresses différentes) bloquent la fusion sauf avec au moins 2 photos communes : une adresse « probable » posée par l'agent peut être fausse, alors que deux photos identiques ne trompent pas. Avec une seule photo commune, la paire devient une suggestion ; sans photo, rien.

**Suggestion**, si la paire n'est pas fusionnée automatiquement, sans refus ni veto « dur » (surface, chambres, distance) : au moins une photo commune, ou un cas de fusion automatique bloqué par un programme neuf ou par un veto avec un autre membre du groupe (D8).

Alternative écartée : un score pondéré unique avec un seuil. Il est plus difficile à expliquer dans le rapport et dans la fiche. Les règles explicites se testent et s'affichent telles quelles (« 3 photos communes, surface 100/101 m², prix −3 % »).

### D8 — Formation des groupes : union-find avec contrôle sur tout le groupe
Les arêtes « auto » sont appliquées par ordre de force décroissante (nombre de photos communes, puis texte). Avant de rattacher une annonce à un groupe, ou de joindre deux groupes, on vérifie l'absence de veto avec chaque membre et l'absence de refus entre les deux groupes. Si la vérification échoue, l'arête devient une suggestion. Il n'y a ainsi pas de chaînage A ~ B ~ C avec A incompatible avec C.

Table `duplicate_pair` :
- colonnes `id_a < id_b`, `status` (`suggested` | `merged` | `rejected`), `decided_by` (`auto` | `user`), `evidence JSONB` (photos appariées, écarts, texte, règle appliquée), `created_at`, `decided_at` ;
- unicité sur `(id_a, id_b)`.

Les refus s'appliquent entre groupes : un refus entre deux membres quelconques bloque la fusion automatique des deux groupes.

Pour une arête « jumeaux » (sans photo commune), le contrôle sur tout le groupe porte aussi sur les incompatibilités « douces » (`soft_conflict` : terrain > 5 %, chambres différentes, surface > 5 %, prix > 15 %, adresses différentes), et les arêtes jumelles sont appliquées de la plus proche à la plus lointaine (écart prix + surface). Sans ce contrôle, la base du 2026-11-01 formait des groupes de 27 annonces mêlant des terrains de 130, 144 et 325 m². Avec lui, il ne reste aucun groupe hétérogène dû aux jumeaux. Les grands groupes restants (48 et 39 annonces) sont des cartes SeLoger renvoyées sous un nouvel identifiant à chaque e-mail, pour un seul bien.

### D9 — Annonce principale et report des données utilisateur
La principale est choisie selon l'ordre de la spec. Elle reste stable quand une nouvelle source rejoint le groupe. Quand deux groupes fusionnent, seules les deux principales sont départagées.

À la fusion, les données utilisateur des secondaires sont reportées sur les colonnes de la principale :
- `reaction` : `like` l'emporte sur `dislike`, qui l'emporte sur une absence de réaction ;
- `user_status` : `to_visit` si l'une des annonces l'a ;
- `user_note` : notes distinctes concaténées ;
- `seen_at` et `alerted_at` : la valeur la plus ancienne.

Chaque secondaire garde ses propres valeurs, ce qui permet de la séparer plus tard.

Limite assumée : après une séparation, la principale garde les valeurs reportées. Elles ne sont pas « dé-fusionnées ».

### D10 — Sérialisation par verrou consultatif
Toute fusion, séparation, refus ou rafraîchissement de groupe s'exécute dans une transaction qui prend `pg_advisory_xact_lock(hashtext('maison-dedupe'))`. Cela vaut pour le collecteur, l'analyse initiale et les routes web. Une analyse lancée pendant un cycle ne peut donc pas créer de chaîne ni de fusion concurrente.

### D11 — Intégration au collecteur
Modifications de `run_once` :
- **`postprocess`** : il télécharge aussi les photos quand l'exclusion est `à compléter` (`PENDING_OK`). Les autres exclusions sortent toujours tôt.
- **`photos.download`** : il renvoie les empreintes calculées en mémoire, qui sont écrites dans `listing_photo`.
- **Nouvelle étape** : après la boucle `postprocess`, `dedupe.process(conn, touched)` hache les fichiers non encore hachés ou modifiés, compare, puis fusionne ou suggère. Vient ensuite `dedupe.refresh_clusters(conn)`, puis `rescore`.
- **Annonces « nouvelles ou modifiées »** : la colonne `listing.dedupe_sig` garde une empreinte des valeurs comparées au dernier passage (empreintes photo, prix, surface, chambres, pièces, terrain, commune, position, texte). Seules les annonces du périmètre dont la signature a changé sont comparées à l'ensemble. Une correction de l'agent (commune d'une carte « à compléter », par exemple) est donc reprise au cycle suivant, sans recomparer toute la base à chaque heure.
- **`rescore`** : il traite les principales seulement (`merged_into IS NULL`). Son filtre devient `(status = 'active' OR (cluster->>'active')::boolean)`.
- **Alertes** : elles partent après la fusion. La garde existante par `fingerprint` reste en place et s'ajoute à `alerted_at`, reporté sur la principale.
- **`/api/enrichment/pending`** : il ajoute `merged_into IS NULL`. Une carte e-mail fusionnée dans le cycle n'est donc jamais proposée à l'agent, qui passe à :20, alors que la fusion a lieu pendant le cycle, quelques secondes après l'ingestion.
- **`POST /api/listings/{id}/enrichment` pour une secondaire** : l'enrichissement est enregistré sur la ligne de la secondaire, puis le groupe est recalculé et la principale rescorée. L'enrichissement de la principale n'est pas touché.

### D12 — API et UI
**Lecture**
- `GET /api/listings` : renvoie les principales seulement (`merged_into IS NULL`), avec `cluster` et `dup_pending`, le nombre de suggestions en attente, obtenu par sous-requête sur `duplicate_pair`.
- `GET /api/listings/{id}` : ajoute `cluster`, `members` (résumé de chaque secondaire) et `suggestions` (l'autre annonce en résumé, avec 3 photos et les preuves). Pour une secondaire, il renvoie la ligne avec `merged_into`.

**Actions** (sans jeton, comme `/reaction` et `/user`, puisque l'accès est limité au LAN et au VPN)
- `POST /api/listings/{id}/merge {other_id}` ;
- `POST /api/listings/{id}/split {member_id}` : enregistre un refus `rejected` entre la secondaire et le groupe, puis rescore les deux annonces. La secondaire rejoint la file d'enrichissement si elle n'a jamais été enrichie ;
- `POST /api/listings/{id}/not_duplicate {other_id}`.

**UI** (`app.js`, `style.css`, `index.html`)
- **Carte** : badge « N sources » et étiquette « Doublon ? » si `dup_pending > 0`. Le pied indique les sources (`bienici · seloger +1`). Le prix affiché est le prix retenu.
- **Fiche, en-tête** : le bouton « Annonce ↗ » devient un menu déroulant `<details>` qui liste les offres (source, agence, prix, statut « retirée » ou « non confirmée »), la moins chère en premier.
- **Fiche, prix** : le bloc prix ajoute « autres prix », l'historique est fusionné et le carrousel utilise `cluster.photos`.
- **Fiche, sections** : nouvelle section « Sources fusionnées » avec un bouton « Séparer » par offre, et bloc « Doublon possible » en côte à côte avec « Fusionner » et « Pas un doublon ».
- **Filtres** : la puce « Doublons à valider » remplace la case `#f-dups`. Le regroupement par `fingerprint` disparaît de `filtered()`.
- **Redirection** : `#/l/<id>` d'une secondaire redirige vers `#/l/<merged_into>`.
- **Alerte Discord** (`build_embed`) : prix retenu, URL de l'offre active la moins chère, champ « Sources ».

### D13 — Analyse initiale : `scripts/dedupe_backfill.py`
Lancement : `docker compose run --rm collector python scripts/dedupe_backfill.py [--apply] [--report PATH] [--workers 4] [--since-days 90]`

Étapes :
1. Appliquer le schéma.
2. Hacher les photos locales du périmètre avec `multiprocessing.Pool(workers)` : environ 12 000 photos × 20 ms ÷ 4, soit environ 1 min.
3. Télécharger les vignettes manquantes des cartes « à compléter », par `photos.download`.
4. Calculer les paires, les décisions et les groupes.
5. Écrire un rapport HTML autonome dans `/data/reports/dedupe-AAAAMMJJ-HHMM.html` (`data/` est ignoré par git), avec :
   - des vignettes en base64 de 160 px ;
   - les groupes « auto » ;
   - les suggestions ;
   - les photos génériques ;
   - les compteurs et les temps.
6. Avec `--apply` seulement : fusions et suggestions sous le verrou D10, puis `refresh_clusters`, puis `rescore` **sans alertes** (`DISABLE_ALERTS=1`) pour éviter une rafale Discord. Le résumé liste les biens qui deviennent alertables.

En mode à blanc, aucune ligne `listing` n'est modifiée. Seules les empreintes de `listing_photo` sont écrites, ce qui sert de cache.

### D14 — Dépendances et exploitation
- **Dépendances** : `pillow` et `numpy>=2` dans `requirements.txt` (roues disponibles pour `python:3.12-slim`, environ 30 Mo d'image en plus). `openspec/` est ajouté à `.dockerignore`.
- **Santé quotidienne** (`ops/healthcheck.py`) : `PENDING_SQL_FILTER` ajoute `merged_into IS NULL`, et un nouveau fait `dup_pending` donne une ligne ⚠️ informative quand des suggestions attendent depuis plus de 7 jours.
- **Skill Hermes `maison-enrichment`** : remplacer les pièges « Doublon d'un bien déjà traité » et « possible doublon » par la règle suivante. Les doublons sont fusionnés par le système et les secondaires n'entrent jamais dans la file. Un soupçon de doublon non détecté se signale dans `red_flags` avec l'identifiant de l'autre annonce.

## Risks / Trade-offs

- **Une fusion automatique à tort masque un bien distinct** (deux lots d'un même lotissement qui partagent une vue aérienne, par exemple) → vétos, marquage des photos génériques, programme neuf en suggestion seulement. Les jumeaux sans photo commune (environ 10 % d'erreur estimée sur échantillon) sont fusionnés par choix explicite. Le rapport à blanc est relu avant `--apply`. Le bouton « Séparer » s'accompagne d'un refus mémorisé.
- **Doublon raté** (sous-ensembles de photos différents, recadrage fort, miroir) → suggestions par caractéristiques jumelles et par texte. On accepte qu'une partie reste visible en double.
- **Prix plus bas, mais périmé, d'une carte e-mail** → statut `stale` après 45 jours sans renvoi. La fenêtre est réglable.
- **Report irréversible des données utilisateur sur la principale** → limité aux champs utilisateur et documenté. Les secondaires conservent leurs valeurs d'origine.
- **Verrou de schéma au démarrage** (historique d'interblocage) → uniquement des `ADD COLUMN IF NOT EXISTS` et `CREATE TABLE IF NOT EXISTS`, et le web garde `lock_timeout`.
- **Charge processeur de l'analyse initiale sur le NAS** → 4 workers, lancement en heures creuses, environ 1 à 2 minutes mesurées par extrapolation du spike.
- **Course avec les cron Hermes** → verrou consultatif. Un enrichissement posté sur une secondaire est accepté (D11).
- **Ancien lien Discord vers une secondaire** → redirection côté UI, et l'API renvoie `merged_into`.

## Migration Plan

1. `pg_dump -t listing maison`, sauvegarde à conserver jusqu'à validation.
2. `docker compose build`, puis lancer l'analyse à blanc avec la nouvelle image (`docker compose run --rm collector python scripts/dedupe_backfill.py`), sans redémarrer les services. Le schéma additif est sans effet pour l'ancien code, qui ignore les nouvelles colonnes.
3. Relire le rapport avec Alexandre, recalibrer les seuils D4 et D7 au besoin, puis relancer l'analyse à blanc.
4. `docker compose up -d` : le web et le collecteur passent au nouveau code, et le traitement incrémental est actif. Les photos existantes sont déjà hachées.
5. Lancer immédiatement `dedupe_backfill.py --apply`, puis contrôler l'UI, la liste, une fiche fusionnée, Séparer et la file `maison pending --standard`.
6. Commit local (auteur SoaAlex), sans push sans accord.

**Retour arrière** : `UPDATE listing SET merged_into = NULL, cluster = NULL`, puis redéploiement de l'image précédente. Les tables `listing_photo` et `duplicate_pair` deviennent inertes. Les données utilisateur reportées restent sur les principales, sans effet de bord, et la sauvegarde de l'étape 1 permet de revenir à l'état exact.

## Open Questions

- Valeurs finales des seuils D4 et D7, et fenêtre `stale` de 45 jours : à fixer d'après le rapport à blanc, sans effet sur l'architecture.
- Faut-il porter de 10 à 15 la limite de photos téléchargées par annonce pour améliorer le rappel (cas #163) ? Le coût disque et réseau est à mesurer après la mise en service.
