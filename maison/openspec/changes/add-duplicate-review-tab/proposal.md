# Proposal

## Why

Le 2026-11-13, la base compte 400 paires « doublon possible » en attente, soit 109 couples de biens distincts une fois ramenés à leurs annonces principales (87 dont aucun côté n'est exclu). Pour les traiter, il faut aujourd'hui activer la puce « Doublons à valider », ouvrir chaque fiche, descendre jusqu'au bloc « ⚠️ Doublon possible », cliquer, puis revenir à la liste. Chaque couple apparaît deux fois (une fiche par côté) et rien n'indique combien il en reste. Alexandre veut un onglet dédié pour les passer en revue vite, l'un après l'autre.

## What Changes

- **Nouvel onglet « Doublons »** dans la barre du haut, à côté d'« Annonces » et de « Carte » (route `#/doublons`), avec le nombre de couples à valider.
- **File de revue** : un couple de biens à la fois, chaque couple une seule fois (et non une fois par côté), avec la position dans la file (« 12 / 87 »).
- **Comparaison côte à côte** : photos plus grandes que dans la fiche, puis un tableau ligne par ligne (prix, surface, terrain, pièces, chambres, DPE, ville, adresse, agence, sources, première vue) qui distingue les valeurs égales des valeurs différentes, et les preuves de la détection (règle, photos communes, distance, texte, veto).
- **Décision en un geste** : « Fusionner », « Pas un doublon », « Passer », au clic ou au clavier (`F`, `N`, `→`/`S`). Le couple suivant s'affiche aussitôt.
- **« Annuler »** pour la dernière décision : elle reste annulable quelques secondes avant d'être enregistrée, car un refus est définitif (seule une fusion manuelle le lève).
- **Périmètre par défaut** : les couples dont au moins un côté est actif et non exclu. Une case affiche aussi les couples entièrement écartés ou disparus.
- **API** : `GET /api/duplicates` (file des couples ramenés aux principales, avec résumés et meilleures preuves) et `POST /api/duplicates/decision` (fusion ou refus d'un couple, refusé avec 409 si le couple a déjà été traité, par exemple par le collecteur entre-temps).
- **Inchangé** : la puce « Doublons à valider », le badge « Doublon ? » des cartes, le bloc de la fiche, « 🔗 Fusionner avec… », « Séparer », les règles de détection et de fusion.
- Hors périmètre : glisser pour décider sur mobile, revue des fusions automatiques déjà faites, choix manuel de l'annonce principale, mémorisation des couples passés d'une visite à l'autre.

## Capabilities

### New Capabilities
- `duplicate-review` : revue rapide des doublons possibles dans un onglet dédié (file de couples, comparaison, décisions au clavier, annulation, couple déjà traité).

### Modified Capabilities
Aucune. Les exigences de `merged-listings` (« Fusion manuelle », « Refus d'un doublon possible », « Filtre des doublons à valider ») et de `listing-deduplication` (« Mémoire des refus ») restent valables telles quelles ; l'onglet en est une nouvelle porte d'entrée.

## Impact

- **API** (`app/web.py`) : deux routes, sans changement de schéma de base. Réutilise `dedupe.merge`, `dedupe.reject`, `principal_of`, `_summary` et `_rescore`.
- **UI** : `app/templates/index.html` (onglet, conteneur, versions `?v=`), `app/static/app.js` (route, file, raccourcis, annulation), `app/static/style.css` (vue de revue, tableau comparatif, mobile).
- **Tests** : nouveau `tests/test_duplicate_review.py` (contrat de l'API) ; l'UI se vérifie dans le navigateur, faute d'outillage de test JavaScript.
- **Agent et collecteur** : aucun changement. La skill `maison-enrichment` peut citer l'onglet là où elle renvoie l'utilisateur vers « Fusionner avec… ».
- **Livraison** : reconstruire l'image et ne recréer que `maison-web` (voir design.md).
