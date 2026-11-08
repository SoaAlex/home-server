# merged-listings Specification

## Purpose

Présenter un bien publié par plusieurs sources comme une seule annonce, qui conserve le contenu de chaque source (liens, prix, photos, champs) et les choix de l'utilisateur. Le score, les alertes et l'enrichissement s'appliquent une seule fois par bien, et l'utilisateur peut fusionner ou séparer à la main.

## Requirements

### Requirement: Une annonce principale par bien
Chaque groupe fusionné SHALL avoir exactement une annonce principale. Les autres annonces du groupe sont secondaires et pointent directement vers elle, sans chaîne. Les annonces secondaires SHALL NOT apparaître dans la liste, sur la carte ni dans les compteurs, mais restent en base pour que le collecteur continue à les mettre à jour.

#### Scenario: Liste après fusion
- **WHEN** trois annonces (Bien'ici, Netty, SeLoger) sont fusionnées
- **THEN** la liste et la carte n'affichent qu'une annonce, marquée « 3 sources »

#### Scenario: Fusion de deux groupes
- **WHEN** un groupe A est fusionné avec un groupe B
- **THEN** toutes les secondaires de B pointent vers l'annonce principale retenue, sans chaîne de redirection

### Requirement: Choix de l'annonce principale
Lors de la formation d'un groupe, l'annonce principale SHALL être la première selon cet ordre :
1. adresse validée par l'utilisateur ;
2. données utilisateur (réaction, à visiter, note, commentaire) ;
3. enrichissement terminé ;
4. priorité de source : agence directe, Netty, Maes ou notaires, puis PAP ou Bien'ici, puis cartes e-mail ;
5. nombre de photos ;
6. ancienneté.

Une annonce qui rejoint un groupe existant SHALL NOT changer l'annonce principale.

#### Scenario: Carte e-mail rejoignant une annonce enrichie
- **WHEN** une carte SeLoger neuve est fusionnée avec une annonce Bien'ici déjà enrichie par l'agent
- **THEN** l'annonce Bien'ici reste principale et son enrichissement est conservé

#### Scenario: Annonce commentée
- **WHEN** deux annonces seules sont fusionnées, l'une enrichie par l'agent et l'autre commentée par l'utilisateur, sans autre donnée utilisateur ni adresse validée
- **THEN** l'annonce commentée devient principale

### Requirement: Offres de toutes les sources
L'annonce SHALL exposer la liste de ses offres. Chaque offre indique la source, l'agence, le lien, le prix, le statut et les dates de première et de dernière vue. Le statut vaut :
- active ;
- retirée ;
- non confirmée : carte e-mail qui n'a pas été revue depuis 45 jours.

L'UI SHALL présenter ces liens dans un menu déroulant « Annonces ↗ » quand il y en a plusieurs, et en bouton simple sinon.

#### Scenario: Menu déroulant des liens
- **WHEN** l'utilisateur ouvre la fiche d'un bien publié par Bien'ici à 235 000 € et par SeLoger à 241 500 €
- **THEN** le menu « Annonces ↗ » propose les deux liens avec leur source, leur agence et leur prix, la moins chère en premier

#### Scenario: Offre retirée
- **WHEN** la source d'une offre a retiré l'annonce
- **THEN** l'offre reste listée, marquée « retirée », et son lien reste cliquable

### Requirement: Prix retenu
Le prix retenu SHALL être le plus bas parmi les offres actives, en tenant compte des corrections de prix de l'agent. Une offre non confirmée ne fixe pas ce prix. Si aucune offre n'est active, c'est le plus bas de toutes les offres. Ce prix SHALL servir au score, au tri, aux filtres, au prix au m² et aux alertes. Tous les prix restent visibles.

#### Scenario: Deux prix actifs
- **WHEN** un bien a une offre active à 241 500 € et une autre à 235 000 €
- **THEN** la carte et le score utilisent 235 000 €, et la fiche indique l'autre prix

#### Scenario: Offre la moins chère retirée
- **WHEN** l'offre à 235 000 € est retirée et celle à 241 500 € reste active
- **THEN** le prix retenu devient 241 500 €

#### Scenario: Ancienne carte e-mail moins chère
- **WHEN** une carte SeLoger à 230 000 € n'a pas été revue depuis plus de 45 jours, alors que l'offre Bien'ici active du même bien est à 245 000 €
- **THEN** le prix retenu est 245 000 €, et l'offre SeLoger figure dans le menu comme « non confirmée »

### Requirement: Historique de prix fusionné
La fiche SHALL afficher un historique de prix unique, qui réunit les historiques de toutes les offres, date chaque entrée et indique sa source.

#### Scenario: Baisse vue sur une autre source
- **WHEN** une carte SeLoger reparaît à 239 500 € après une première carte à 241 500 € pour le même bien
- **THEN** l'historique montre 241 500 € puis 239 500 €, avec la source de chaque entrée, et la carte du bien porte le badge « Baisse »

### Requirement: Champs complétés par les autres sources
Un champ vide de l'annonce principale SHALL être complété par la valeur d'une autre offre, dans l'ordre de priorité des sources : terrain, DPE, GES, chambres, pièces, année, garage, jardin, adresse ou position. Les valeurs de l'enrichissement de l'agent et celles saisies par l'utilisateur SHALL rester prioritaires.

#### Scenario: Terrain connu seulement par la carte e-mail
- **WHEN** l'annonce principale Bien'ici n'a pas de terrain et la carte SeLoger fusionnée indique 367 m²
- **THEN** la fiche et le score utilisent un terrain de 367 m²

#### Scenario: Adresse validée par l'utilisateur
- **WHEN** l'utilisateur a validé l'adresse de l'annonce principale et une secondaire porte une autre adresse
- **THEN** l'adresse validée reste affichée et utilisée

### Requirement: Photos réunies sans doublon
La fiche SHALL présenter les photos de l'annonce principale, puis celles des autres offres qui ne sont pas identiques à une photo déjà présente.

#### Scenario: Vignette déjà présente
- **WHEN** la seule vignette d'une carte e-mail fusionnée est identique à la première photo de l'annonce principale
- **THEN** le carrousel ne l'affiche qu'une fois

### Requirement: Report des données utilisateur
Lors d'une fusion, l'annonce principale SHALL reprendre les données utilisateur des secondaires :
- 👍 si une annonce du groupe est aimée, l'emportant sur 👎 ;
- 👎 sinon, si une annonce du groupe est rejetée ;
- « à visiter » si une annonce l'est ;
- les notes réunies ;
- les commentaires de toutes les annonces du groupe, réunis dans un seul fil par ordre chronologique et comptés une seule fois dans le badge ;
- la date de première consultation et la date d'alerte les plus anciennes.

#### Scenario: Bien déjà rejeté qui revient
- **WHEN** une nouvelle source publie un bien que l'utilisateur avait marqué 👎
- **THEN** l'annonce fusionnée reste 👎, masquée par le filtre par défaut, et ne porte pas le badge « Nouveau »

#### Scenario: Conflit de réactions
- **WHEN** une annonce du groupe est 👍 et une autre 👎
- **THEN** l'annonce fusionnée est 👍

#### Scenario: Deux annonces commentées fusionnées
- **WHEN** une annonce portant deux commentaires est fusionnée avec une annonce portant un commentaire
- **THEN** la fiche du bien montre les trois commentaires par ordre chronologique, et sa carte affiche « 💬 3 »

### Requirement: Commentaire envoyé sur un bien fusionné
Un commentaire ajouté sur un bien fusionné SHALL être rattaché à son annonce principale, même si la requête désigne une annonce secondaire.

#### Scenario: Commentaire envoyé sur une secondaire
- **WHEN** l'API reçoit un commentaire pour une annonce devenue secondaire
- **THEN** le commentaire est rattaché à l'annonce principale du groupe

### Requirement: Bien actif tant qu'une offre est active
Un groupe SHALL rester visible comme actif tant qu'au moins une de ses offres est active, même si l'annonce principale a disparu de sa source.

#### Scenario: Principale disparue, secondaire active
- **WHEN** l'annonce Bien'ici principale disparaît alors que l'annonce Netty fusionnée reste en ligne
- **THEN** le bien reste dans la liste, et l'offre Bien'ici est marquée « retirée »

### Requirement: Score, alertes et enrichissement une seule fois par bien
Le score, les alertes Discord et la file d'enrichissement de l'agent SHALL porter uniquement sur les annonces principales, calculées avec les valeurs fusionnées. Un groupe déjà alerté SHALL NOT être réalerté parce qu'une nouvelle source le rejoint.

#### Scenario: File d'enrichissement
- **WHEN** l'agent demande les annonces en attente d'enrichissement
- **THEN** aucune annonce secondaire n'est renvoyée

#### Scenario: Nouvelle source d'un bien alerté
- **WHEN** une nouvelle source rejoint un bien déjà signalé sur Discord
- **THEN** aucune nouvelle alerte n'est envoyée

#### Scenario: Alerte d'un bien multi-sources
- **WHEN** un bien fusionné déclenche une alerte
- **THEN** l'alerte donne le prix retenu, le lien de l'offre active la moins chère, le nombre de sources et le lien vers la fiche principale

### Requirement: Liens vers une annonce secondaire
Une annonce secondaire SHALL rester consultable par son identifiant via l'API, qui indique l'annonce principale. L'UI SHALL rediriger un lien `#/l/<id>` d'une annonce secondaire vers la fiche principale.

#### Scenario: Ancien lien Discord
- **WHEN** l'utilisateur ouvre un ancien lien d'alerte qui pointe vers une annonce devenue secondaire
- **THEN** la fiche principale s'ouvre

### Requirement: Enrichissement reçu pour une annonce secondaire
Un enrichissement posté pour une annonce devenue secondaire, par exemple quand l'agent avait lu la file avant la fusion, SHALL être accepté et enregistré sur cette annonce. Il SHALL NOT remplacer l'enrichissement de l'annonce principale.

#### Scenario: Course entre agent et fusion
- **WHEN** l'agent poste un enrichissement pour une annonce fusionnée entre-temps
- **THEN** la réponse est un succès, l'enrichissement est rangé sur l'annonce secondaire et l'enrichissement de l'annonce principale reste inchangé

### Requirement: Fusion manuelle
L'utilisateur SHALL pouvoir fusionner depuis l'UI un doublon possible, ou deux annonces désignées. Une fusion manuelle SHALL lever un refus antérieur entre ces annonces.

#### Scenario: Valider un doublon possible
- **WHEN** l'utilisateur clique sur « Fusionner » dans le bloc « Doublon possible » d'une fiche
- **THEN** les deux annonces forment un seul bien, et la suggestion disparaît

### Requirement: Refus d'un doublon possible
L'utilisateur SHALL pouvoir marquer un doublon possible « Pas un doublon ». La suggestion disparaît alors et le refus est mémorisé.

#### Scenario: Rejeter une suggestion
- **WHEN** l'utilisateur clique sur « Pas un doublon »
- **THEN** les deux annonces restent séparées et la paire n'est plus proposée

### Requirement: Séparation d'une source
L'utilisateur SHALL pouvoir séparer une offre d'un bien fusionné. L'annonce séparée redevient indépendante avec ses propres données, dont les commentaires écrits sur elle avant la fusion. Les commentaires écrits sur le bien fusionné restent sur l'annonce principale. Le refus entre elle et le groupe est mémorisé, et le score des deux biens est recalculé.

#### Scenario: Séparer une fusion erronée
- **WHEN** l'utilisateur clique sur « Séparer » pour l'offre SeLoger d'un bien fusionné
- **THEN** l'annonce SeLoger réapparaît seule dans la liste avec son prix et ses photos, et elle n'est plus fusionnée automatiquement avec ce bien

#### Scenario: Commentaires après une séparation
- **WHEN** l'annonce SeLoger portait un commentaire avant sa fusion, puis un second commentaire a été ajouté sur le bien fusionné, et l'utilisateur sépare l'offre SeLoger
- **THEN** l'annonce SeLoger séparée montre son commentaire d'origine, et l'annonce principale garde le second commentaire

### Requirement: Filtre des doublons à valider
L'UI SHALL signaler les annonces qui ont au moins un doublon possible en attente, par un badge sur la carte, et SHALL proposer un filtre « Doublons à valider » qui les affiche seules. Le regroupement côté navigateur par empreinte `fingerprint` et la case « Afficher les doublons » SHALL être retirés.

#### Scenario: Revue des suggestions
- **WHEN** l'utilisateur active le filtre « Doublons à valider »
- **THEN** seules les annonces avec un doublon possible en attente s'affichent, et chaque fiche montre l'autre annonce côte à côte avec les boutons « Fusionner » et « Pas un doublon »
