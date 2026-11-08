# Spec Delta

## Purpose

Décrit l'état attendu de l'hôte Debian/UGOS en dehors des conteneurs (démon Docker, réseau, SSH, unités systemd, outils, services interdits) pour qu'Ansible le reproduise et le vérifie.

## ADDED Requirements

### Requirement: Racine Docker sur le SSD
Le playbook SHALL garantir que `/etc/docker/daemon.json` déclare `data-root` `/volume1/@docker` avec `containerd-snapshotter` désactivé, et que ce fichier est en place avant le premier démarrage de Docker sur un NAS neuf.

#### Scenario: NAS neuf
- **WHEN** Docker n'a encore créé aucun conteneur et `daemon.json` est absent
- **THEN** le fichier est écrit, Docker redémarre une fois et rapporte `/volume1/@docker` comme racine

#### Scenario: NAS en service conforme
- **WHEN** `daemon.json` a déjà le contenu attendu
- **THEN** rien n'est écrit et Docker n'est pas redémarré

### Requirement: Docker fourni par UGOS
Le playbook ne SHALL pas installer, mettre à jour ni remplacer le moteur Docker ou le plugin compose : il SHALL vérifier leur présence et une version minimale, et échouer avec un message clair s'ils manquent.

#### Scenario: Docker absent
- **WHEN** la commande `docker` n'existe pas sur l'hôte
- **THEN** l'exécution échoue en indiquant d'installer l'app Docker d'UGOS, sans tenter d'installation

### Requirement: Shim macvlan vers Pi-hole
Le playbook SHALL installer le script du shim macvlan sur le système de fichiers racine et une unité systemd système activée qui le lance au démarrage, avec l'interface, les adresses IPv4/IPv6 et l'adresse MAC épinglées définies dans le repo.

#### Scenario: Après installation
- **WHEN** le rôle est appliqué et l'unité démarrée
- **THEN** `ip route get 192.168.1.100` passe par l'interface `ph-shim` avec la source `192.168.1.56`

### Requirement: Redirection TCP SSH conservée après redémarrage
Le playbook SHALL poser un drop-in sshd qui fixe `AllowTcpForwarding local`, sans modifier `sshd_config` (réécrit par UGOS), valider la configuration avec `sshd -t` avant de recharger SSH, et ne jamais recharger SSH si la validation échoue.

#### Scenario: Configuration invalide
- **WHEN** la validation `sshd -t` échoue après écriture du drop-in
- **THEN** le drop-in précédent est restauré et SSH n'est pas rechargé

#### Scenario: Valeur effective
- **WHEN** le rôle est appliqué
- **THEN** `sshd -T` rapporte `allowtcpforwarding local`

### Requirement: Services utilisateur après montage des volumes
Le playbook SHALL installer le drop-in `user@1000` qui attend le montage de `/volume1` et `/home`, activer le linger de l'utilisateur, et installer et activer `nas-boot-check.service`.

#### Scenario: Redémarrage du NAS
- **WHEN** le NAS redémarre après application du rôle
- **THEN** les services et timers utilisateur (Hermes, nas-backup) démarrent sans connexion de l'utilisateur et un verdict nas-boot-check est posté

### Requirement: Timers et services utilisateur
Le playbook SHALL installer dans `~/.config/systemd/user/` les unités `nas-backup*` depuis `backup/systemd/` et activer leurs timers, en recopiant le contenu du repo sans le modifier.

#### Scenario: Timers actifs
- **WHEN** le rôle est appliqué
- **THEN** `systemctl --user list-timers` montre `nas-backup.timer`, `nas-backup-weekly.timer` et `nas-backup-monthly.timer` actifs

### Requirement: Outils épinglés et vérifiés
Le playbook SHALL installer restic, rclone et age dans `~/.local/bin` à des versions épinglées dans le repo, en vérifiant chaque archive téléchargée par somme SHA-256 avant de l'installer.

#### Scenario: Somme incorrecte
- **WHEN** l'archive téléchargée ne correspond pas à la somme épinglée
- **THEN** l'installation échoue et le binaire existant n'est pas remplacé

#### Scenario: Version déjà installée
- **WHEN** le binaire en place rapporte la version épinglée
- **THEN** rien n'est téléchargé

### Requirement: Wrappers versionnés
Chaque wrapper de `~/.local/bin` utilisé par les jobs ou les skills (`nas-backup`, `nas-boot-check`, `maison`, `eurl-frais`, `mrun`, `dk`) SHALL provenir d'un fichier du repo, et le playbook SHALL l'installer exécutable pour l'utilisateur.

#### Scenario: Wrapper divergent
- **WHEN** un wrapper installé diffère de sa source dans le repo
- **THEN** le mode vérification affiche le diff et l'exécution normale le remplace par la version du repo

### Requirement: Tailscale reste arrêté
Le playbook SHALL garantir que `tailscaled.service` est arrêté et désactivé, et ne SHALL jamais le démarrer.

#### Scenario: Tailscale réactivé par une mise à jour UGOS
- **WHEN** une mise à jour a réactivé ou démarré `tailscaled`
- **THEN** le playbook l'arrête et le désactive, et le rapporte comme changement

### Requirement: Crontab root sans tâche obsolète
Le playbook SHALL pouvoir retirer de la crontab root les copies et dumps hérités remplacés par nas-backup, uniquement quand l'utilisateur l'active par une variable explicite, sans toucher aux autres lignes.

#### Scenario: Nettoyage non demandé
- **WHEN** la variable de nettoyage n'est pas activée
- **THEN** la crontab root n'est ni lue en écriture ni modifiée

#### Scenario: Nettoyage demandé
- **WHEN** la variable est activée
- **THEN** seules les lignes héritées listées dans le repo sont retirées
