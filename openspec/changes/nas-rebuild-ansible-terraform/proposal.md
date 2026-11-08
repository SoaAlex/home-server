# Proposal

## Why

Reconstruire le NAS aujourd'hui veut dire dérouler à la main `REBUILD.md` (371 lignes) plus les README de `backup/`, `boot-check/`, `hermes-vault/` et `home-server/wstunnel/` : copies `sudo cp` de fichiers systemd, `daemon.json` à restaurer avant le premier démarrage de Docker, drop-in sshd, outils installés par script, wrappers de `~/.local/bin` dont certains n'existent que sur le NAS (`mrun`, `dk`) ou ont divergé du repo (`eurl-frais`), proxy hosts NPM et DNS Cloudflare recréés à la main dans des UI. Rien n'est rejoué ni vérifié automatiquement, et plusieurs étapes documentées n'ont jamais été appliquées (le drop-in `user@1000` et `nas-boot-check.service` de `boot-check/` ne sont pas installés). Alexandre préfère Ansible et Terraform, plus répandus et qu'il maîtrise, à un empilement de scripts et de procédures.

## What Changes

- **Nouveau dossier `ansible/`** à la racine du repo `/volume1/docker` : inventaire du NAS, rôles, playbook `site.yml` (converge tout l'hôte et les stacks) et playbook `restore.yml` (restauration des données, opt-in explicite). Exécutable depuis le Mac en SSH ou depuis un conteneur Ansible sur le NAS lui-même.
- **Les `docker-compose.yaml` existants restent la source de vérité des services** : Ansible les déploie tels quels (`docker compose up` par stack, dans l'ordre `common` → `home-server` → `teslamate` → `maison`), sans les réécrire ni les dupliquer en YAML Ansible.
- **Secrets dans Ansible Vault**, fichier chiffré versionné dans le repo public : Ansible génère chaque `.env` (mode 600, `1000:10`) depuis le vault. Un script d'import initial remplit le vault depuis les `.env` actuels sans afficher de valeur.
- **Configuration de l'hôte en rôles** : `/etc/docker/daemon.json`, shim macvlan, drop-in sshd `AllowTcpForwarding local`, drop-in `user@1000` et `nas-boot-check.service`, linger, unités systemd utilisateur (Hermes, nas-backup), outils restic/rclone/age épinglés avec checksum, wrappers `~/.local/bin`, Tailscale maintenu arrêté et désactivé.
- **Installation de l'agent Hermes** : installation du code, restauration de la config depuis `hermes-vault/` (clé age fournie à l'exécution, jamais stockée), services `hermes-gateway` et `hermes-dashboard`.
- **Nouveau dossier `terraform/`** : enregistrements DNS Cloudflare de `soaalex.com` et proxy hosts Nginx Proxy Manager (provider communautaire), importés depuis l'existant pour un premier `plan` sans changement. Les enregistrements mis à jour par la DDNS ignorent leur adresse IP.
- **Adoption du NAS actuel sans casse** : le premier `ansible-playbook site.yml --check --diff` sur le NAS en production doit converger vers zéro changement, ou seulement vers des écarts expliqués et corrigés dans le repo. Aucun redémarrage de Docker ou d'un service n'est déclenché sur la machine vivante sans accord explicite.
- **Documentation** : `REBUILD.md` devient le guide « Ansible d'abord » et garde en annexe manuelle ce qui ne s'automatise pas (UGOS : RAID, partages, utilisateurs, synchro OneDrive, app qBittorrent, VM Home Assistant ; box Livebox).
- Hors périmètre : réécrire les compose en rôles Ansible, gérer UGOS lui-même, la box Internet, Cloudflare Access, changer l'architecture réseau ou les services.

## Capabilities

### New Capabilities

- `rebuild-automation` : points d'entrée de la reconstruction (playbooks, exécution depuis le Mac ou un conteneur sur le NAS, ordre complet d'une reconstruction, mode vérification sans changement, documentation).
- `infra-secrets` : gestion des secrets de l'infrastructure dans Ansible Vault (stockage chiffré dans un repo public, import depuis l'existant, génération des `.env`, non-divulgation dans les logs et les diffs).
- `nas-host-config` : état attendu de l'hôte Debian/UGOS hors Docker (démon Docker, réseau macvlan, SSH, unités systemd système et utilisateur, outils, services interdits).
- `docker-stacks` : déploiement des stacks Docker à partir des fichiers compose existants, ordre et réseaux partagés, images construites localement.
- `hermes-agent-provisioning` : installation de l'agent Hermes et restauration de sa configuration depuis `hermes-vault/`.
- `edge-config` : configuration publique gérée par Terraform (DNS Cloudflare, proxy hosts Nginx Proxy Manager) et son état.
- `nas-data-restore` : restauration opt-in des données (restic → bind mounts, volumes Docker, bases Postgres) pilotée par `nas-backup`.

### Modified Capabilities

Aucune (le projet n'a pas encore de spec principale).

## Impact

- **Repo `/volume1/docker` (public)** : nouveaux dossiers `ansible/` et `terraform/`, fichier vault chiffré, wrappers `mrun`/`dk` rapatriés dans le repo, `.gitignore` (état Terraform, mot de passe du vault, `.terraform/`), `REBUILD.md` et `README.md` mis à jour. Les compose, `backup/`, `boot-check/` et `hermes-vault/` ne changent pas de comportement.
- **NAS** : installe des fichiers root (drop-in `user@1000`, `nas-boot-check.service`) jamais posés jusqu'ici ; le reste doit déjà correspondre à l'état décrit. Exécution root via `sudo` avec mot de passe (`become`), pas de sudo sans mot de passe.
- **Dépendances** : `ansible-core` + collections `community.docker`, `community.general`, `ansible.posix` (version épinglée) ; Terraform ≥ 1.8 avec les providers `cloudflare/cloudflare` v5 et `Sander0542/nginxproxymanager` ; image Docker du conteneur de contrôle construite localement.
- **Comptes externes** : un token API Cloudflare (DNS lecture/écriture sur `soaalex.com`) et les identifiants admin NPM, stockés dans le vault. Le mot de passe du vault vit dans le gestionnaire de mots de passe d'Alexandre, pas sur GitHub.
- **Risques** : provider NPM communautaire (couverture de `advanced_config` et des certificats à vérifier avant de s'y engager) ; un vault public reste attaquable hors ligne si son mot de passe est faible ; dérive entre le live et le repo révélée au premier `--check`.
