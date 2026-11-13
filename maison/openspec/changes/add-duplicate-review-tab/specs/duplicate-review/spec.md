# Spec Delta

## Purpose

Permettre à l'utilisateur de passer en revue rapidement, dans un onglet dédié, les doublons possibles en attente : un couple de biens à la fois, comparé côte à côte, tranché en un geste.

## ADDED Requirements

### Requirement: Onglet de revue des doublons
L'UI SHALL proposer un onglet « Doublons », à côté d'« Annonces » et de « Carte », accessible par l'adresse `#/doublons`. L'onglet SHALL afficher le nombre de couples à valider dans le périmètre par défaut, et aucun nombre quand il n'y en a pas.

#### Scenario: Ouverture de l'onglet
- **WHEN** l'utilisateur clique sur l'onglet « Doublons (87) »
- **THEN** l'adresse devient `#/doublons`, l'onglet est marqué actif et le premier couple de la file s'affiche

#### Scenario: Aucun doublon à valider
- **WHEN** aucun couple n'est en attente dans le périmètre par défaut
- **THEN** l'onglet s'intitule « Doublons » sans nombre, et la vue indique qu'il n'y a rien à valider

### Requirement: Un couple de biens par entrée de la file
La file SHALL présenter chaque couple de biens une seule fois, chaque côté étant ramené à l'annonce principale de son groupe, même si plusieurs paires d'annonces relient les deux biens. Un couple dont les deux côtés appartiennent au même groupe SHALL NOT figurer dans la file.

#### Scenario: Plusieurs paires entre deux biens
- **WHEN** trois paires « doublon possible » relient des sources du bien A à des sources du bien B
- **THEN** la file contient une seule entrée A–B, avec les preuves de la paire qui a le plus de photos communes

#### Scenario: Couple fusionné entre-temps par le collecteur
- **WHEN** une paire suggérée relie deux annonces que le collecteur a ensuite fusionnées dans le même bien
- **THEN** ce couple n'apparaît pas dans la file

### Requirement: Périmètre de la file
Par défaut, la file SHALL contenir les couples dont au moins un côté est actif et non exclu (« à compléter » ne compte pas comme exclusion). L'utilisateur SHALL pouvoir y ajouter, par une case, les couples dont les deux côtés sont exclus ou disparus. Les filtres du panneau des annonces SHALL NOT s'appliquer à la file.

#### Scenario: Couple entièrement écarté
- **WHEN** les deux côtés d'un couple sont exclus pour « ville exclue »
- **THEN** le couple n'est pas dans la file par défaut, et il y apparaît quand la case « Inclure les biens écartés ou disparus » est cochée

#### Scenario: Un seul côté écarté
- **WHEN** un côté du couple est 👎 ou exclu et l'autre est actif et non exclu
- **THEN** le couple est dans la file par défaut

#### Scenario: Filtres de la liste
- **WHEN** la puce « Garage ✓ » est active dans le panneau des filtres
- **THEN** la file de l'onglet « Doublons » reste la même

### Requirement: Ordre de la file
La file SHALL être triée par meilleur score des deux côtés, décroissant (score inconnu en dernier), puis par date de détection la plus ancienne. L'UI SHALL afficher la position du couple courant et la taille de la file.

#### Scenario: Biens les mieux notés d'abord
- **WHEN** la file contient un couple dont le meilleur score est 82 et un autre dont le meilleur score est 40
- **THEN** le couple à 82 est présenté en premier, avec la mention « 1 / 2 »

### Requirement: Comparaison côte à côte
Pour le couple courant, l'UI SHALL montrer les deux biens côte à côte (l'un sous l'autre sur un écran étroit), avec pour chacun jusqu'à six photos, ses sources, son agence et un lien vers chaque annonce d'origine et vers sa fiche. L'UI SHALL afficher les preuves de la détection : règle, nombre de photos communes, écarts de prix et de surface, distance, similarité du texte et veto éventuel.

#### Scenario: Consultation d'un couple
- **WHEN** le couple courant oppose une annonce Bien'ici et une carte SeLoger
- **THEN** les deux biens sont affichés côte à côte avec leurs photos, les preuves « caractéristiques jumelles · aucune photo commune · prix 365 000 / 367 000 € · 0,4 km » et un lien vers chaque fiche

#### Scenario: Ouverture d'une fiche depuis la revue
- **WHEN** l'utilisateur ouvre la fiche d'un des deux biens depuis la revue, puis la ferme
- **THEN** il revient à l'onglet « Doublons » sur le même couple

### Requirement: Tableau comparatif des caractéristiques
L'UI SHALL présenter, ligne par ligne pour les deux biens : prix retenu, surface habitable, terrain, pièces, chambres, DPE, ville, adresse, agence et date de première vue. Chaque ligne SHALL indiquer si les deux valeurs sont égales (prix à 1 % près, surfaces à 1 m² ou 1 % près), différentes ou inconnues d'un côté. Les valeurs sont celles affichées sur les cartes de la liste (bien fusionné, corrections de l'agent comprises).

#### Scenario: Terrains différents
- **WHEN** les deux biens ont le même prix et la même surface, mais des terrains de 144 m² et de 325 m²
- **THEN** les lignes prix et surface sont marquées égales, et la ligne terrain est mise en évidence comme différente

#### Scenario: Surfaces à 1 m² près
- **WHEN** les deux biens affichent 90 m² et 91 m² habitables
- **THEN** la ligne surface est marquée égale

#### Scenario: Valeur inconnue d'un côté
- **WHEN** un seul des deux biens a un DPE connu
- **THEN** la ligne DPE est marquée « inconnu d'un côté », sans être présentée comme une différence

### Requirement: Décisions en un geste
Pour le couple courant, l'utilisateur SHALL pouvoir choisir « Fusionner », « Pas un doublon » ou « Passer », par un bouton ou par une touche (`F`, `N`, `S` ou `→`). Le couple suivant SHALL s'afficher sans attendre la réponse du serveur. Les touches SHALL rester sans effet pendant une saisie ou quand une fiche est ouverte.

#### Scenario: Fusion au clavier
- **WHEN** l'utilisateur appuie sur `F` devant un couple
- **THEN** le couple suivant s'affiche aussitôt, et les deux biens sont fusionnés selon les règles de la fusion manuelle

#### Scenario: Refus
- **WHEN** l'utilisateur clique sur « Pas un doublon »
- **THEN** le refus est mémorisé selon les règles existantes et le couple ne revient plus dans la file

#### Scenario: Passer un couple
- **WHEN** l'utilisateur appuie sur `→` devant un couple
- **THEN** rien n'est enregistré, le couple suivant s'affiche et le couple passé est reproposé en fin de file pendant cette visite

#### Scenario: Touche pendant une saisie
- **WHEN** l'utilisateur tape « f » dans le champ de recherche alors que l'onglet « Doublons » est affiché
- **THEN** aucune décision n'est prise

### Requirement: Annulation de la dernière décision
Après « Fusionner » ou « Pas un doublon », l'UI SHALL proposer « Annuler » (bouton ou touche `Z`) pendant 5 secondes, et SHALL NOT enregistrer la décision avant la fin de ce délai, une nouvelle décision ou la sortie de l'onglet. Une décision annulée SHALL NOT être enregistrée, et le couple SHALL redevenir le couple courant.

#### Scenario: Refus annulé
- **WHEN** l'utilisateur clique sur « Pas un doublon », puis sur « Annuler » deux secondes plus tard
- **THEN** aucun refus n'est enregistré et le couple est de nouveau affiché

#### Scenario: Décision confirmée par la suivante
- **WHEN** l'utilisateur appuie sur `F`, puis sur `N` pour le couple suivant une seconde plus tard
- **THEN** la fusion du premier couple est enregistrée à ce moment, et seul le refus du second reste annulable

#### Scenario: Sortie de l'onglet
- **WHEN** l'utilisateur fusionne un couple puis passe à l'onglet « Annonces » dans le délai
- **THEN** la fusion est enregistrée, et la liste affiche le bien fusionné

### Requirement: Décision sur un couple déjà traité
Le système SHALL refuser une décision portant sur un couple qui n'a plus de doublon possible en attente, notamment parce qu'il a été fusionné, refusé ou séparé entre-temps. L'UI SHALL alors le signaler sans bloquer la revue et SHALL retirer ce couple de la file.

#### Scenario: Fusion faite par le collecteur pendant la revue
- **WHEN** l'utilisateur refuse un couple que le collecteur vient de fusionner automatiquement
- **THEN** aucun refus n'est enregistré, l'UI indique « déjà traité » et la revue continue au couple suivant

#### Scenario: Annonce supprimée
- **WHEN** une décision vise une annonce qui n'existe plus
- **THEN** la décision est refusée comme « introuvable » et le couple quitte la file

### Requirement: Cohérence avec la liste
Après chaque décision enregistrée, la liste, la carte, les badges « Doublon ? », la puce « Doublons à valider » et le nombre affiché sur l'onglet SHALL refléter le nouvel état, sans rechargement de la page. Une fusion SHALL retirer de la file tout autre couple devenu interne au bien fusionné, et ramener les couples restants à la nouvelle annonce principale.

#### Scenario: Bien présent dans deux couples
- **WHEN** le bien A figure dans les couples A–B et A–C, et que l'utilisateur fusionne A et B
- **THEN** le couple suivant qui concernait A désigne désormais le bien fusionné, et le nombre de l'onglet diminue d'un

#### Scenario: Fin de la file
- **WHEN** l'utilisateur tranche le dernier couple
- **THEN** la vue indique que tous les doublons sont traités, propose de revoir les couples passés s'il y en a, et l'onglet s'intitule « Doublons » sans nombre
