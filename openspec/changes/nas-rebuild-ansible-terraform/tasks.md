# Tasks

Règles pour toute la mise en œuvre : rien n'est appliqué en réel sur le NAS sans l'accord d'Alexandre (le mode `--check --diff` est toujours permis) ; aucune valeur de secret dans une sortie ; commits locaux en `SoaAlex`, chemins explicites dans `git add`, jamais de push sans accord (voir `scripts/prepush_audit.py` du skill home-server-stack).

## 1. Préparation et spikes

- [x] 1.1 Décider avec Alexandre de la racine OpenSpec (déplacer `home-server/openspec/` à la racine du repo ou garder tel quel) et vérifier `openspec status --change nas-rebuild-ansible-terraform` depuis la racine retenue
- [x] 1.2 Créer `ansible/` (`ansible.cfg`, `requirements.yml` avec `community.docker`, `community.general`, `ansible.posix` épinglés, `inventories/nas/hosts.yml` avec `ansible_python_interpreter: /usr/bin/python3`) et vérifier `ansible-inventory --graph` et `ansible nas -m ping`
- [x] 1.3 Écrire `ansible/Dockerfile` (python slim, ansible-core, collections, terraform, age, openssh-client ; versions et sommes SHA-256 épinglées) et `ansible/run` (réseau host, repo monté, clé `~/.ssh/nas_ansible`, cible `127.0.0.1`) ; vérifier `ansible/run ansible nas -m ping` depuis le NAS
- [x] 1.4 Spike : playbook jetable `docker_compose_v2` sur `common` en `--check --diff` avec Compose v5.1.3 ; vérifier « ok », aucun conteneur recréé, et noter le résultat dans `design.md` (sinon basculer sur le repli `docker compose` + hash de config)
- [ ] 1.5 Spike : provider `Sander0542/nginxproxymanager` contre une copie jetable de NPM (conteneur sur un port libre, `data/` copié) ; vérifier lecture des 6 proxy hosts, `advanced_config`, ressource de certificat et import ; consigner le choix (provider retenu ou repli) dans `design.md`
- [x] 1.6 Confirmer l'unité qui démarre Docker sous UGOS (`docker_serv.service` ou autre) via `systemctl status` et la consigner dans `design.md`
- [x] 1.7 Ajouter à `.gitignore` : `ansible/.vault-pass*`, `**/.terraform/`, `*.tfstate*`, `*.tfvars` hors exemples ; vérifier avec `git check-ignore` sur un fichier de chaque type

## 2. Secrets (Ansible Vault)

- [x] 2.1 Écrire `ansible/scripts/vault-import-env.py` (lit les 8 `.env`, `restic.pass`, `rclone.conf` ; écrit un YAML 600 temporaire, le chiffre, supprime le clair ; n'imprime que les noms et signale les clés absentes des `.env.example`) avec un test unitaire sur des `.env` factices ; vérifier que la sortie du test ne contient aucune valeur
- [ ] 2.2 Faire générer par Alexandre le mot de passe du vault (`openssl rand -base64 32`), l'enregistrer dans son gestionnaire et dans `~/.config/nas-ansible/vault-pass` (600) ; lancer l'import, compléter les entrées Cloudflare, NPM, rôle Postgres Terraform ; vérifier que `vault.yml` commence par `$ANSIBLE_VAULT;`
- [x] 2.3 Écrire `ansible/scripts/check-vault-encrypted.sh` et l'appeler depuis `prepush_audit.py` ; vérifier qu'il refuse un `vault.yml` déchiffré mis en staging et accepte le fichier chiffré
- [x] 2.4 Rôle `secrets` : écriture des `.env` (mode 600, `1000:10`, `no_log`, `diff: false`) avec contrôle des clés requises ; vérifier en `--check --diff` zéro changement sur les 8 fichiers et qu'aucun contenu n'apparaît même avec `-vvv`
- [x] 2.5 Test d'échec : retirer une clé requise dans une copie de travail du vault et vérifier que le rôle échoue en nommant stack et variable, sans valeur

## 3. Configuration de l'hôte

- [x] 3.1 Rôle `preflight` : version de Docker et de Compose, montages `/volume1` `/volume2` `/home`, commit du contrôleur comparé à celui du NAS, vault déverrouillé ; vérifier l'avertissement en lançant depuis un clone à un autre commit
- [x] 3.2 Rôle `repo` : clone seulement si `/volume1/docker/.git` est absent, sinon simple rapport (en avance / modifié) ; garantir `go-w` sur les fichiers suivis, leurs dossiers, `.git`, `ansible/`, `openspec/` et la racine `/volume1/docker` (dossiers de données exclus) ; vérifier sur le NAS `changed=0` et, dans un dossier vide de test, un clone propriétaire `1000:10`
- [x] 3.3 Rôle `docker_host` : `daemon.json` avec garde `allow_docker_restart`, sans installer Docker ; vérifier `--check` sans écart, puis sur une copie modifiée l'échec explicite sans redémarrage
- [x] 3.4 Rôle `network_shim` : `/usr/local/bin/macvlan-shim.sh` et `macvlan-shim.service` depuis `home-server/scripts/` (`remote_src`) ; vérifier zéro écart et `ip route get 192.168.1.100` via `ph-shim`
- [x] 3.5 Rôle `ssh` : drop-in `00-tcp-forwarding.conf` en `block`/`rescue` avec `sshd -t` ; vérifier zéro écart puis `sshd -T | grep allowtcpforwarding` = `local`
- [x] 3.6 Rôle `systemd_boot` : `user@1000.service.d/10-wait-mounts.conf`, `nas-boot-check.service` (depuis `boot-check/systemd/`), linger, Tailscale arrêté et désactivé ; vérifier en `--check` que seuls les deux fichiers jamais posés apparaissent comme changements
- [x] 3.7 Rapatrier dans le repo les wrappers `nas-backup`, `nas-boot-check` (à côté de leur outil), `mrun` (`maison/scripts/`), `dk` (`ansible/files/`), et aligner `eurl-frais/scripts/eurl-frais` sur la version en service après diff ; vérifier que `md5sum` de chaque source égale celui de `~/.local/bin`
- [x] 3.8 Rôle `user_tools` : wrappers depuis le repo, restic/rclone/age épinglés avec SHA-256 (versions actuelles 0.19.1 / 1.75.2 / 1.3.2), installation sautée si la version est déjà là ; vérifier zéro écart, puis l'échec avec une somme volontairement fausse sans toucher au binaire en place
- [x] 3.9 Rôle `backup_units` : unités `backup/systemd/*` vers `~/.config/systemd/user/`, timers activés ; vérifier zéro écart et les 3 timers dans `systemctl --user list-timers`
- [x] 3.10 Option `cleanup_legacy_cron` (retire seulement les lignes héritées listées dans `group_vars`) ; vérifier en `--check` qu'aucune tâche cron ne s'exécute sans la variable et qu'avec elle seule la ligne `cp -r` de 02:00 serait retirée
- [x] 3.11 Documenter chaque rôle (variables, tags, garde-fous) dans `ansible/README.md` et vérifier que chaque commande citée tourne telle quelle en `--check`

## 4. Stacks Docker

- [x] 4.1 Rôle `stacks` : liste ordonnée `common` → `home-server` → `teslamate` → `maison`, vérification de `common-network` avant les stacks qui le déclarent `external`, `docker_compose_v2` avec `pull: never` par défaut et `stacks_pull=true` pour mettre à jour ; vérifier en `--check --diff` zéro conteneur recréé
- [x] 4.2 Préconditions : `.env` présent et dossiers critiques non vides (`vw-data`, `wireguard/config`, `nginx-proxy-manager/data`), contournement `allow_empty_data` ; vérifier le refus avec une variable pointant vers un dossier vide de test
- [x] 4.3 Images locales : construire `maison`, `eurl-frais` et `nas-backup` seulement si absentes, aucun `up` pour `eurl-frais` ; vérifier `changed=0` avec les images présentes et `docker ps` sans conteneur `eurl-frais`
- [x] 4.4 Exclusions : `qbittorrent-app-1`, conteneurs `hermes-*` jamais touchés ; vérifier que les rapports `--check` ne les mentionnent pas
- [x] 4.5 Documenter dans `ansible/README.md` l'ajout d'un service (compose seul, rien dans Ansible) et vérifier avec un service factice ajouté dans un compose de test que le rôle le démarre sans modification d'Ansible

## 5. Agent Hermes

- [x] 5.1 Relever dans la doc Hermes la commande d'installation et de génération des unités, et la consigner dans `design.md`
- [x] 5.2 Rôle `hermes` : installation si l'exécutable est absent, rien sinon ; vérifier `changed=0` sur le NAS
- [ ] 5.3 Restauration depuis `hermes-vault/` : déchiffrement sur le contrôleur (`hermes_age_identity`), copie, dépaquetage, suppression du clair, pas d'écrasement sans `hermes_restore_overwrite` ; vérifier en répétition vers un `HOME` de test que les fichiers égalent ceux de `seal.py --verify` et qu'aucun fichier de clé ni archive en clair ne reste sur le NAS
- [x] 5.4 Activation de `hermes-gateway` et `hermes-dashboard` seulement si les unités et `~/.hermes/.env` existent, message sinon ; vérifier zéro écart sur le NAS et le message avec un `.env` absent (HOME de test)
- [x] 5.5 Mettre à jour `hermes-vault/README.md` pour renvoyer vers le rôle et vérifier que les commandes citées fonctionnent

## 6. Vérification de fin de convergence et adoption

- [ ] 6.1 Rôle `verify` (aussi `playbooks/verify.yml`) : racine Docker, volumes nommés, conteneurs avec politique de redémarrage en marche, route shim, timers, services Hermes ; vérifier qu'il passe sur le NAS et échoue avec un message clair sur une attente volontairement fausse
- [ ] 6.2 `site.yml` complet en `--check --diff` sur le NAS ; corriger le repo pour chaque écart non voulu jusqu'à n'avoir que les deux fichiers root attendus ; joindre le récapitulatif à la demande d'accord
- [ ] 6.3 Avec l'accord d'Alexandre : exécution réelle de `site.yml` (`-K`), puis deuxième exécution ; vérifier `changed=0 failed=0` à la deuxième et un `nas-boot-check run` vert
- [ ] 6.4 Lancer la même convergence depuis le conteneur (`ansible/run`) et, si Alexandre le fait, depuis son Mac ; vérifier `changed=0` dans les deux cas

## 7. Terraform : DNS Cloudflare

- [ ] 7.1 Créer la base `terraform`, son rôle et le schéma `cloudflare` dans `common-postgres` (mot de passe `secrets.token_hex`, jamais affiché, stocké dans le vault) ; vérifier la connexion avec le rôle
- [ ] 7.2 Écrire `terraform/tf` (identifiants depuis `ansible-vault view`, uniquement dans l'environnement de terraform) ; vérifier que `ps e` et le disque n'en gardent aucune trace après exécution
- [ ] 7.3 Faire créer par Alexandre un token Cloudflare limité à `Zone.DNS Edit` + `Zone Read` sur `soaalex.com`, l'ajouter au vault ; vérifier qu'une lecture hors DNS est refusée
- [ ] 7.4 `terraform/cloudflare` : provider 5.x épinglé, backend `pg`, ressources générées par `cf-terraforming` + blocs `import`, `ignore_changes = [content]` sur les enregistrements DDNS identifiés ; vérifier `terraform plan` = « No changes »
- [ ] 7.5 Retirer les blocs `import` après le premier `apply` sans changement, vérifier un nouveau `plan` sans changement et `git status` sans état ni `.terraform/`

## 8. Terraform : proxy hosts NPM

- [ ] 8.1 Schéma `npm` du backend ; identifiants admin NPM dans le vault ; vérifier l'authentification du provider sur le NPM en service (lecture seule)
- [ ] 8.2 `terraform/npm` : 6 proxy hosts (dont `vpn.soaalex.com` avec WebSockets et délais `1d`) et certificats selon le choix du spike 1.5, blocs `import` avec les IDs de `database.sqlite` ; vérifier `plan` = « No changes »
- [ ] 8.3 Répétition sur le NPM jetable du spike, vidé : `apply` recrée les hôtes ; vérifier la configuration générée de `vpn.soaalex.com` (WebSockets, délais) dans ce NPM, puis le supprimer
- [ ] 8.4 Documenter `terraform/README.md` (prérequis, `tf`, import, ordre DNS puis NPM) et vérifier les commandes citées

## 9. Restauration des données

- [ ] 9.1 `restore.yml` en mode liste par défaut (source `local` ou `gdrive`, snapshot, chemins, bases) ; vérifier que sans `restore_confirm` rien n'est écrit
- [ ] 9.2 Fichiers : `nas-backup restic restore` puis recopie en place par conteneur root, refus sur chemin non vide ou service actif sans `restore_overwrite`, arrêt des services concernés sinon ; vérifier le refus avec `bitwarden` en marche
- [ ] 9.3 Postgres : `common` seul, `globals.sql`, `pg_restore -C` par base, comptage des tables comparé au dump ; vérifier en répétition
- [ ] 9.4 Mode `restore_rehearsal` : préfixe de test + Postgres jetable `--network none`, nettoyage final ; vérifier sur le NAS en service qu'aucun conteneur n'a redémarré (`docker ps` avant/après), comptes de tables égaux et propriétaires/modes identiques pour `home-server/wireguard/`
- [ ] 9.5 Documenter `restore.yml` dans `ansible/README.md` et `backup/README.md` ; vérifier les commandes citées en mode liste

## 10. Documentation et intégration

- [ ] 10.1 Réécrire `REBUILD.md` en « Ansible d'abord » : préparation UGOS manuelle, `site.yml` partiel, `restore.yml`, `site.yml` complet, Terraform, vérifications ; annexe manuelle (RAID, partages, utilisateurs, synchro OneDrive, app qBittorrent, VM, Livebox) ; vérifier que chaque commande citée existe et que le fichier ne contient ni secret ni donnée personnelle
- [ ] 10.2 Mettre à jour `README.md` (arborescence, section Ansible/Terraform) selon le style docs-writer ; vérifier le rendu Markdown
- [ ] 10.3 Lint complet dans l'image de contrôle (`ansible-lint`, `yamllint`, `--syntax-check`, `terraform fmt -check`, `terraform validate`) ; vérifier zéro erreur
- [ ] 10.4 `prepush_audit.py` sur la plage de commits du change ; vérifier aucun secret ni donnée personnelle, puis demander à Alexandre l'accord de push

## Workflow follow-up

- Archiver le change (`/openspec-archive-change`) une fois le push validé et la répétition de restauration verte.
- Mettre à jour le skill `home-server-stack` (et `nas-backup` pour `restore.yml`) avec les commandes Ansible/Terraform.
