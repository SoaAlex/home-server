# Spec Delta

## MODIFIED Requirements

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

### Requirement: Report des données utilisateur
Lors d'une fusion, l'annonce principale SHALL reprendre les données utilisateur des secondaires :
- 👍 si une annonce du groupe est aimée, l'emportant sur 👎 ;
- 👎 sinon, si une annonce du groupe est rejetée ;
- « à visiter » si une annonce l'est ;
- les notes réunies ;
- les commentaires de toutes les annonces du groupe, réunis dans un seul fil par ordre chronologique et comptés une seule fois dans le badge ;
- la date de première consultation et la date d'alerte les plus anciennes.

Un commentaire ajouté sur un bien fusionné SHALL être rattaché à son annonce principale, même si la requête désigne une annonce secondaire.

#### Scenario: Bien déjà rejeté qui revient
- **WHEN** une nouvelle source publie un bien que l'utilisateur avait marqué 👎
- **THEN** l'annonce fusionnée reste 👎, masquée par le filtre par défaut, et ne porte pas le badge « Nouveau »

#### Scenario: Conflit de réactions
- **WHEN** une annonce du groupe est 👍 et une autre 👎
- **THEN** l'annonce fusionnée est 👍

#### Scenario: Deux annonces commentées fusionnées
- **WHEN** une annonce portant deux commentaires est fusionnée avec une annonce portant un commentaire
- **THEN** la fiche du bien montre les trois commentaires par ordre chronologique, et sa carte affiche « 💬 3 »

#### Scenario: Commentaire envoyé sur une secondaire
- **WHEN** l'API reçoit un commentaire pour une annonce devenue secondaire
- **THEN** le commentaire est rattaché à l'annonce principale du groupe

### Requirement: Séparation d'une source
L'utilisateur SHALL pouvoir séparer une offre d'un bien fusionné. L'annonce séparée redevient indépendante avec ses propres données, dont les commentaires écrits sur elle avant la fusion. Les commentaires écrits sur le bien fusionné restent sur l'annonce principale. Le refus entre elle et le groupe est mémorisé, et le score des deux biens est recalculé.

#### Scenario: Séparer une fusion erronée
- **WHEN** l'utilisateur clique sur « Séparer » pour l'offre SeLoger d'un bien fusionné
- **THEN** l'annonce SeLoger réapparaît seule dans la liste avec son prix et ses photos, et elle n'est plus fusionnée automatiquement avec ce bien

#### Scenario: Commentaires après une séparation
- **WHEN** l'annonce SeLoger portait un commentaire avant sa fusion, puis un second commentaire a été ajouté sur le bien fusionné, et l'utilisateur sépare l'offre SeLoger
- **THEN** l'annonce SeLoger séparée montre son commentaire d'origine, et l'annonce principale garde le second commentaire
