# Design

## Context

- L'UI est une page unique sans framework (`app/static/app.js`). `load()` charge `/api/listings` une fois ; `filtered()` applique tous les filtres côté navigateur ; `render()` alimente la liste, la carte et le compteur. Les champs du panneau déclenchent `rerender()` par un seul écouteur `input` sur `#filters`, et « Réinitialiser » vide tous les `input` du panneau avant de recharger.
- Le filtre actuel du terrain est `#f-land` lu dans `filtered()` (`minLand`) : `(+eff(l, "land_m2") || 0) < minLand` masque déjà un terrain inconnu. Il n'existe aucun filtre sur la surface habitable.
- `GET /api/listings` renvoie `surface_m2` et `land_m2` de chaque bien. Pour un bien fusionné, `merged_columns()` y a déjà recopié le terrain des autres sources quand il est vide (spec `merged-listings`), et `eff(l, clé)` côté navigateur ajoute la correction de l'agent (`enrichment[clé]`) : c'est la valeur que la carte affiche (📐, 🌱), et `fmt()` y met « ? » quand elle manque.
- Données du 2026-11-08, liste par défaut de 929 annonces : surface habitable connue pour 928 (27 à 377 m², médiane 105) ; terrain connu pour 682 (0 à 2 323 m², médiane 204), dont 3 à 0 m² et 21 au-dessus de 1 000 m² ; 247 sans terrain. Aucune annonce n'a de surface corrigée dans `enrichment` (l'agent écrit dans les colonnes).
- Les trois fichiers d'UI sont aussi ceux de `add-listing-comments` et de `improve-adjacency-detection`. Quand ce change a été proposé (2026-11-08), leurs tâches d'UI s'appliquaient dans le même arbre de travail et aucun des deux n'était déployé. À la livraison, le même jour, ils sont commités et en service : les fichiers d'`app/` de l'image de `maison-web` sont identiques à ceux de l'arbre, hors les trois fichiers de ce change (`?v=12` en service).

## Goals / Non-Goals

**Goals:**
- Un filtre qui voit ce que la carte montre : si la carte affiche « 🌱 367 m² », le filtre lit 367.
- Ne jamais masquer des annonces sans le dire : le nombre d'annonces écartées faute de surface se lit à l'écran.
- Aucun changement de contrat serveur.

**Non-Goals:**
- Pas de filtrage serveur, de paramètre d'URL ni de mémorisation des bornes.
- Pas de nouvelle donnée de surface.
- Pas de refonte du panneau : type, DPE, villes, score et prix gardent leur comportement.

## Decisions

### D1. « Surface totale » = surface de la parcelle (`land_m2`)
- *Pourquoi* : Maison n'enregistre que deux surfaces, `surface_m2` (habitable) et `land_m2` (terrain, affiché 🌱). « Surface totale » face à « surface habitable » désigne donc la parcelle. Certaines agences emploient pourtant « surface totale » pour la surface de la maison (fixture `tests/fixtures/iec_list.html` : « Maison individuelle de 115m2 de surface totale ») : le libellé « Surface totale – terrain » lève le doute à l'écran.
- *Alternative écartée* : une « surface bâtie totale » (annexes comprises). Aucune source ne la distingue de la surface habitable ; elle exigerait une colonne et une extraction dans chaque source, soit un change à part. À confirmer à la relecture : si Alexandre pensait à autre chose, les artefacts sont à reprendre avant d'appliquer.

### D2. Filtrage côté navigateur, sur les valeurs déjà reçues
- *Pourquoi* : `filtered()` alimente déjà la liste, la carte et le compteur ; la réponse contient déjà les deux surfaces, et le jeu de données (929 annonces) se filtre sans délai perceptible.
- *Alternative écartée* : des paramètres `min_surface` / `max_land` sur `/api/listings`. Un aller-retour par frappe, un contrat d'API de plus et deux chemins de filtrage à garder cohérents, sans gain.

### D3. Quatre champs et un prédicat à quatre issues
- Champs : `#f-surf-min`, `#f-surf-max`, `#f-land-min`, `#f-land-max` (`type="number"`, `min="0"`, `inputmode="decimal"`, pas de 5 m² pour l'habitable et de 50 m² pour le terrain comme l'ancien champ). `#f-land` disparaît.
- Une borne vaut 0 (aucune borne) quand le champ est vide, nul ou non numérique, comme `#f-price` et l'ancien `#f-land`. Le navigateur normalise la saisie d'un champ numérique selon sa langue et `.value` porte toujours un point : « 99,5 » donne 99,5 dans un navigateur en français, alors qu'un Chromium en anglais ignore la virgule (« 99,5 » devient 995, mesuré), comme pour « Prix max ».
- `surfaceStatus(valeur, min, max)` rend `"off"` (aucune borne sur cette surface), `"unknown"` (borne posée, valeur absente), `"out"` ou `"ok"`. La valeur est inconnue quand `eff()` rend null, « » ou NaN : c'est exactement le cas où `fmt()` affiche « ? ». 0 est une valeur.
- *Pourquoi quatre issues* : le filtre et la mention des annonces masquées partagent la même évaluation, donc la mention ne peut pas diverger de ce que la liste montre.

### D4. Ordre d'évaluation et comptage dans `filtered()`
- Tous les filtres existants passent d'abord. Pour les annonces restantes, on calcule les deux statuts : `out` sur l'une ou l'autre exclut l'annonce sans la compter ; sinon un `unknown` l'exclut et la compte pour chaque surface inconnue ; sinon elle est gardée. Une annonce sans les deux surfaces compte donc dans les deux mentions.
- Les compteurs sont rangés dans `state.hidden` (`{surf, land}`) et `render()` les affiche, une seule fois par rendu. La fonction continue de ne renvoyer que la liste triée.

### D5. Surface inconnue masquée, avec une mention chiffrée
- Statu quo de « Terrain min » : dès qu'une borne est posée, l'inconnue sort. La mention (`<small class="hint" aria-live="polite">`, sous le filtre) rend l'effet visible : « N annonces sans terrain connu sont masquées », « 1 annonce sans surface habitable connue est masquée ».
- *Alternatives écartées* : garder toujours les inconnues (un minimum de 300 m² laisserait passer 247 annonces dont on ne sait rien, et le comportement actuel s'inverserait) ; une case « inclure les inconnues » (un contrôle de plus pour un cas que la mention rend déjà visible ; elle s'ajoutera plus tard sans toucher au reste si le besoin se confirme).

### D6. Panneau : deux sections min/max, « Prix max » seul
- « Prix max (k€) » garde son champ, seul sur sa ligne. Puis deux sections avec un `h4` (« Surface habitable (m²) », « Surface totale – terrain (m²) »), une ligne `.row2` « Min » / « Max » dont chaque champ porte un `aria-label` complet, et la mention. Les styles réutilisent `.row2` et les champs `.filters input[type=number]` ; seul `.hint` est nouveau, avec les variables de thème existantes (clair et sombre).
- La réinitialisation et l'écouteur `input` couvrent déjà les nouveaux champs ; la mention se recalcule au `load()` qu'elle déclenche. Aucun câblage de plus.

### D7. Vérification sans base ni conteneur
- Un serveur jetable en bibliothèque standard (`.scratch/surface_ui_server.py`, ignoré par git et par l'image) sert le dépôt à la volée : `/` = `app/templates/index.html`, `/static/…` = `app/static/…`, plus deux JSON pour `/api/listings` et `/api/criteria`. Le JSON est un instantané de `GET /api/listings` en production (lecture seule) complété d'annonces fabriquées pour les cas limites : surface inconnue, terrain à 0, décimales, bornes exactes, terrain d'une source fusionnée, annonce 👎. Un calcul Python indépendant sur le même JSON doit redonner les nombres que le navigateur affiche.
- *Pourquoi* : toute la logique est dans le navigateur. `maison_test` est partagé avec les tests, qui la vident, et avec le serveur de test du change des commentaires.
- *Alternatives écartées* : un test unitaire du prédicat sous Node (Node absent de l'image `web`, pour une quinzaine de lignes) ; `maison_test` et un conteneur de test (partagés, vidés par `pytest`).
- Le contrat de données, lui, se fige par un test pytest : `/api/listings` renvoie `surface_m2` et `land_m2`, terrain fusionné compris.

## Risks / Trade-offs

- [247 annonces (27 %) disparaissent dès qu'une borne de terrain est posée] → mention chiffrée sous le filtre ; décision réversible en une case à cocher (D5).
- [L'hypothèse « surface totale = terrain » est fausse] → le prédicat est indépendant du champ lu ; seuls les libellés et la clé lue changeraient. À trancher à la relecture (D1).
- [Un autre agent modifie `app.js`, `style.css` et `index.html` en même temps] → relire le fichier juste avant chaque modification, patcher par petits blocs et ne jamais réécrire un fichier entier ; contrôler `git diff --stat` avant le commit.
- [« 99,5 » tapé dans un navigateur dont la langue n'est pas le français devient 995] → même comportement que « Prix max » ; les surfaces se saisissent le plus souvent en entiers ; la saisie décimale est vérifiée sous un Chromium en français (tâche 2.1). Basculer les champs en `type="text"` avec une lecture maison réglerait le cas, au prix des flèches et de la cohérence avec les autres champs.
- [La carte arrondit (`fmt()` : 99,6 m² s'affiche « 100 m² ») alors que le filtre compare la valeur exacte : un minimum de 100 écarte une annonce dont la carte dit « 100 m² »] → mesuré sur l'API de production le 2026-11-08 : 1 044 surfaces habitables connues, dont 134 non entières, et au plus 2 annonces concernées par borne testée de 60 à 300 m² (2 pour un minimum de 100, 0,14 en moyenne) ; 2 terrains non entiers sur 745. Valeur exacte gardée, comme l'ancien « Terrain min » et comme les scénarios de la spec (99,5 est écartée par un minimum de 100). Passer à la valeur arrondie tient en une ligne (`Math.round` dans `surfaceValue`), avec la mise à jour de scénarios de la spec et des nombres de la tâche 2.1.

## Migration Plan

- Aucune migration de données ni de configuration.
- **Mise en service** : le 2026-11-08, l'image de `maison-web` porte déjà les UI des commentaires et de l'adjacence (`app/`, `db/` et `requirements.txt` identiques à ceux de l'arbre, hors les trois fichiers d'UI de ce change, contrôlés par `md5sum`). Étiqueter l'image en service (`docker tag $(docker inspect maison-web --format '{{.Image}}') maison:rollback-<date>`), reconstruire (`docker compose build -q`), puis ne recréer que le service web (`docker compose up -d --no-deps web`, en tâche de fond) : quelques secondes d'indisponibilité, le collecteur n'est pas touché et son cycle continue.
- **Avant de choisir cette voie**, refaire la comparaison de l'image en service avec l'arbre (skill `maison-enrichment`, `references/maintenance.md`, « Déploiement, tests, commit ») : si un autre change non déployé a modifié `app/` ou `db/`, reconstruire l'image l'embarquerait ; il faudrait alors le déploiement ciblé de la skill, ou attendre ce change.
- Retour arrière : `docker tag maison:rollback-<date> maison:latest` puis `docker compose up -d --no-deps web`, ou revert du commit (UI seule) puis nouveau déploiement.
