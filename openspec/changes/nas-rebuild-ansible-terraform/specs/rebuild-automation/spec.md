# Spec Delta

## Purpose

Donne un point d'entrée unique et rejouable pour reconstruire le NAS (hôte, stacks, agent, configuration publique, données), exécutable depuis le Mac ou depuis le NAS, et vérifiable sans rien modifier.

## ADDED Requirements

### Requirement: Playbook de convergence complet
Le repo SHALL fournir un playbook principal qui amène le NAS à l'état décrit dans le repo : configuration de l'hôte, secrets, stacks Docker et agent Hermes. Il SHALL être idempotent : une deuxième exécution consécutive ne rapporte aucun changement.

#### Scenario: Deuxième exécution
- **WHEN** le playbook principal est exécuté deux fois de suite sur le même NAS sans autre modification entre les deux
- **THEN** la deuxième exécution termine avec `changed=0` et `failed=0`

#### Scenario: Exécution partielle par tag
- **WHEN** l'utilisateur lance le playbook avec un tag de rôle (par exemple `--tags stacks`)
- **THEN** seules les tâches de ce périmètre s'exécutent, sans toucher au reste de l'hôte

### Requirement: Vérification sans modification
Le playbook principal SHALL fonctionner en mode vérification (`--check --diff`) sans rien modifier sur le NAS et en listant chaque écart entre le NAS et le repo.

#### Scenario: Adoption du NAS en production
- **WHEN** le playbook est lancé en `--check --diff` sur le NAS actuel après l'adoption
- **THEN** il rapporte zéro changement, ou seulement des écarts documentés dans le repo comme volontaires

#### Scenario: Écart détecté
- **WHEN** un fichier géré (unité systemd, wrapper, drop-in) diffère sur le NAS
- **THEN** le mode vérification affiche le fichier concerné et son diff, sans l'écrire

### Requirement: Pas de redémarrage disruptif implicite
Aucune exécution SHALL redémarrer le démon Docker, un conteneur en cours d'exécution ou un service système sur un NAS en service sans une variable d'autorisation explicite passée par l'utilisateur.

#### Scenario: daemon.json différent sur un NAS en service
- **WHEN** `/etc/docker/daemon.json` diffère et Docker tourne avec des conteneurs actifs, sans autorisation de redémarrage
- **THEN** l'exécution échoue sur cette tâche avec un message qui explique l'écart et la variable à passer, sans redémarrer Docker

#### Scenario: Reconstruction autorisée
- **WHEN** l'utilisateur passe l'autorisation explicite de redémarrage
- **THEN** le fichier est écrit et Docker est redémarré une seule fois

### Requirement: Exécution depuis le Mac ou depuis le NAS
L'automatisation SHALL pouvoir être lancée soit depuis une machine de contrôle externe (le Mac) en SSH vers le NAS, soit depuis un conteneur de contrôle sur le NAS lui-même, avec les mêmes playbooks, le même inventaire et le même résultat.

#### Scenario: Depuis le Mac
- **WHEN** l'utilisateur lance le playbook depuis un clone du repo sur son Mac, avec sa clé SSH et le mot de passe sudo demandé à l'exécution
- **THEN** le NAS est configuré en SSH, sans rien installer d'Ansible sur le NAS

#### Scenario: Depuis le NAS, rien d'installé sur l'hôte
- **WHEN** l'utilisateur lance le script d'exécution fourni sur le NAS
- **THEN** Ansible tourne dans un conteneur construit depuis le repo et configure l'hôte en SSH, sans paquet Ansible ni Python supplémentaire installé sur l'hôte

### Requirement: Ordre d'une reconstruction complète documenté
`REBUILD.md` SHALL décrire la reconstruction comme une suite de commandes (préparation manuelle UGOS, restauration des données, playbook principal, Terraform, vérifications) et garder en annexe manuelle uniquement ce qui ne peut pas être automatisé.

#### Scenario: Nouveau NAS
- **WHEN** un lecteur suit `REBUILD.md` sur un NAS neuf dont UGOS, les volumes et Docker sont en place
- **THEN** chaque étape automatisable est une commande Ansible ou Terraform, et chaque étape manuelle (RAID, partages, utilisateurs, synchro OneDrive, app qBittorrent, VM, box Internet) est listée avec sa raison

### Requirement: Vérification de fin de reconstruction
Le playbook principal SHALL se terminer par une vérification qui échoue si un élément critique attendu n'est pas en place : racine Docker sur `/volume1/@docker`, volumes nommés présents, conteneurs avec politique de redémarrage en marche, route vers Pi-hole par le shim.

#### Scenario: Racine Docker erronée
- **WHEN** Docker tourne avec une racine différente de `/volume1/@docker`
- **THEN** la vérification échoue avec un message qui nomme la racine trouvée et l'attendue

#### Scenario: Tout est en place
- **WHEN** tous les éléments critiques sont présents
- **THEN** la vérification réussit et affiche un résumé par élément
