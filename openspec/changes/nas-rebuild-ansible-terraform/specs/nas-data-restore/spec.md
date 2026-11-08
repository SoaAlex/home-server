# Spec Delta

## Purpose

Restaure les données du NAS (fichiers de `/volume1/docker`, volumes Docker nommés, bases Postgres, secrets de l'agent) depuis la sauvegarde restic, par un playbook séparé qui ne s'exécute que sur demande explicite.

## ADDED Requirements

### Requirement: Playbook de restauration séparé et opt-in
La restauration des données SHALL être un playbook distinct du playbook principal. Il ne SHALL rien écrire sans une variable de confirmation explicite, et le playbook principal ne SHALL jamais le déclencher.

#### Scenario: Lancement sans confirmation
- **WHEN** le playbook de restauration est lancé sans la variable de confirmation
- **THEN** il affiche ce qu'il restaurerait (snapshot, chemins, bases) et s'arrête sans rien écrire

### Requirement: Restauration pilotée par nas-backup
Le playbook SHALL passer par l'outil `nas-backup` (et donc restic dans ses conteneurs) pour lire les sauvegardes, depuis le dépôt du RAID ou depuis Google Drive au choix, et pour un snapshot choisi (par défaut le dernier).

#### Scenario: Dépôt Google Drive
- **WHEN** l'utilisateur choisit la source Google Drive et un snapshot
- **THEN** les fichiers sont lus depuis le dépôt `rclone:gdrive:nas-backup/restic` avec le même mot de passe restic

### Requirement: Mot de passe restic fourni à l'exécution
Le mot de passe restic SHALL être lu depuis le fichier attendu par nas-backup s'il existe, ou depuis le vault des secrets, et ne SHALL jamais être affiché.

#### Scenario: Mot de passe absent
- **WHEN** ni le fichier de mot de passe ni le vault ne le contiennent
- **THEN** le playbook échoue avant toute lecture avec un message qui indique où le placer

### Requirement: Pas d'écrasement de données vivantes
Le playbook SHALL refuser de restaurer vers un chemin non vide ou une base existante dont le service tourne, sauf variable explicite d'écrasement, et SHALL arrêter les services concernés avant d'écraser.

#### Scenario: Vaultwarden en marche
- **WHEN** la restauration de `vw-data` est demandée alors que le conteneur `bitwarden` tourne, sans variable d'écrasement
- **THEN** rien n'est écrit et le message nomme le service et la variable

### Requirement: Bases Postgres restaurées depuis leurs dumps
Les bases de `common-postgres` SHALL être restaurées depuis les dumps `pg_dump -Fc` du snapshot (rôles d'abord, puis chaque base), avec une vérification du nombre de tables après restauration.

#### Scenario: Base restaurée
- **WHEN** la base `teslamate` est restaurée sur un Postgres neuf
- **THEN** les rôles existent, la base existe et son nombre de tables est égal à celui du dump

### Requirement: Répétition sans interruption
Le playbook SHALL pouvoir restaurer vers un préfixe de test et dans un Postgres jetable au lieu des emplacements réels, pour valider la procédure sur le NAS en service sans arrêter aucun service.

#### Scenario: Répétition sur le NAS en service
- **WHEN** le playbook est lancé avec le préfixe de test et la confirmation
- **THEN** les fichiers sont restaurés sous ce préfixe, les bases dans un conteneur Postgres jetable, aucun conteneur en service n'est arrêté, et le préfixe et le conteneur sont supprimés à la fin

### Requirement: Propriété des fichiers restaurés
Les fichiers restaurés SHALL retrouver leur propriétaire et leurs modes d'origine tels qu'enregistrés dans le snapshot.

#### Scenario: Restauration de WireGuard
- **WHEN** `home-server/wireguard/` est restauré
- **THEN** les fichiers ont le même propriétaire et les mêmes modes que dans le snapshot
