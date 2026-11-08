# Spec Delta

## Purpose

Déploie les stacks Docker du NAS à partir des fichiers compose existants du repo, dans le bon ordre, sans dupliquer leur définition ni perturber les conteneurs déjà conformes.

## ADDED Requirements

### Requirement: Les fichiers compose restent la source de vérité
Le déploiement SHALL utiliser les `docker-compose.yaml` existants de chaque stack sans les générer, les réécrire ni recopier leurs services dans la configuration Ansible. Un service ajouté ou modifié dans un compose SHALL être pris en compte sans modifier Ansible.

#### Scenario: Service ajouté dans un compose
- **WHEN** un service est ajouté au compose d'une stack déjà gérée
- **THEN** l'exécution suivante du playbook le démarre sans aucune modification des fichiers Ansible

#### Scenario: Compose utilisable à la main
- **WHEN** l'utilisateur lance `docker compose up -d` dans un dossier de stack après le passage d'Ansible
- **THEN** le résultat est identique, sans fichier supplémentaire requis

### Requirement: Ordre de démarrage des stacks
Le playbook SHALL démarrer les stacks dans l'ordre `common`, `home-server`, `teslamate`, `maison`, et SHALL vérifier que le réseau `common-network` existe avant de démarrer une stack qui le déclare `external`.

#### Scenario: Reconstruction à vide
- **WHEN** aucune stack ne tourne et aucun réseau n'existe
- **THEN** `common` crée `common-network`, puis les autres stacks démarrent sans erreur de réseau externe manquant

### Requirement: Stacks conformes laissées en place
Le playbook ne SHALL pas recréer ni redémarrer un conteneur dont la configuration compose et l'image n'ont pas changé, et ne SHALL pas tirer de nouvelle image d'office.

#### Scenario: Stack déjà démarrée
- **WHEN** une stack tourne avec la configuration du repo
- **THEN** le playbook rapporte `ok` pour cette stack et aucun conteneur n'est recréé

#### Scenario: Mise à jour des images demandée
- **WHEN** l'utilisateur passe la variable explicite de mise à jour des images
- **THEN** les images sont tirées et seuls les conteneurs dont l'image a changé sont recréés

### Requirement: Images construites localement
Le playbook SHALL construire les images locales (`maison`, `eurl-frais`, `nas-backup`) quand elles sont absentes, et ne SHALL démarrer aucun conteneur pour `eurl-frais`, qui reste une commande lancée à la demande.

#### Scenario: Image eurl-frais absente
- **WHEN** l'image `eurl-frais:latest` n'existe pas
- **THEN** elle est construite depuis `eurl-frais/` et aucun conteneur `eurl-frais` ne reste en marche

### Requirement: Stacks hors compose explicitement exclues
Les applications gérées par UGOS (qBittorrent) et les conteneurs propres à Hermes (bac à sable, helpers) ne SHALL pas être créés, modifiés ni supprimés par le playbook.

#### Scenario: Conteneur qBittorrent présent
- **WHEN** le conteneur `qbittorrent-app-1` existe
- **THEN** le playbook ne le modifie pas et ne le signale pas comme écart

### Requirement: Préconditions des données
Avant de démarrer une stack, le playbook SHALL vérifier la présence de son `.env` et des dossiers de données dont la perte rendrait le service vide sans erreur (Vaultwarden, WireGuard, NPM), et SHALL refuser de démarrer la stack si l'un manque sauf autorisation explicite d'un démarrage à vide.

#### Scenario: Données Vaultwarden absentes
- **WHEN** `home-server/vw-data/` est vide ou absent et aucune autorisation de démarrage à vide n'est passée
- **THEN** la stack `home-server` n'est pas démarrée et le message renvoie vers la restauration des données
