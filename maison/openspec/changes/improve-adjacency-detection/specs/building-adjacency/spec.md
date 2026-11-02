# Spec Delta

## Purpose

Déterminer à partir des données cartographiques publiques (bâtiments, parcelles, liens entre adresse et bâtiment) si la maison d'une annonce à adresse précise partage des murs avec ses voisines, combien, sur quelle longueur et par quel volume, afin d'en tirer une catégorie de mitoyenneté vérifiable.

## ADDED Requirements

### Requirement: Calcul réservé aux adresses précises
Le système SHALL calculer la mitoyenneté de chaque annonce principale active dont la position vient d'une adresse précise : confiance `exact` ou `probable`, ou adresse validée par l'utilisateur. Il SHALL NOT la calculer pour une position au centroïde de la commune, au niveau de la rue ou dans un flou de portail. Il l'indique alors comme non applicable.

#### Scenario: Adresse exacte trouvée par l'agent
- **WHEN** l'agent poste une adresse `exact` pour une annonce
- **THEN** la mitoyenneté de cette annonce est calculée au plus tard au cycle suivant du collecteur

#### Scenario: Position au centroïde communal
- **WHEN** une carte e-mail n'a qu'une position `city`
- **THEN** aucun calcul n'est lancé, et la fiche indique que la mitoyenneté cartographique n'est pas applicable

#### Scenario: Rue connue sans numéro
- **WHEN** l'adresse a la confiance `street`
- **THEN** aucun calcul n'est lancé

### Requirement: Identification du bâtiment de l'adresse
Le système SHALL désigner le bâtiment de l'annonce :
1. par le lien officiel entre l'adresse et le bâtiment quand il existe ;
2. à défaut, par le bâtiment principal de la parcelle qui contient le point de l'adresse ;
3. à défaut, par le bâtiment le plus proche, à moins de 6 m.

Le résultat SHALL indiquer la méthode utilisée. Une méthode de repli SHALL être signalée par un avertissement.

#### Scenario: Lien adresse-bâtiment disponible
- **WHEN** l'adresse de l'annonce est reliée à un bâtiment dans la base adresse-bâtiment
- **THEN** ce bâtiment est retenu, avec la méthode « lien »

#### Scenario: Pas de lien, parcelle connue
- **WHEN** aucun lien n'existe et que le point de l'adresse tombe dans une parcelle bâtie
- **THEN** le plus grand bâtiment en dur de cette parcelle est retenu, avec la méthode « parcelle » et un avertissement de repli

#### Scenario: Aucun bâtiment
- **WHEN** aucun bâtiment n'est trouvé par ces trois méthodes, par exemple pour une construction trop récente
- **THEN** le résultat a le statut « bâtiment introuvable » et ne propose aucune catégorie

### Requirement: La propriété regroupe les bâtiments de sa parcelle
Les bâtiments situés sur la même parcelle que le bâtiment de l'annonce (garage, extension, abri) SHALL être traités comme une partie de la propriété. Leurs contacts entre eux SHALL NOT compter comme de la mitoyenneté.

#### Scenario: Garage propre accolé
- **WHEN** la maison touche seulement son propre garage, sur la même parcelle, et aucun bâtiment voisin
- **THEN** la catégorie calculée est « individuelle »

### Requirement: Détection des murs communs
Un bâtiment situé sur une autre parcelle SHALL être compté comme accolé quand il touche la propriété sur une longueur minimale, 2 m par défaut, avec une tolérance de digitalisation de 0,5 m. Un contact plus court SHALL être ignoré. Les voisins accolés SHALL être comptés par parcelle voisine.

#### Scenario: Maison dans une rangée
- **WHEN** la maison partage 9 m de mur avec la maison de gauche et 8 m avec celle de droite
- **THEN** deux voisins accolés sont retenus, avec leurs longueurs de contact

#### Scenario: Voisin séparé par un passage
- **WHEN** le bâtiment voisin le plus proche est à 2 m
- **THEN** aucun contact n'est retenu avec lui

#### Scenario: Simple angle commun
- **WHEN** la maison et un bâtiment voisin ne se touchent que sur 1 m, à un angle
- **THEN** ce contact est ignoré

#### Scenario: Voisin formé de plusieurs volumes
- **WHEN** la maison voisine et son extension, sur la même parcelle voisine, touchent toutes deux la maison
- **THEN** elles comptent pour un seul voisin

### Requirement: Nature des volumes en contact
Chaque volume en contact SHALL être classé :
- **habitation** : volume principal d'une maison ou d'un autre bâtiment en dur ;
- **annexe** : garage, abri, véranda, construction légère, ou volume petit ou bas.

Un mur habitable commun SHALL relier un volume principal de la propriété à un volume « habitation » voisin. Un contact entre deux annexes SHALL être ignoré.

#### Scenario: Garage du voisin contre la maison
- **WHEN** un garage bas d'une parcelle voisine est accolé au volume principal de la maison
- **THEN** ce contact est compté comme une liaison par annexe, pas comme un mur habitable commun

#### Scenario: Garage propre contre la maison voisine
- **WHEN** le garage de la propriété touche la maison voisine, sans que la maison elle-même la touche
- **THEN** ce contact est compté comme une liaison par annexe

#### Scenario: Abri contre abri
- **WHEN** seul l'abri de jardin de la propriété touche l'abri du voisin
- **THEN** aucun contact n'est retenu

### Requirement: Catégorie calculée
Le système SHALL déduire la catégorie du nombre de parcelles voisines reliées :
- 2 voisins ou plus avec un mur habitable commun → `terraced` ;
- 1 voisin avec un mur habitable commun → `semi` ;
- aucun mur habitable commun, mais au moins une liaison par annexe → `link_detached` ;
- aucun contact → `detached`.

#### Scenario: Mitoyenne des deux côtés
- **WHEN** la maison a un mur habitable commun avec deux parcelles voisines
- **THEN** la catégorie calculée est `terraced`

#### Scenario: Semi avec garage de l'autre côté
- **WHEN** la maison a un mur habitable commun d'un côté et touche de l'autre côté le garage du voisin
- **THEN** la catégorie calculée est `semi`

#### Scenario: Reliée seulement par le garage
- **WHEN** la seule liaison avec un voisin passe par un garage, celui de la propriété ou celui du voisin
- **THEN** la catégorie calculée est `link_detached`

#### Scenario: Maison isolée
- **WHEN** aucun bâtiment voisin ne touche la propriété
- **THEN** la catégorie calculée est `detached`

### Requirement: Avertissements de qualité
Le résultat SHALL porter des avertissements dans les cas suivants : bâtiment de plusieurs logements, emprise anormalement grande, identification par repli, adresse seulement probable. Avec les deux premiers avertissements ou sans bâtiment, la catégorie calculée SHALL NOT servir au type retenu. Elle reste affichée à titre d'information.

#### Scenario: Bâtiment de trois logements
- **WHEN** le bâtiment retenu compte trois logements
- **THEN** le résultat porte l'avertissement « plusieurs logements », et sa catégorie n'est pas utilisée pour le score

#### Scenario: Emprise très grande
- **WHEN** l'emprise du bâtiment retenu dépasse le seuil d'emprise anormale (300 m² par défaut)
- **THEN** le résultat porte l'avertissement « bâtiment très grand », et sa catégorie n'est pas utilisée pour le score

### Requirement: Conservation et recalcul
Le résultat SHALL être conservé avec sa date et les données d'entrée (adresse, position, identifiant d'adresse). Il SHALL être recalculé quand l'adresse ou la position change, et SHALL NOT être recalculé sinon. Un échec de calcul SHALL être réessayé plus tard sans interrompre le cycle du collecteur.

#### Scenario: Adresse corrigée par l'utilisateur
- **WHEN** l'utilisateur corrige l'adresse d'une annonce
- **THEN** l'ancien résultat est remplacé par un nouveau calcul au cycle suivant

#### Scenario: Aucun changement
- **WHEN** un cycle du collecteur passe sur une annonce dont l'adresse et la position n'ont pas changé depuis le dernier calcul
- **THEN** aucune requête cartographique n'est émise pour elle

#### Scenario: Service cartographique indisponible
- **WHEN** le service cartographique ne répond pas
- **THEN** le cycle se poursuit (score, alertes), l'échec est enregistré, et un nouvel essai a lieu au plus tôt 6 heures plus tard

### Requirement: Débit borné par cycle
Chaque cycle du collecteur SHALL calculer au plus un nombre borné d'annonces, 40 par défaut, en commençant par les adresses les plus récemment établies. Le reste SHALL être traité aux cycles suivants.

#### Scenario: Gros arriéré
- **WHEN** 600 annonces attendent un calcul
- **THEN** un cycle en calcule 40, et les autres attendent les cycles suivants

### Requirement: Rattrapage à blanc puis application
Un script de rattrapage SHALL calculer la mitoyenneté de toutes les annonces éligibles et produire un rapport :
- effectifs par catégorie ;
- comparaison avec le type actuel ;
- variations de score ;
- désaccords et avertissements.

Par défaut, il SHALL NOT modifier la base. En mode application, il SHALL enregistrer les résultats et recalculer les scores sans envoyer d'alerte.

#### Scenario: Analyse à blanc
- **WHEN** le script est lancé sans option
- **THEN** aucune ligne d'annonce n'est modifiée, et un rapport autonome est écrit dans le dossier des rapports

#### Scenario: Application
- **WHEN** le script est lancé avec l'option d'application
- **THEN** les résultats sont enregistrés, les scores recalculés, et aucune alerte Discord n'est envoyée

#### Scenario: Relance
- **WHEN** le script est relancé en mode application sans changement d'adresse
- **THEN** seules les annonces dont le calcul avait échoué sont recalculées

### Requirement: Analyse d'un point quelconque pour l'agent
L'API SHALL proposer à l'agent, avec authentification par jeton, l'analyse de mitoyenneté d'une adresse ou d'un point quelconque. Cette analyse SHALL NOT être enregistrée sur une annonce. Elle sert notamment à écarter des adresses candidates lors d'une investigation.

#### Scenario: Adresse candidate testée
- **WHEN** l'agent demande l'analyse d'une adresse candidate
- **THEN** il reçoit la catégorie, les contacts et les avertissements, et aucune annonce n'est modifiée

#### Scenario: Appel sans jeton
- **WHEN** l'analyse est demandée sans jeton valide
- **THEN** l'API répond 401

### Requirement: Vue aérienne annotée
Le système SHALL produire pour une annonce à adresse précise, ou pour un point, une image aérienne récente (orthophotographie IGN) sur laquelle sont tracés, dans des couleurs distinctes et avec une légende :
- la parcelle ;
- le bâtiment retenu ;
- les autres bâtiments de la propriété ;
- les voisins accolés (habitation ou annexe) ;
- les murs communs.

#### Scenario: Annonce mitoyenne
- **WHEN** la vue aérienne d'une annonce `terraced` est demandée
- **THEN** l'image montre le bâtiment de l'annonce et ses deux voisins accolés, avec les murs communs tracés

#### Scenario: Annonce sans adresse précise
- **WHEN** la vue aérienne est demandée pour une annonce au centroïde communal
- **THEN** l'API répond qu'elle n'est pas applicable, sans image

### Requirement: Données ouvertes uniquement
Le calcul et la vue aérienne SHALL utiliser uniquement des services de données ouvertes accessibles sans clé : IGN Géoplateforme et Base Adresse Nationale. Le système SHALL s'identifier par son User-Agent, limiter le parallélisme (4 requêtes simultanées au plus) et SHALL NOT appeler de service Google.

#### Scenario: Requêtes émises pendant un calcul
- **WHEN** une mitoyenneté est calculée
- **THEN** les seules requêtes sortantes visent les services de l'IGN et de la BAN

### Requirement: Échecs visibles dans le contrôle santé
Le contrôle santé quotidien SHALL signaler :
- les échecs répétés du calcul cartographique, plus de 20 % des calculs depuis le contrôle précédent ;
- l'indisponibilité du service cartographique.

#### Scenario: Service IGN en panne depuis la veille
- **WHEN** tous les calculs ont échoué depuis le contrôle précédent
- **THEN** le message du matin contient une ligne d'attention sur la mitoyenneté cartographique, avec le nombre d'échecs
