# Spec Delta

## Purpose

Installe l'agent Hermes sur le NAS et restaure sa configuration depuis l'archive chiffrée `hermes-vault/`, pour que la passerelle Discord, le tableau de bord et les jobs planifiés reviennent après une reconstruction.

## ADDED Requirements

### Requirement: Installation de Hermes si absent
Le playbook SHALL installer Hermes pour l'utilisateur du NAS quand il est absent, avec la méthode d'installation officielle, et ne SHALL ni réinstaller ni mettre à jour une installation existante.

#### Scenario: Hermes déjà installé
- **WHEN** l'exécutable Hermes existe déjà
- **THEN** le playbook ne lance aucune installation ni mise à jour

### Requirement: Restauration depuis hermes-vault
Quand l'utilisateur fournit le chemin de sa clé age à l'exécution, le playbook SHALL déchiffrer les archives de `hermes-vault/` et restaurer `~/.hermes`, `~/.local/bin` et les unités `hermes-*.service`. La clé ne SHALL jamais être copiée, écrite ni conservée sur le NAS au-delà de l'exécution.

#### Scenario: Clé fournie sur un NAS neuf
- **WHEN** Hermes n'a pas de configuration et la clé est fournie
- **THEN** la configuration, les skills, les mémoires et les jobs sont restaurés, et aucun fichier contenant la clé ne reste sur le NAS à la fin

#### Scenario: Clé non fournie
- **WHEN** aucune clé n'est fournie
- **THEN** l'étape de restauration est sautée avec un message, et le reste du playbook continue

#### Scenario: Configuration existante
- **WHEN** `~/.hermes/config.yaml` existe déjà
- **THEN** la restauration n'écrase rien sans la variable explicite d'écrasement

### Requirement: Services Hermes actifs
Le playbook SHALL activer et démarrer `hermes-gateway` et `hermes-dashboard` en services utilisateur quand leur configuration et leurs secrets (`~/.hermes/.env`) sont présents, et SHALL signaler sans échouer quand les secrets manquent encore.

#### Scenario: Secrets restaurés
- **WHEN** `~/.hermes/.env` existe
- **THEN** les deux services sont actifs et activés au démarrage

#### Scenario: Secrets absents
- **WHEN** `~/.hermes/.env` est absent
- **THEN** les services ne sont pas démarrés et la sortie indique de restaurer les secrets depuis restic
