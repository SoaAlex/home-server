# Spec Delta

## Purpose

Faire vérifier la mitoyenneté par l'agent Hermes à partir d'images ouvertes : photos de l'annonce, vue de rue, vue aérienne. La vérification suit un protocole reproductible, sa sortie est contrôlée par l'API, et elle couvre les nouvelles annonces comme les annonces avec garage déjà en base.

## ADDED Requirements

### Requirement: Planche de photos numérotées
L'API SHALL fournir à l'agent, avec authentification par jeton, une planche unique qui réunit au plus 12 photos locales de l'annonce, numérotées dans l'ordre de l'annonce. Elle SHALL être lisible en un seul appel de vision, soit 960 px de large au plus. Pour un bien fusionné, la planche SHALL utiliser les photos du bien, sans doublon visuel.

#### Scenario: Annonce avec dix photos
- **WHEN** l'agent demande la planche d'une annonce qui a 10 photos locales
- **THEN** il reçoit une image où les photos sont numérotées de 0 à 9

#### Scenario: Annonce sans photo locale
- **WHEN** l'annonce n'a aucune photo locale
- **THEN** l'API répond 404 avec un message explicite

### Requirement: Vue de rue orientée vers la maison
Le système SHALL fournir une vue de rue tirée de la photo ouverte (Panoramax) la plus proche, à moins de 30 m de la maison : photo 360° ou photo plane orientée vers elle. La vue SHALL être recadrée vers le bâtiment de l'annonce et accompagnée de sa date, de sa distance, de son auteur, de sa licence et d'un lien vers la visionneuse.

#### Scenario: Photo 360° à 7 m
- **WHEN** une photo 360° prise à 7 m existe pour une annonce à adresse exacte
- **THEN** la vue renvoyée montre la façade visée et ses voisines immédiates, avec la date et la mention de l'auteur

#### Scenario: Aucune photo proche
- **WHEN** aucune photo ne se trouve à moins de 30 m, ou qu'aucune n'est orientée vers la maison
- **THEN** la réponse indique « aucune vue de rue », sans image

#### Scenario: Seconde demande
- **WHEN** la même vue est demandée une seconde fois pour la même position
- **THEN** elle est servie depuis le cache local, sans nouveau téléchargement

### Requirement: Protocole de vérification par côté
La procédure de l'agent SHALL :
1. repérer les vues extérieures sur la planche ;
2. qualifier chaque côté de la maison (gauche, droite) en habitation, garage, annexe, libre ou inconnu, à partir des vues extérieures, puis de la vue de rue si aucune façade n'est visible ;
3. confronter cette lecture au calcul cartographique et à la vue aérienne.

Elle SHALL conclure seulement si les deux côtés sont qualifiés par les images ou par la carte.

#### Scenario: Pas de photo de façade, vue de rue disponible
- **WHEN** les photos de l'annonce ne montrent que l'intérieur et le jardin, et qu'une vue de rue existe
- **THEN** l'agent qualifie les côtés sur la vue de rue et l'indique dans ses preuves

#### Scenario: Un seul côté visible, pas de carte
- **WHEN** seules les photos montrent le côté droit libre, et qu'aucun calcul cartographique n'existe
- **THEN** l'agent n'envoie pas de `property_type` et précise dans ses notes le côté resté inconnu

#### Scenario: Garage vu sur la photo, carte « mitoyenne »
- **WHEN** la carte donne deux voisins accolés et que la façade montre que l'un d'eux ne touche que le garage
- **THEN** l'agent conclut `semi`, avec les preuves des deux côtés

### Requirement: Format de sortie contrôlé
L'API d'enrichissement SHALL accepter :
- `property_type` parmi `detached`, `link_detached`, `semi` et `terraced` ;
- `type_evidence`, avec les champs suivants :
  - `left` et `right` parmi `house`, `garage`, `annex`, `free` et `unknown` ;
  - `seen_on` parmi `listing_photos`, `street_view`, `aerial`, `map` et `text` ;
  - `photos` : liste d'indices ;
  - `notes` : 300 caractères au plus.

Toute autre valeur SHALL être refusée avec une erreur 422 explicite, et rien ne SHALL être enregistré.

#### Scenario: Prose au lieu d'une valeur
- **WHEN** l'agent envoie `property_type: "Maison mitoyenne"`
- **THEN** l'API répond 422 avec la liste des valeurs acceptées, et l'enrichissement n'est pas modifié

#### Scenario: Sortie valide
- **WHEN** l'agent envoie `property_type: "semi"` avec des preuves valides
- **THEN** l'enrichissement est enregistré et le type retenu est recalculé

### Requirement: Cohérence entre type et côtés
Quand les deux côtés de `type_evidence` sont qualifiés, c'est-à-dire différents de `unknown`, l'API SHALL refuser par une erreur 422 un `property_type` incompatible avec eux :
- deux côtés `house` → `terraced` ;
- un seul côté `house` → `semi` ;
- seulement `garage` ou `annex`, et `free` → `link_detached` ;
- deux côtés `free` → `detached`.

#### Scenario: Mitoyenne avec un côté libre
- **WHEN** l'agent envoie `terraced` avec `left: house` et `right: free`
- **THEN** l'API répond 422 en indiquant que les côtés correspondent à `semi`

#### Scenario: Un côté inconnu
- **WHEN** l'agent envoie `semi` avec `left: house` et `right: unknown`
- **THEN** l'envoi est accepté

### Requirement: File de vérification du type
Le système SHALL tenir une file de demandes de vérification du type, distincte de la file d'enrichissement standard et des investigations d'adresse. La file SHALL être servie par ordre de score décroissant, puis de demande la plus ancienne. Elle SHALL NOT changer le statut d'enrichissement de l'annonce.

#### Scenario: Demande en file
- **WHEN** une demande de vérification est créée pour une annonce déjà enrichie
- **THEN** l'annonce apparaît dans la file des vérifications de type, n'apparaît pas dans la file standard, et garde son statut `done`

#### Scenario: Verdict posté
- **WHEN** l'agent poste pour cette annonce un verdict accompagné de preuves
- **THEN** la demande est retirée et ajoutée à l'historique des vérifications de l'annonce (date, verdict, notes)

#### Scenario: Enrichissement standard d'une annonce en file
- **WHEN** l'enrichissement standard d'une annonce poste un verdict avec preuves alors qu'une demande de vérification est en attente
- **THEN** la demande est considérée comme traitée

### Requirement: Périmètre de la re-vérification
À la mise en service, le système SHALL créer une demande de vérification pour chaque annonce principale active, non exclue, dont le garage est confirmé. Les nouvelles annonces SHALL être vérifiées par l'enrichissement standard. Par la suite, un nouveau désaccord sur une annonce avec garage confirmé SHALL créer une seule demande par calcul cartographique.

#### Scenario: Mise en service
- **WHEN** le rattrapage est appliqué avec l'option de demandes
- **THEN** une demande est créée pour chaque annonce active non exclue avec garage confirmé (environ 410 le 2026-11-01), et aucune pour les autres

#### Scenario: Nouvelle annonce
- **WHEN** une nouvelle annonce est enrichie par le job standard
- **THEN** le protocole de vérification par côté est appliqué dans le même passage, sans demande séparée

#### Scenario: Désaccord apparu après un calcul
- **WHEN** un calcul cartographique contredit le verdict de l'agent sur une annonce avec garage confirmé
- **THEN** une demande est créée une fois, et le même calcul n'en crée pas d'autre

### Requirement: Demande manuelle depuis la fiche
L'utilisateur SHALL pouvoir demander une vérification du type depuis la fiche. La fiche SHALL afficher qu'une vérification est en attente.

#### Scenario: Clic sur « Faire vérifier »
- **WHEN** l'utilisateur clique sur « Faire vérifier le type »
- **THEN** une demande est créée et la fiche affiche « vérification demandée »

### Requirement: Débit du job de vérification
Le job Hermes de vérification SHALL traiter au plus un nombre borné d'annonces par passage, 8 par défaut. L'arriéré initial SHALL ainsi s'écouler en quelques jours, sans retarder l'enrichissement standard ni les investigations d'adresse.

#### Scenario: Arriéré initial
- **WHEN** 410 demandes sont en attente et que le job passe une fois par heure de 8 h à 22 h
- **THEN** chaque passage traite au plus 8 demandes, et la file se vide en 4 jours environ

### Requirement: Aucune image Google
La procédure de l'agent SHALL NOT télécharger, recadrer ni analyser d'image Google Maps ou Street View. Elle n'utilise que les photos de l'annonce ou de ses miroirs, les vues Panoramax et les images de l'IGN.

#### Scenario: Pas de vue de rue ouverte
- **WHEN** aucune vue Panoramax n'existe et que les photos ne montrent pas la façade
- **THEN** l'agent ne se rabat pas sur Google : il conclut à partir de la carte, ou n'envoie pas de `property_type`

### Requirement: Suivi de la file dans le contrôle santé
Le contrôle santé quotidien SHALL indiquer :
- la taille et l'ancienneté de la file des vérifications de type ;
- l'état du job de vérification : en pause, tirs en échec, réponses vides.

Une demande de vérification en attente SHALL NOT être comptée comme un enrichissement bloqué.

#### Scenario: Arriéré en cours d'écoulement
- **WHEN** 300 demandes attendent et que le job tourne normalement
- **THEN** le message du matin donne la taille de la file, sans alerte « enrichissement bloqué »

#### Scenario: Job en pause
- **WHEN** le job de vérification est en pause alors que des demandes attendent
- **THEN** le message du matin contient une alerte sur ce job
