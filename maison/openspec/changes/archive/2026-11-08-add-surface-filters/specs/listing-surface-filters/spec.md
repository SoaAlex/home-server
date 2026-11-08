# Spec Delta

## Purpose

Permettre de borner la liste et la carte des annonces par la surface habitable et par la surface totale du terrain, avec un minimum et un maximum pour chacune, comme les autres filtres du panneau.

## ADDED Requirements

### Requirement: Filtre de surface habitable
Le panneau des filtres SHALL proposer un filtre « Surface habitable (m²) » avec un champ minimum et un champ maximum. Une annonce passe le filtre quand sa surface habitable est comprise entre les deux bornes, bornes incluses. Un champ vide n'impose aucune limite.

#### Scenario: Minimum seul
- **WHEN** l'utilisateur saisit 100 en minimum et laisse le maximum vide
- **THEN** seules les annonces de 100 m² habitables ou plus restent affichées

#### Scenario: Maximum seul
- **WHEN** l'utilisateur saisit 130 en maximum et laisse le minimum vide
- **THEN** seules les annonces de 130 m² habitables ou moins restent affichées

#### Scenario: Fourchette, bornes incluses
- **WHEN** l'utilisateur saisit 100 en minimum et 130 en maximum
- **THEN** les annonces de 100 m² et de 130 m² restent affichées, celles de 99 m² et de 131 m² sont masquées

### Requirement: Filtre de surface totale du terrain
Le panneau des filtres SHALL proposer un filtre « Surface totale – terrain (m²) » sur la surface de la parcelle, avec un minimum et un maximum et les mêmes règles que le filtre de surface habitable. Il SHALL remplacer le champ « Terrain min (m²) », qui n'existe plus.

#### Scenario: Terrain minimum
- **WHEN** l'utilisateur saisit 300 en minimum
- **THEN** une annonce de 250 m² de terrain est masquée et une annonce de 300 m² reste affichée, comme avec l'ancien champ « Terrain min (m²) »

#### Scenario: Terrain maximum
- **WHEN** l'utilisateur saisit 1 000 en maximum
- **THEN** une annonce de 1 500 m² de terrain est masquée et une annonce de 800 m² reste affichée

#### Scenario: Ancien champ retiré
- **WHEN** l'utilisateur ouvre le panneau des filtres
- **THEN** il n'y trouve plus de champ « Terrain min (m²) », et « Prix max (k€) » est toujours présent

### Requirement: Surface lue comme sur la carte
Chaque filtre SHALL porter sur la surface que la carte du bien affiche : la valeur de l'annonce principale, complétée par les autres sources du bien fusionné quand elle est vide, et remplacée par la correction de l'agent quand il y en a une.

#### Scenario: Terrain connu par une source fusionnée
- **WHEN** l'annonce principale d'un bien n'a pas de terrain, qu'une carte SeLoger fusionnée indique 367 m², et que l'utilisateur saisit 300 en minimum
- **THEN** le bien reste affiché, et sa carte montre 367 m²

#### Scenario: Surface corrigée par l'agent
- **WHEN** l'agent a corrigé la surface habitable d'un bien de 100 m² à 120 m² et que l'utilisateur saisit 110 en minimum
- **THEN** le bien reste affiché

### Requirement: Annonces sans surface connue
Une surface est inconnue quand la carte affiche « ? » à sa place. Tant qu'une borne est posée sur une surface, les annonces dont cette surface est inconnue SHALL être masquées. Sans borne sur cette surface, elles SHALL rester affichées.

#### Scenario: Terrain inconnu avec un minimum
- **WHEN** l'utilisateur saisit 300 en minimum de terrain et qu'une annonce n'a pas de terrain connu
- **THEN** cette annonce est masquée

#### Scenario: Aucune borne
- **WHEN** les deux champs du terrain sont vides
- **THEN** une annonce sans terrain connu reste affichée

#### Scenario: Surface à zéro
- **WHEN** une annonce indique 0 m² de terrain et que l'utilisateur saisit 500 en maximum
- **THEN** cette annonce reste affichée, car 0 m² est une valeur connue

### Requirement: Mention des annonces masquées faute de surface
Sous un filtre dont une borne est posée, l'UI SHALL indiquer combien d'annonces sont masquées parce que cette surface est inconnue, parmi celles que les autres critères laissent passer. Sans borne, ou quand ce nombre est nul, l'UI SHALL n'afficher aucune mention.

#### Scenario: Plusieurs annonces masquées
- **WHEN** l'utilisateur saisit 300 en minimum de terrain et que 12 annonces sans terrain connu passent tous les autres critères
- **THEN** la mention « 12 annonces sans terrain connu sont masquées » apparaît sous le filtre du terrain

#### Scenario: Une seule annonce masquée
- **WHEN** l'utilisateur saisit 100 en minimum de surface habitable et qu'une seule annonce sans surface habitable connue passe les autres critères
- **THEN** la mention est « 1 annonce sans surface habitable connue est masquée »

#### Scenario: Annonce déjà écartée par un autre critère
- **WHEN** une annonce sans terrain connu dépasse aussi le prix maximum saisi
- **THEN** elle n'est pas comptée dans la mention du terrain

#### Scenario: Bornes vidées
- **WHEN** l'utilisateur vide les deux champs du terrain
- **THEN** la mention disparaît et les annonces sans terrain connu réapparaissent dans la liste

### Requirement: Saisie des bornes
Les champs SHALL accepter des nombres positifs, décimaux compris, en m², avec le séparateur décimal de la langue du navigateur. Un champ vide, nul ou non numérique SHALL n'imposer aucune limite. Quand le minimum dépasse le maximum, aucune annonce SHALL passer.

#### Scenario: Valeur décimale
- **WHEN** l'utilisateur, dans un navigateur en français, saisit 99,5 en minimum de surface habitable
- **THEN** une annonce de 99,5 m² reste affichée et une annonce de 99 m² est masquée

#### Scenario: Zéro
- **WHEN** l'utilisateur saisit 0 en maximum de terrain
- **THEN** aucune limite n'est appliquée au terrain

#### Scenario: Minimum supérieur au maximum
- **WHEN** l'utilisateur saisit 200 en minimum et 100 en maximum
- **THEN** la liste affiche « Aucune annonce ne correspond aux filtres. » et le compteur d'annonces indique 0

### Requirement: Portée et réinitialisation des fourchettes
Les fourchettes SHALL s'appliquer à la liste, à la carte et au compteur d'annonces, se combiner avec tous les autres filtres, et se mettre à jour à chaque modification d'un champ sans recharger la page ni appeler l'API. « Réinitialiser les filtres » SHALL vider les quatre champs.

#### Scenario: Combinaison avec un filtre rapide
- **WHEN** l'utilisateur saisit une fourchette de 100 à 130 m² habitables puis active « Garage ✓ »
- **THEN** seules les annonces qui respectent la fourchette et ont un garage confirmé restent affichées

#### Scenario: Carte
- **WHEN** l'utilisateur ouvre l'onglet « Carte » avec une fourchette posée
- **THEN** seuls les marqueurs des annonces qui respectent la fourchette sont affichés, et le compteur d'annonces correspond à leur nombre

#### Scenario: Saisie en direct
- **WHEN** l'utilisateur modifie une borne
- **THEN** la liste et le compteur se mettent à jour sans requête vers le serveur

#### Scenario: Réinitialisation
- **WHEN** l'utilisateur clique sur « Réinitialiser les filtres » alors que les quatre champs sont remplis
- **THEN** les quatre champs sont vides, aucune mention d'annonces masquées ne reste affichée, et la liste revient à ses annonces par défaut
