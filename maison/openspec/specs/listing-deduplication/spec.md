# listing-deduplication Specification

## Purpose

Repérer les annonces qui décrivent le même bien, venues de sources différentes ou de la même source sous plusieurs identifiants. La comparaison porte d'abord sur les photos, puis sur les caractéristiques et le texte. Selon le degré de certitude, le système fusionne automatiquement les annonces ou les propose à l'utilisateur.

## Requirements

### Requirement: Empreinte de chaque photo stockée
Le système SHALL calculer une empreinte visuelle pour chaque photo d'annonce stockée localement et la conserver en base, associée à l'annonce et au rang de la photo. Une photo SHALL être décodée au plus une fois pour cela, sauf si son fichier change.

#### Scenario: Photo téléchargée par le collecteur
- **WHEN** le collecteur télécharge une nouvelle photo pour une annonce
- **THEN** l'empreinte de cette photo est enregistrée dans le même cycle, sans relire le fichier depuis le disque

#### Scenario: Cycle sans nouvelle photo
- **WHEN** un cycle du collecteur ne télécharge aucune photo et qu'aucun fichier de photo n'a changé
- **THEN** aucune image n'est décodée pour la déduplication

#### Scenario: Fichier remplacé
- **WHEN** le fichier d'une photo déjà hachée change de taille ou de date de modification
- **THEN** son empreinte est recalculée au passage suivant

### Requirement: Reconnaissance d'une même photo
Le système SHALL considérer deux photos comme identiques quand l'une est une copie de l'autre, recompressée, redimensionnée, légèrement recadrée ou marquée d'un filigrane. Deux photos de scènes différentes SHALL NOT être considérées comme identiques.

#### Scenario: Vignette d'e-mail et photo d'origine
- **WHEN** une carte d'alerte e-mail porte une vignette de 500×370 px qui reproduit la photo de 1920×1440 px d'une annonce Bien'ici
- **THEN** les deux photos sont reconnues comme identiques

#### Scenario: Pièces différentes
- **WHEN** deux photos montrent deux pièces ou deux façades différentes
- **THEN** elles ne sont pas reconnues comme identiques

### Requirement: Photos génériques ignorées
Le système SHALL écarter des preuves de doublon les photos génériques, c'est-à-dire :
- les images quasi uniformes ;
- les photos partagées par des annonces dont les caractéristiques sont incompatibles entre elles, comme un bandeau d'agence ou un rendu commun à plusieurs lots.

#### Scenario: Bandeau d'agence répété
- **WHEN** la même image de bandeau figure dans plusieurs annonces viager d'une agence, dont certaines ont des surfaces incompatibles
- **THEN** cette image est marquée générique et ne compte comme preuve pour aucune paire

#### Scenario: Image quasi uniforme
- **WHEN** une photo est presque unie (fond blanc, logo seul)
- **THEN** elle ne compte pas comme photo commune

### Requirement: Compatibilité des caractéristiques
Le système SHALL juger la compatibilité de deux annonces sur la surface, le nombre de chambres, la distance entre leurs positions et le prix. Une caractéristique inconnue d'un côté SHALL NOT empêcher la compatibilité.

#### Scenario: Surfaces proches
- **WHEN** une annonce affiche 100 m² et l'autre 101 m²
- **THEN** les surfaces sont compatibles

#### Scenario: Surfaces incompatibles
- **WHEN** une annonce affiche 100 m² et l'autre 130 m²
- **THEN** la paire fait l'objet d'un veto

#### Scenario: Communes voisines
- **WHEN** la même maison est annoncée à Wasquehal par une source et à Villeneuve-d'Ascq par une autre, à quelques kilomètres d'écart
- **THEN** la différence de commune n'empêche pas la fusion

### Requirement: Fusion automatique des cas sûrs
Le système SHALL fusionner automatiquement deux annonces sans veto dans l'un des cas suivants :
- elles partagent au moins deux photos non génériques distinctes ;
- elles partagent une photo non générique et ont une surface et un prix compatibles (au moins l'un des deux connu des deux côtés) ;
- leurs descriptions sont quasi identiques et leur surface et leur prix sont compatibles.

#### Scenario: Deux photos communes
- **WHEN** une annonce Netty et une annonce Bien'ici partagent trois photos non génériques et ont une surface compatible
- **THEN** elles sont fusionnées sans intervention

#### Scenario: Carte e-mail à une seule vignette
- **WHEN** une carte SeLoger dont la seule vignette est identique à une photo d'une annonce Bien'ici affiche la même surface et un prix à 3 % près
- **THEN** elles sont fusionnées sans intervention

#### Scenario: Prix différents chez la même agence
- **WHEN** deux annonces de la même agence partagent cinq photos, avec des prix de 205 000 € et de 190 000 € et deux communes voisines
- **THEN** elles sont fusionnées et les deux prix restent visibles

### Requirement: Fusion automatique des caractéristiques jumelles
Le système SHALL fusionner automatiquement, même sans photo commune, deux annonces sans veto aux caractéristiques jumelles : même commune ou communes à 3 km au plus, prix à 1 % près, surface à 1 m² (ou 1 %) près, pièces à une unité près, mêmes chambres et terrain à 5 % près quand ils sont connus des deux côtés.

#### Scenario: Cartes jumelles sans photo commune
- **WHEN** deux cartes SeLoger affichent le même prix, la même surface, le même terrain et la même commune, mais des vignettes différentes
- **THEN** elles sont fusionnées sans intervention, et l'utilisateur peut les séparer

#### Scenario: Terrains différents
- **WHEN** deux annonces de la même commune ont le même prix et la même surface, mais des terrains de 125 m² et de 254 m²
- **THEN** elles ne sont pas fusionnées

### Requirement: Pas de dérive des fusions sans photo
Un jumeau SHALL rejoindre un groupe existant seulement s'il reste compatible avec chacun de ses membres : terrain à 5 % près et mêmes chambres quand ils sont connus, surface à 5 % près, prix à 15 % près, pas d'adresses différentes. Sinon, la paire est proposée comme doublon possible. Les paires les plus proches sont fusionnées en premier.

#### Scenario: Jumeaux en chaîne
- **WHEN** A (365 000 €, 90 m², terrain 144 m²) et B (367 000 €, 91 m², terrain inconnu) sont jumeaux, et B et C (367 000 €, 91 m², terrain 325 m²) aussi
- **THEN** B et C sont fusionnés, et A est proposé comme doublon possible du groupe

#### Scenario: Cartes répétées d'un même bien
- **WHEN** une annonce Netty et 39 cartes SeLoger, renvoyées sous des identifiants différents, affichent le même prix et la même surface dans la même commune
- **THEN** elles forment un seul bien

### Requirement: Vétos à la fusion automatique
Le système SHALL NOT fusionner automatiquement deux annonces dans les cas suivants :
- surfaces incompatibles ;
- écart d'au moins deux chambres ;
- positions trop éloignées ;
- refus enregistré par l'utilisateur ;
- deux adresses précises à plus de 500 m l'une de l'autre, sauf si les annonces partagent au moins deux photos ;
- programme neuf ou lot de promoteur.

Un programme neuf, ou des adresses différentes avec une seule photo commune, donnent au plus un doublon possible.

#### Scenario: Lots d'un même programme neuf
- **WHEN** deux annonces d'un programme neuf partagent une image de rendu et ont des surfaces proches
- **THEN** elles ne sont pas fusionnées automatiquement et sont proposées comme doublon possible

#### Scenario: Adresses différentes
- **WHEN** deux annonces de même prix et de même surface ont chacune une adresse précise, à 1 km l'une de l'autre, sans photo commune
- **THEN** elles ne sont ni fusionnées ni proposées

#### Scenario: Chaînage interdit
- **WHEN** une annonce A est fusionnable avec B, mais une autre source C déjà fusionnée avec B fait l'objet d'un veto avec A
- **THEN** A n'est pas fusionnée automatiquement dans ce groupe et la paire est proposée comme doublon possible

### Requirement: Doublons possibles proposés à l'utilisateur
Le système SHALL enregistrer comme « doublon possible » toute paire sans fusion automatique ni veto dur qui présente l'un des indices suivants :
- une photo commune non générique, avec une surface ou un prix trop éloignés, ou des adresses différentes ;
- un cas de fusion automatique bloqué par un programme neuf ;
- un cas de fusion automatique bloqué par un veto avec une autre annonce du groupe visé.

Le système SHALL conserver les preuves de chaque paire proposée.

#### Scenario: Une photo commune, surfaces éloignées
- **WHEN** deux annonces partagent une photo non générique mais affichent 97 m² et 90 m²
- **THEN** la paire est proposée comme doublon possible, avec les caractéristiques comparées

#### Scenario: Preuves consultables
- **WHEN** l'utilisateur consulte un doublon possible
- **THEN** il voit le nombre de photos communes, les écarts de prix, de surface et de distance, et la similarité du texte

### Requirement: Mémoire des refus
Le système SHALL mémoriser chaque refus (« Pas un doublon » ou « Séparer »). Il SHALL NOT refusionner automatiquement ni reproposer une paire refusée, même après de nouvelles analyses. Seule une fusion manuelle de l'utilisateur SHALL lever le refus.

#### Scenario: Refus puis nouvelle analyse
- **WHEN** l'utilisateur a marqué une paire « Pas un doublon » et que l'analyse complète est relancée
- **THEN** la paire n'est ni fusionnée ni reproposée

### Requirement: Analyse initiale avec rapport à blanc
Le système SHALL fournir une analyse complète des annonces actives et des annonces disparues depuis 90 jours au plus. Par défaut, elle s'exécute à blanc et produit un rapport HTML autonome. Ce rapport liste les fusions automatiques prévues et les doublons possibles, avec les vignettes côte à côte et les preuves.

#### Scenario: Exécution à blanc
- **WHEN** l'analyse initiale est lancée sans option d'application
- **THEN** aucune annonce n'est modifiée en base et un rapport HTML lisible hors ligne est produit

#### Scenario: Application
- **WHEN** l'analyse initiale est relancée avec l'option d'application
- **THEN** les fusions automatiques et les doublons possibles du rapport sont enregistrés

#### Scenario: Relance idempotente
- **WHEN** l'analyse est appliquée deux fois de suite sans nouvelle annonce
- **THEN** la seconde exécution ne crée ni fusion ni suggestion nouvelle

### Requirement: Détection à chaque nouvelle annonce
À chaque cycle, le collecteur SHALL comparer les annonces nouvelles ou modifiées aux annonces déjà connues, après le téléchargement des photos et avant le calcul du score et l'envoi des alertes.

#### Scenario: Carte e-mail d'un bien déjà connu
- **WHEN** une nouvelle carte d'alerte e-mail arrive pour un bien déjà présent via Bien'ici
- **THEN** elle est fusionnée dans le même cycle, n'entre pas dans la file d'enrichissement de l'agent et ne déclenche aucune alerte

#### Scenario: Remise en vente
- **WHEN** une nouvelle annonce correspond à une annonce disparue depuis moins de 90 jours
- **THEN** elle est fusionnée avec elle et l'historique de prix du bien est conservé

### Requirement: Vignettes des cartes à compléter
Le système SHALL télécharger, puis hacher, la vignette fournie par une carte d'alerte e-mail même quand l'annonce attend que l'agent la complète (« à compléter »). La déduplication peut ainsi intervenir avant l'enrichissement. Le système SHALL NOT appeler une page ni une API LeBonCoin ou SeLoger pour obtenir d'autres photos.

#### Scenario: Carte sans commune
- **WHEN** une carte SeLoger arrive sans commune reconnue, avec une vignette
- **THEN** la vignette est téléchargée et hachée dans le cycle, et aucune autre requête vers SeLoger n'est faite

### Requirement: Coût borné du traitement continu
Le traitement incrémental SHALL comparer les nouvelles empreintes à celles déjà en base sans redécoder d'images existantes. L'analyse initiale de la base actuelle (environ 2 000 annonces et 12 000 photos) SHALL se terminer en moins de 15 minutes sur le NAS.

#### Scenario: Nouvelle annonce de dix photos
- **WHEN** une nouvelle annonce de dix photos arrive dans une base d'environ 12 000 empreintes
- **THEN** sa comparaison ajoute moins de 5 secondes au cycle, hors téléchargement
