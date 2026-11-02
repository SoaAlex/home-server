# Spec Delta

## Purpose

Retenir pour chaque bien une seule catégorie de mitoyenneté, explicable, à partir de toutes les sources disponibles (utilisateur, agent, carte, annonce), puis l'utiliser partout de la même façon : score, filtres, alertes, fiche et agent.

## ADDED Requirements

### Requirement: Catégories de mitoyenneté
Le système SHALL utiliser cinq catégories :

| Valeur | Libellé | Définition |
|---|---|---|
| `detached` | Individuelle | aucun mur commun avec un voisin |
| `link_detached` | Accolée par le garage | aucun mur habitable commun, liaison par un garage ou une annexe |
| `semi` | Semi-individuelle | un voisin avec un mur habitable commun |
| `terraced` | Mitoyenne | deux voisins ou plus avec un mur habitable commun |
| `unknown` | Inconnu | aucun verdict |

#### Scenario: Maison reliée par son garage
- **WHEN** la seule liaison entre la maison et le voisin est le garage
- **THEN** la catégorie est « Accolée par le garage » (`link_detached`)

#### Scenario: Mur habitable d'un côté, garage de l'autre
- **WHEN** la maison partage un mur habitable avec un voisin, et que son garage touche un autre voisin
- **THEN** la catégorie est « Semi-individuelle » (`semi`)

### Requirement: Barème du critère « type »
Le critère « type » du score SHALL valoir 12 points pour `detached`, 10 pour `link_detached`, 7 pour `semi`, et 2 pour `terraced` ou `unknown`. Les autres critères du score restent inchangés.

#### Scenario: Semi reconnue comme reliée par le garage
- **WHEN** le type retenu d'une annonce passe de `semi` à `link_detached`
- **THEN** son score augmente de 3 points

### Requirement: Ordre de priorité des sources
Le type retenu SHALL être le premier verdict disponible dans cet ordre :
1. correction de l'utilisateur ;
2. verdict de l'agent accompagné de ses preuves (nouveau protocole) ;
3. carte, avec une adresse exacte ou validée et sans avertissement bloquant ;
4. verdict de l'agent sans preuves (ancien protocole) ;
5. carte, avec une adresse probable ;
6. annonce : champ structuré, puis texte explicite, puis texte faible ;
7. sinon `unknown`.

La valeur `unknown` SHALL NOT compter comme un verdict.

#### Scenario: Carte contre ancien verdict de l'agent
- **WHEN** la carte (adresse exacte) donne `terraced` et que l'ancien verdict de l'agent, sans preuves, donne `detached`
- **THEN** le type retenu est `terraced`, avec la source « carte », et un désaccord est signalé

#### Scenario: Agent qui a vu le garage
- **WHEN** la carte donne `terraced` et que l'agent, avec preuves photo, conclut `link_detached`
- **THEN** le type retenu est `link_detached`, avec la source « agent », et le désaccord avec la carte est signalé

#### Scenario: Correction de l'utilisateur
- **WHEN** l'utilisateur a choisi `semi`, et que la carte comme l'agent disent autre chose
- **THEN** le type retenu reste `semi`

#### Scenario: Carte à adresse probable seule
- **WHEN** seule la carte donne un verdict, pour une adresse `probable`
- **THEN** ce verdict est retenu avec une confiance « moyenne »

### Requirement: Verdicts de toutes les sources d'un bien fusionné
Pour un bien qui regroupe plusieurs annonces, le système SHALL tenir compte des verdicts « annonce » et « agent » des annonces secondaires quand l'annonce principale n'en a pas. Une valeur `unknown` de l'annonce principale SHALL NOT masquer le verdict d'une secondaire.

#### Scenario: Carte SeLoger principale, fiche Netty secondaire
- **WHEN** l'annonce principale n'a aucun type et que sa secondaire Netty déclare « semi-individuelle »
- **THEN** le type retenu est `semi`, avec la source « annonce »

### Requirement: Désaccords signalés
Le système SHALL signaler un désaccord quand le type retenu diffère d'un autre verdict disponible parmi :
- l'agent ;
- la carte (adresse exacte ou probable) ;
- le texte explicite de l'annonce.

Un texte faible SHALL NOT créer de désaccord à lui seul.

#### Scenario: Carte et agent en désaccord
- **WHEN** la carte donne `terraced` et l'agent `semi`
- **THEN** la fiche affiche un badge « sources en désaccord » avec les deux verdicts

#### Scenario: Texte faible contraire
- **WHEN** la carte donne `semi` et que le texte dit seulement « maison de ville »
- **THEN** aucun badge de désaccord n'est affiché

### Requirement: Explication du type retenu
L'API SHALL fournir pour chaque annonce :
- le type retenu, sa source et sa confiance (haute, moyenne ou basse) ;
- l'indicateur de désaccord ;
- le verdict de chaque source, avec une preuve courte : côtés et longueurs de mur pour la carte, côtés et photos pour l'agent, expression trouvée pour le texte.

La liste des annonces SHALL fournir ces champs sans les géométries.

#### Scenario: Fiche d'une maison mitoyenne
- **WHEN** l'utilisateur ouvre la fiche d'une annonce dont la carte a trouvé deux murs communs de 9 m et 8 m, alors que le texte dit « individuelle »
- **THEN** la fiche affiche « Mitoyenne · carte (2 murs : 9 m, 8 m) » et le verdict contraire de l'annonce

### Requirement: Correction manuelle du type
L'utilisateur SHALL pouvoir fixer le type depuis la fiche, parmi les cinq catégories, ou rendre le choix automatique. Une correction SHALL être retenue avant toute autre source, recalculer immédiatement le score et rester protégée contre les envois ultérieurs de l'agent.

#### Scenario: Correction
- **WHEN** l'utilisateur choisit « Accolée par le garage »
- **THEN** le type retenu et le score sont mis à jour dès la réponse de l'API

#### Scenario: Envoi ultérieur de l'agent
- **WHEN** l'agent poste ensuite un autre `property_type` pour cette annonce
- **THEN** le verdict de l'agent est conservé comme preuve, mais le type retenu reste celui de l'utilisateur

#### Scenario: Retour à l'automatique
- **WHEN** l'utilisateur rend le choix automatique
- **THEN** le type retenu redevient celui que donne l'ordre de priorité

### Requirement: Détection du type dans le texte
Le système SHALL déduire un verdict « annonce » du titre et de la description, avec une force « explicite » ou « faible » :
- liaison par garage → `link_detached` ;
- semi-mitoyenne ou mitoyenne d'un côté → `semi` ;
- mitoyenne des deux côtés ou en bande → `terraced` (explicite) ;
- individuelle, non mitoyenne ou 4 façades → `detached` ;
- « mitoyenne » seul ou « maison de ville » → `terraced` (faible).

« Indépendante » seul SHALL NOT valoir `detached`.

#### Scenario: Cuisine indépendante
- **WHEN** la description contient « cuisine indépendante » et aucune autre indication de type
- **THEN** le verdict « annonce » est absent (`unknown`)

#### Scenario: Mitoyenne par le garage
- **WHEN** la description contient « maison mitoyenne par le garage »
- **THEN** le verdict « annonce » est `link_detached` (explicite)

#### Scenario: Mitoyenne d'un seul côté
- **WHEN** la description contient « maison individuelle mitoyenne d'un seul côté »
- **THEN** le verdict « annonce » est `semi`

#### Scenario: Quatre façades
- **WHEN** la description contient « maison 4 façades »
- **THEN** le verdict « annonce » est `detached` (explicite)

#### Scenario: Non mitoyenne
- **WHEN** la description contient « maison non mitoyenne »
- **THEN** le verdict « annonce » est `detached`, et non `terraced`

### Requirement: Type déclaré par les sources structurées
Quand une source fournit un type structuré, par exemple le type de maison des sites Netty, le système SHALL le conserver séparément du texte. Ce type SHALL primer sur le texte de la même annonce.

#### Scenario: Fiche Netty déclarée mitoyenne
- **WHEN** une fiche Netty déclare le type « mitoyenne » et que son texte dit « cuisine indépendante »
- **THEN** le verdict « annonce » est `terraced`, avec la provenance « champ structuré »

### Requirement: Recalcul des verdicts « annonce » existants
Le rattrapage SHALL recalculer une fois, avec les nouvelles règles, le verdict « annonce » des annonces que leurs sources ne renvoient plus, comme les cartes e-mail anciennes. Les scores concernés SHALL être recalculés sans alerte.

#### Scenario: Ancienne carte « cuisine indépendante »
- **WHEN** le rattrapage traite une carte e-mail dont le seul indice était « cuisine indépendante »
- **THEN** son verdict « annonce » passe d'individuelle à inconnu, et son score est recalculé sans alerte Discord

### Requirement: Même type partout
Le type retenu SHALL être le seul utilisé par :
- le score ;
- les filtres de la liste ;
- la fiche ;
- l'alerte Discord, qui SHALL afficher le type et sa source ;
- les données transmises à l'agent.

#### Scenario: Alerte d'un bien accolé par le garage
- **WHEN** une alerte Discord part pour un bien de type retenu `link_detached` venant de la carte
- **THEN** elle affiche « Type : Accolée par le garage (carte) »

#### Scenario: Données transmises à l'agent
- **WHEN** l'agent lit la file d'enrichissement
- **THEN** chaque annonce porte son type retenu, la source de ce type et le résumé du calcul cartographique

### Requirement: Filtre par type
Le filtre de type de la liste SHALL proposer :
- Tous ;
- Individuelle ;
- Par garage (`link_detached`) ;
- Semi ;
- Autre (mitoyenne et inconnue).

Il porte sur le type retenu.

#### Scenario: Filtre « Par garage »
- **WHEN** l'utilisateur choisit « Par garage »
- **THEN** seules les annonces de type retenu `link_detached` restent affichées
