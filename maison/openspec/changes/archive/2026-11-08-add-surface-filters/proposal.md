# Proposal

## Why

La liste des annonces ne se filtre aujourd'hui que par un terrain minimum (« Terrain min (m²) »). Alexandre et Marie ne peuvent ni choisir une surface habitable, ni écarter les très grands terrains (21 annonces dépassent 1 000 m²), alors que ces deux surfaces figurent sur chaque carte (📐 et 🌱). Le 2026-11-08, la liste par défaut compte 929 annonces : 559 ont au moins 100 m² habitables et 188 au moins 300 m² de terrain.

## What Changes

- **Deux filtres en fourchette** dans le panneau des filtres, chacun avec un minimum et un maximum en m² : **Surface habitable** et **Surface totale – terrain**. Une borne vide ne limite rien.
- **« Terrain min (m²) » disparaît** : il devient le minimum de « Surface totale – terrain » et garde le même effet quand il est seul. « Prix max » reste en place.
- **Même valeur que la carte** : le filtre lit la surface que la carte affiche, c'est-à-dire la valeur de l'annonce principale, complétée par les autres sources du bien fusionné, corrigée par l'agent le cas échéant.
- **Surface inconnue** : dès qu'une borne est posée sur une surface, les annonces dont la carte affiche « ? » pour cette surface sont masquées (comportement actuel de « Terrain min »). Sous le filtre, l'UI dit combien d'annonces sont ainsi masquées : 247 annonces (27 %) n'ont pas de terrain connu, contre 1 pour la surface habitable.
- **Comme les autres filtres** : les fourchettes s'appliquent à la liste, à la carte et au compteur d'annonces, se combinent avec les autres filtres, se mettent à jour à la saisie et sont vidées par « Réinitialiser les filtres ».
- **Hypothèse à confirmer** : « surface totale » désigne la surface de la parcelle (« Terrain » sur les cartes), la seule autre surface que Maison enregistre. Une surface bâtie totale, annexes comprises, n'existe dans aucune source et demanderait un champ de plus (change à part).
- Hors périmètre : tri par surface, curseurs ou histogramme, mémorisation des filtres d'une visite à l'autre (aucun filtre n'est mémorisé aujourd'hui), renommage de « Terrain » en « Surface totale » sur les cartes et la fiche, effet sur le score, filtrage côté serveur.

## Capabilities

### New Capabilities
- `listing-surface-filters` : filtres de la liste et de la carte sur la surface habitable et sur la surface totale du terrain (fourchettes, valeur lue, annonces sans surface connue, combinaison avec les autres filtres).

### Modified Capabilities
Aucune. La règle « Champs complétés par les autres sources » de `merged-listings` s'applique telle quelle et aucune exigence existante ne change.

## Impact

- **UI seulement** : `app/templates/index.html` (panneau des filtres, versions `?v=` des deux fichiers statiques), `app/static/app.js` (`filtered()`, compteurs des annonces masquées, rendu), `app/static/style.css` (ligne min/max, mention des annonces masquées).
- **Aucun changement** de base, d'API, de collecteur, de score, de scripts ni de skill : `GET /api/listings` renvoie déjà `surface_m2` et `land_m2`, terrain fusionné compris.
- **Tests** : un test d'API fige ce contrat (`tests/test_listing_surfaces.py`). Le reste se vérifie dans le navigateur, sur une page servie avec des annonces d'essai (sans base ni conteneur), faute d'outillage de test JavaScript dans le projet.
- **Livraison** : les fichiers statiques sont copiés dans l'image. Le 2026-11-08, l'image de `maison-web` porte déjà les UI de `add-listing-comments` et de `improve-adjacency-detection` : ce change est le seul écart entre le code en service et l'arbre (`app/`, `db/`, `requirements.txt`), donc reconstruire l'image puis ne recréer que `maison-web` suffit. Voir design.md, Migration Plan.
