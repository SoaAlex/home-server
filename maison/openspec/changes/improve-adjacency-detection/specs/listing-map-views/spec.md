# Spec Delta

## Purpose

Montrer dans la fiche la maison et ses voisines vues du ciel, sur le cadastre et depuis la rue, pour vérifier la mitoyenneté d'un coup d'œil, avec en plus des liens vers Google Maps pour un contrôle manuel.

## ADDED Requirements

### Requirement: Fonds de la mini-carte
La mini-carte de la fiche SHALL proposer deux fonds, « Plan » et « Photo aérienne » (orthophotographie IGN), ainsi qu'une surcouche cadastrale que l'on peut activer ou masquer. Le dernier choix SHALL être mémorisé dans le navigateur d'une fiche à l'autre.

#### Scenario: Passage en photo aérienne
- **WHEN** l'utilisateur choisit « Photo aérienne »
- **THEN** la mini-carte affiche l'orthophotographie IGN autour de l'annonce

#### Scenario: Choix mémorisé
- **WHEN** l'utilisateur ouvre une autre fiche après avoir choisi « Photo aérienne » et activé le cadastre
- **THEN** la nouvelle mini-carte s'ouvre avec la photo aérienne et le cadastre

### Requirement: Bâtiments et murs communs sur la mini-carte
Quand un calcul cartographique existe, la mini-carte SHALL tracer, avec une légende :
- le bâtiment de l'annonce ;
- les autres bâtiments de la propriété ;
- les voisins accolés, en distinguant habitation et annexe ;
- les murs communs.

#### Scenario: Maison mitoyenne
- **WHEN** la fiche d'une annonce `terraced` est ouverte
- **THEN** la mini-carte montre le bâtiment de l'annonce, ses deux voisins accolés et les murs communs en trait épais

#### Scenario: Pas de calcul
- **WHEN** l'annonce n'a pas de calcul cartographique
- **THEN** aucun bâtiment n'est tracé, et la fiche explique pourquoi (adresse imprécise, calcul en attente ou échec)

### Requirement: Vue de rue dans la fiche
La fiche SHALL afficher, quand elle existe, la vue de rue orientée vers la maison, avec sa date, sa distance, la mention de son auteur et de sa licence, et un lien vers la visionneuse Panoramax. La vue SHALL être demandée seulement à l'ouverture de la fiche, puis servie depuis le cache.

#### Scenario: Fiche avec vue de rue
- **WHEN** l'utilisateur ouvre la fiche d'une annonce à adresse exacte couverte par Panoramax
- **THEN** la vue de rue s'affiche avec sa date et la mention de l'auteur

#### Scenario: Liste des annonces
- **WHEN** la liste des annonces est chargée
- **THEN** aucune vue de rue n'est demandée

### Requirement: Liens manuels vers Google Maps
La fiche SHALL proposer deux liens qui ouvrent Google Maps dans un nouvel onglet, à la position de l'annonce : vue satellite et Street View. L'application SHALL NOT charger elle-même de contenu Google : ni tuiles, ni images, ni script.

#### Scenario: Clic sur « Street View »
- **WHEN** l'utilisateur clique sur « Street View »
- **THEN** un nouvel onglet ouvre Google Maps en Street View à la position de l'annonce

#### Scenario: Affichage de la fiche
- **WHEN** la fiche s'affiche
- **THEN** aucune requête n'est envoyée à un domaine Google

### Requirement: Position imprécise signalée
Pour une position imprécise (centroïde communal, rue, flou), les vues et les liens SHALL rester centrés sur cette position. La fiche SHALL alors indiquer clairement que la maison n'est pas identifiée, et aucun bâtiment ne SHALL être mis en évidence.

#### Scenario: Carte e-mail au centroïde communal
- **WHEN** la fiche d'une annonce `city` est ouverte en photo aérienne
- **THEN** la carte est centrée sur la commune, aucun bâtiment n'est mis en évidence, et la mention « maison non localisée » est visible

### Requirement: Mentions de source
Les fonds de carte, la surcouche cadastrale et les images SHALL afficher leur mention de source : « © IGN » pour l'orthophotographie et le cadastre, auteur et licence pour Panoramax.

#### Scenario: Photo aérienne affichée
- **WHEN** le fond « Photo aérienne » est actif
- **THEN** la mention « © IGN » est visible sur la mini-carte
