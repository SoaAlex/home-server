# listing-comments Specification

## Purpose

Permettre aux occupants du foyer d'écrire, de relire et de retrouver leurs remarques sur chaque bien, dans un fil de commentaires datés et signés que l'agent Hermes peut aussi lire comme indices.

## Requirements

### Requirement: Fil de commentaires d'un bien
La fiche d'un bien SHALL afficher un bloc « Commentaires » qui liste ses commentaires du plus ancien au plus récent. Chaque commentaire SHALL montrer son auteur, sa date et son heure de création, et son texte avec ses retours à la ligne. Un bien sans commentaire SHALL afficher un fil vide et le formulaire d'ajout.

#### Scenario: Fiche avec deux commentaires
- **WHEN** Marie a commenté un bien le 5 novembre, puis Alexandre le 6 novembre
- **THEN** la fiche montre le commentaire de Marie puis celui d'Alexandre, chacun avec son auteur, sa date et son heure

#### Scenario: Texte sur plusieurs lignes
- **WHEN** un commentaire contient trois lignes
- **THEN** la fiche l'affiche sur trois lignes

### Requirement: Ajout d'un commentaire
L'utilisateur SHALL pouvoir ajouter un commentaire depuis la fiche. Le texte, débarrassé des espaces en début et en fin, SHALL contenir de 1 à 2 000 caractères. Le commentaire SHALL apparaître dans le fil sans recharger la page ni perdre la position du carrousel de photos. L'ajout d'un commentaire SHALL NOT modifier le score, la réaction, le statut « à visiter » ni l'état d'enrichissement du bien.

#### Scenario: Ajout réussi
- **WHEN** l'utilisateur écrit « Visite samedi 10 h » et clique sur « Publier »
- **THEN** le commentaire apparaît en fin de fil avec l'auteur choisi et l'heure actuelle, et le champ de saisie se vide

#### Scenario: Texte vide
- **WHEN** l'utilisateur publie un texte composé uniquement d'espaces
- **THEN** l'API répond 400 et aucun commentaire n'est créé

#### Scenario: Texte trop long
- **WHEN** l'API reçoit un texte de 2 001 caractères
- **THEN** elle répond 400 et aucun commentaire n'est créé

#### Scenario: Annonce inconnue
- **WHEN** l'API reçoit un commentaire pour un identifiant d'annonce qui n'existe pas
- **THEN** elle répond 404

### Requirement: Auteur d'un commentaire
Chaque commentaire SHALL porter un auteur choisi dans la liste configurée `comment_authors` de `config/criteria.yaml`. L'API SHALL refuser par une erreur 400 un auteur absent de cette liste. Si la liste est vide ou absente, l'API SHALL accepter un commentaire sans auteur et l'UI SHALL masquer le choix de l'auteur.

#### Scenario: Auteur inconnu
- **WHEN** l'API reçoit un commentaire signé « Paul » alors que la liste vaut Alexandre et Marie
- **THEN** elle répond 400 et aucun commentaire n'est créé

#### Scenario: Aucun auteur configuré
- **WHEN** `comment_authors` est absent de la configuration
- **THEN** le formulaire n'affiche pas de choix d'auteur, et le commentaire est enregistré sans auteur

### Requirement: Auteur mémorisé dans le navigateur
L'UI SHALL mémoriser dans le navigateur le dernier auteur choisi et le présélectionner dans le formulaire suivant. Tant qu'aucun auteur n'est choisi dans ce navigateur, l'UI SHALL demander de le choisir avant la première publication.

#### Scenario: Navigateur de Marie
- **WHEN** Marie a publié un commentaire en choisissant « Marie », puis ouvre une autre fiche dans le même navigateur
- **THEN** « Marie » est déjà sélectionné

#### Scenario: Premier commentaire dans un navigateur
- **WHEN** l'utilisateur publie pour la première fois dans un navigateur sans auteur mémorisé
- **THEN** l'UI demande de choisir l'auteur et ne publie qu'après ce choix

### Requirement: Modification d'un commentaire
L'utilisateur SHALL pouvoir modifier le texte d'un commentaire existant, avec les mêmes règles de longueur qu'à l'ajout. L'auteur et la date de création SHALL rester inchangés. Un commentaire modifié SHALL afficher la mention « modifié » avec la date de sa dernière modification.

#### Scenario: Correction d'un commentaire
- **WHEN** l'utilisateur modifie « Visite samedi 10 h » en « Visite samedi 11 h »
- **THEN** le fil montre « Visite samedi 11 h » à la même place, avec le même auteur, la même date de création et la mention « modifié »

#### Scenario: Commentaire inconnu
- **WHEN** l'API reçoit une modification pour un identifiant de commentaire qui n'existe pas
- **THEN** elle répond 404

### Requirement: Suppression d'un commentaire
L'utilisateur SHALL pouvoir supprimer un commentaire après une confirmation. La suppression SHALL être définitive.

#### Scenario: Suppression confirmée
- **WHEN** l'utilisateur clique sur la corbeille d'un commentaire et confirme
- **THEN** le commentaire disparaît du fil et de l'API

#### Scenario: Suppression annulée
- **WHEN** l'utilisateur clique sur la corbeille puis annule la confirmation
- **THEN** le commentaire reste en place

### Requirement: Texte affiché sans interprétation
L'UI SHALL afficher le texte des commentaires comme du texte brut : une balise HTML ou un script contenu dans un commentaire SHALL NOT être interprété.

#### Scenario: Balise dans un commentaire
- **WHEN** un commentaire contient `<img src=x onerror=alert(1)>`
- **THEN** la fiche affiche ces caractères tels quels et aucun script ne s'exécute

### Requirement: Badge des commentaires dans la liste
La carte d'un bien commenté SHALL porter un badge « 💬 N », N étant le nombre de commentaires du bien. Une carte sans commentaire SHALL NOT porter ce badge. Le badge SHALL être mis à jour après un ajout ou une suppression, sans recharger la page.

#### Scenario: Bien commenté deux fois
- **WHEN** un bien a deux commentaires
- **THEN** sa carte affiche « 💬 2 »

#### Scenario: Premier commentaire
- **WHEN** l'utilisateur ajoute le premier commentaire d'un bien puis ferme la fiche
- **THEN** la carte du bien affiche « 💬 1 »

### Requirement: Filtre des annonces commentées
L'UI SHALL proposer un filtre rapide « 💬 Commentées » qui n'affiche que les biens ayant au moins un commentaire. Il se combine aux autres filtres comme les filtres rapides existants.

#### Scenario: Filtre activé
- **WHEN** l'utilisateur active « 💬 Commentées »
- **THEN** seuls les biens ayant au moins un commentaire restent dans la liste et sur la carte

### Requirement: Recherche dans les commentaires
La recherche de la barre du haut SHALL porter aussi sur le texte des commentaires, avec la même normalisation que les autres champs (sans accents ni casse).

#### Scenario: Mot présent seulement dans un commentaire
- **WHEN** l'utilisateur cherche « humidite » et seul un commentaire d'un bien contient « Humidité au sous-sol »
- **THEN** ce bien apparaît dans les résultats

### Requirement: Lecture des commentaires par l'agent
Les files de l'agent (enrichissement en attente et vérifications de type) ainsi que la fiche détaillée de l'API SHALL exposer les commentaires du bien, avec leur auteur, leur texte et leur date. Les copies allégées de `maison pending` SHALL les conserver sous la clé `user_comments`.

#### Scenario: Annonce en attente commentée
- **WHEN** l'agent lance `maison pending --standard 5` et qu'une des annonces porte le commentaire « La maison est au bout de l'impasse »
- **THEN** l'entrée de cette annonce contient `user_comments` avec ce texte, son auteur et sa date

#### Scenario: Annonce sans commentaire
- **WHEN** une annonce de la file n'a aucun commentaire
- **THEN** son entrée contient `user_comments` sous forme de liste vide

### Requirement: Aucune écriture de commentaire par l'agent
L'API SHALL refuser par une erreur 403 tout ajout, modification ou suppression de commentaire envoyé avec un en-tête `X-Token`, quel que soit le jeton. Les commentaires SHALL NOT être modifiés par l'enregistrement d'un enrichissement.

#### Scenario: Requête avec le jeton de l'agent
- **WHEN** une requête d'ajout de commentaire porte l'en-tête `X-Token`
- **THEN** l'API répond 403 et aucun commentaire n'est créé

#### Scenario: Enrichissement posté
- **WHEN** l'agent poste l'enrichissement d'un bien commenté
- **THEN** les commentaires du bien sont inchangés
