# Design

## Context

Voir `proposal.md` pour la motivation. État observé le 2026-11-03 :

- **Hôte** : UGOS sur Debian 12 (x86_64), Docker 29.6.2 et Compose v5.1.3 fournis par l'app Docker d'UGOS (démarré par `docker_serv`, pas par `docker.service`). `/usr/bin/python3` = 3.11 ; le `python3` du PATH utilisateur est celui de Linuxbrew (3.15). `sudo` demande un mot de passe. `/volume1`, `/volume2` et `/home` sont montés par un service UGOS, pas par fstab.
- **Repo** `/volume1/docker` (public, GitHub `SoaAlex/home-server`) : stacks `common`, `home-server`, `teslamate`, `maison` (compose + `.env` git-ignoré), `eurl-frais` (CLI ponctuel), `backup/` (nas-backup : restic, unités systemd utilisateur), `boot-check/` (nas-boot-check + 2 fichiers root), `hermes-vault/` (config Hermes chiffrée avec age), `home-server/scripts/macvlan-shim.{sh,service}`. Le repo contient souvent des commits locaux non poussés et du travail non suivi d'autres sessions Hermes.
- **Déjà en place sur l'hôte** : `daemon.json` (`data-root` `/volume1/@docker`), `macvlan-shim.service` + `/usr/local/bin/macvlan-shim.sh`, drop-in sshd `00-tcp-forwarding.conf`, drop-in `tailscaled.service.d/10-unset-godebug.conf`, unités utilisateur `hermes-gateway`, `hermes-dashboard`, `nas-backup*` (+ linger). **Jamais installés** : `/etc/systemd/system/user@1000.service.d/10-wait-mounts.conf` et `nas-boot-check.service` (posés depuis, le 2026-11-08 à 09:38, identiques au repo et activés : le premier `--check` des rôles hôte est donc à zéro écart). Crontab root : il ne reste que la copie `cp -r` de 02:00.
- **`~/.local/bin`** : `nas-backup` et `nas-boot-check` (wrappers sh non versionnés), `maison` (= `maison/scripts/maison_cli.py`), `eurl-frais` (diffère de `eurl-frais/scripts/eurl-frais`), `mrun` et `dk` (absents du repo), restic/rclone/age (installés par un script de skill), plus des liens posés par Hermes, Claude Code et Codex (non gérés ici).
- **8 fichiers `.env`** : `common`, `home-server`, `home-server/aiostreams`, `home-server/aiometadata`, `teslamate`, `maison`, `eurl-frais`, `backup`. Autres secrets : `~/.config/nas-backup/restic.pass`, `~/.config/rclone/rclone.conf`, `~/.hermes/.env` et `auth.json`.
- **Bord public** : DNS Cloudflare de `soaalex.com` (enregistrements proxifiés, DDNS sur l'IP publique), 6 proxy hosts NPM (listés dans `REBUILD.md` §9) avec certificats Let's Encrypt HTTP-01 ; NPM vise les services par `192.168.1.55:<port>`.
- **Racine OpenSpec** : le change vit dans `home-server/openspec/` (non suivi par git), mais le code ira à la racine du repo (`/volume1/docker/ansible`, `/volume1/docker/terraform`), hors de `home-server/`.

## Goals / Non-Goals

**Goals:**

- Adopter le NAS actuel tel quel : le code décrit l'existant, la première exécution réelle ne pose que les deux fichiers root manquants et les écarts corrigés volontairement.
- Une seule source de vérité par sujet : compose pour les services, fichiers du repo pour les unités et scripts, vault pour les secrets, Terraform pour le bord public.
- Reconstruction d'un NAS neuf en une suite de commandes courte, testable par étapes.

**Non-Goals:**

- Installer ou mettre à jour Docker, UGOS, ou piloter l'API UGOS (RAID, partages, utilisateurs, tâches de synchro et de sauvegarde, apps, VM).
- Gérer la configuration des applications elles-mêmes (Pi-hole, Frigate, Grafana) au-delà des fichiers déjà suivis par git.
- Gérer les wrappers et liens de `~/.local/bin` installés par Hermes, Claude Code ou Codex.
- Un pipeline CI qui applique quoi que ce soit au NAS.

## Decisions

### D1. Arborescence

```
/volume1/docker/
├── ansible/
│   ├── ansible.cfg, requirements.yml, Dockerfile, run          # run = lanceur conteneur sur le NAS
│   ├── inventories/nas/hosts.yml
│   ├── inventories/nas/group_vars/nas/{main.yml,vault.yml}     # vault.yml chiffré
│   ├── playbooks/{site.yml,restore.yml,verify.yml}
│   ├── roles/{preflight,repo,docker_host,network_shim,ssh,systemd_boot,user_tools,
│   │          backup_units,secrets,stacks,hermes,verify,restore}
│   └── scripts/{vault-import-env.py,check-vault-encrypted.sh}
└── terraform/
    ├── tf                                                      # lanceur : identifiants depuis le vault
    ├── cloudflare/   (DNS soaalex.com)
    └── npm/          (proxy hosts + certificats)
```

Un dossier par outil à la racine, comme les autres unités du repo. Alternative écartée : `infra/` unique (un niveau de plus sans bénéfice).

Racine OpenSpec (décision d'Alexandre, 2026-11-07) : `home-server/openspec/` a été déplacé à la racine du repo (`/volume1/docker/openspec/`) et y est suivi par git, comme `maison/openspec/`. `maison/` garde sa propre racine (la plus proche).

### D2. Les compose restent la source de vérité

Le rôle `stacks` boucle sur une liste ordonnée (`common`, `home-server`, `teslamate`, `maison`) et appelle `community.docker.docker_compose_v2` avec `project_src` = dossier de la stack, `pull: never` (ou `missing`), `build: never` sauf image absente, `recreate: auto`. Le module pilote la CLI `docker compose` (pas de SDK Python sur l'hôte) et gère le mode vérification par `--dry-run`. `eurl-frais` et `backup` : construction d'image seulement (`docker_image_build` ou `docker compose build`), aucun `up`.

Alternatives écartées : traduire les compose en tâches `docker_container` (duplication, dérive garantie, contraire à la demande de conserver les compose) ; générer les compose depuis des templates Jinja (le compose ne serait plus lisible ni utilisable seul).

Préconditions par stack (`.env` présent, dossiers de données critiques non vides) vérifiées avant `up` ; `allow_empty_data=true` pour un démarrage à vide volontaire.

**Résultat du spike (tâche 1.4, 2026-11-07) : repli retenu.** `docker_compose_v2` (community.docker 5.4.0, Compose 5.1.3) sur `common` en `--check` : rien n'a été recréé (mêmes ID et heures de démarrage), mais le module rapporte `changed` avec `Recreate` pour `common-postgres` et `common-redis`. Ce n'est pas le module : `docker compose up -d --dry-run` dit la même chose, alors que sur un projet jetable inchangé il dit `Running`. Cause : l'app Docker d'UGOS (`docker_serv`) a une **mise à jour automatique planifiée chaque nuit à 04:00** (journal dans `/volume1/@appstore/com.ugreen.docker/db/docker_info_log.db`, table `docker_log`) ; elle tire les images des projets qu'elle connaît (`common`, `home-server`, `teslamate`, `qbittorrent`, table `compose`) et recrée les conteneurs en posant `com.docker.compose.image` = un digest de manifeste, pas l'ID d'image que Compose attend. Le `config-hash` et l'image réelle du conteneur restent pourtant identiques à ceux du repo. Toute commande `docker compose up` sur ces stacks recréerait donc ces conteneurs une fois de plus, sans rien changer.

Le rôle `stacks` détecte donc lui-même les écarts, par service, en lecture seule (`ansible/files/compose_drift.py`) : conteneur absent, arrêté avec une politique de redémarrage, image ≠ image locale de son tag, ou configuration changée. Pour la configuration (corrigé le 2026-11-08 après un faux positif sur `aiostreams`) : si le conteneur a été créé par Compose (label `com.docker.compose.image` = ID de son image), c'est `docker compose up --dry-run` qui décide, seul contrôle exact, car le hash que `up` écrit pour un service à `env_file` n'est pas reproductible avec `config --hash` ; si UGOS l'a recréé, on compare `config-hash` à `config --hash`, valable seulement sans `env_file` et pour la même version de Compose, sinon le service est signalé « non comparable » et laissé tel quel (`COMPOSE_DRIFT_STRICT=1` pour le compter). Seuls les services en écart passent à `docker compose up -d --no-deps --pull never <services>`. En `--check`, la liste et la raison sont affichées sans rien lancer. Les images ne sont tirées qu'avec `stacks_pull=true` (`docker compose pull`), puis la même comparaison décide des recréations.

### D3. Les fichiers de l'hôte viennent du clone du NAS

Les fichiers déjà suivis par le repo (shim, unités `backup/systemd`, `boot-check/systemd`, wrappers) sont copiés avec `remote_src: true` depuis `/volume1/docker` sur le NAS, pas depuis le clone de la machine de contrôle. Raison : le clone du NAS a souvent des commits locaux non poussés ; un Mac en retard réinstallerait d'anciennes versions. Le rôle `preflight` compare le commit du contrôleur et celui du NAS et avertit s'ils diffèrent (les playbooks eux-mêmes viennent du contrôleur).

Le rôle `repo` clone `https://github.com/SoaAlex/home-server` dans `/volume1/docker` seulement s'il n'y a pas de `.git` ; sinon il ne fait jamais `pull`, `reset` ni `checkout` et signale juste « en avance / modifié ».

Permissions (décision d'Alexandre, 2026-11-08) : UGOS laisse `/volume1/docker` en 777 ; tout compte ou conteneur local pouvait donc modifier ce que root installe ou exécute (unités, shim, playbooks). Seuls root et `soaalex` doivent pouvoir écrire le code : `go-w` appliqué à la main le 2026-11-08 sur les fichiers suivis, leurs dossiers, `.git`, `ansible/` et `openspec/` (modes d'avant dans `~/.local/state/nas-ansible/docker-modes-before-*.txt`). Les dossiers de données ne sont pas touchés (des conteneurs d'autres uid y écrivent). La racine `/volume1/docker` (`root:root` 777) demande sudo. Le rôle `repo` maintient cet état à chaque exécution.

### D4. Secrets : un vault, contenu brut des `.env`

`group_vars/nas/vault.yml` (chiffré, versionné) contient :

- `vault_env_files` : dictionnaire `chemin relatif → contenu brut` des 8 `.env`. Contenu brut plutôt que variable par variable : l'aller-retour est identique à l'octet par construction, et un `.env` modifié à la main n'exige pas de modifier Ansible. Le rôle `secrets` vérifie les clés requises (celles du `.env.example` de la stack) par analyse du contenu, sans l'afficher.
- `vault_restic_password`, `vault_rclone_conf` (posé seulement si absent : rclone réécrit son jeton), `vault_cloudflare_api_token`, `vault_npm_admin_{email,password}`, `vault_tf_pg_password`, et en option `vault_become_password`.
- Hors vault : `~/.hermes/.env` et `auth.json` (Hermes les réécrit lui-même, ils reviennent par restic).

Toutes les tâches concernées : `no_log: true`, `diff: false`, mode 600, `1000:10`.

Mot de passe du vault : aléatoire d'au moins 128 bits (`openssl rand -base64 32`), gardé dans le gestionnaire de mots de passe, fourni par `--ask-vault-pass` ou par un fichier git-ignoré `~/.config/nas-ansible/vault-pass` (600). Raison : Ansible Vault dérive sa clé avec PBKDF2-SHA256 à 10 000 itérations seulement ; dans un repo public, seul un mot de passe à forte entropie empêche une attaque hors ligne. Alternative écartée : age, comme `hermes-vault` (KDF plus robuste, mais intégration Ansible maison) ; SOPS (dépendance de plus). Le contrôle `check-vault-encrypted.sh` (en-tête `$ANSIBLE_VAULT;`) s'ajoute à `prepush_audit.py`.

`vault-import-env.py` lit les `.env` du NAS, écrit le YAML dans un fichier temporaire 600, le chiffre avec `ansible-vault encrypt`, supprime le clair, et n'imprime que les noms.

### D5. Deux façons de lancer, mêmes playbooks

- **Mac** : `ansible-core` épinglé (pipx ou uv) + `ansible-galaxy collection install -r requirements.yml`, SSH vers `soaalex@192.168.1.55`, `-K` pour sudo.
- **NAS** : `ansible/run <args>` construit l'image `nas-ansible:<version>` (python slim + ansible-core + collections + terraform + age, versions et sommes épinglées), la lance en `--network host` avec le repo monté et une clé dédiée `~/.ssh/nas_ansible` (ajoutée à `authorized_keys`), et se connecte à `127.0.0.1` en SSH. On ne peut pas utiliser `connection: local` dans un conteneur : il configurerait le conteneur.

Inventaire : `ansible_python_interpreter: /usr/bin/python3` (le Python Debian, pas Linuxbrew). Le chemin Mac est celui d'un NAS neuf (aucun repo sur le NAS) ; le chemin NAS sert à la convergence courante.

### D6. Opérations root sûres sur un hôte en service

- `daemon.json` : comparé ; si différent et des conteneurs tournent, échec sauf `allow_docker_restart=true`. Unité confirmée le 2026-11-07 (tâche 1.6) : `dockerd` tourne sous `docker.service` (`/lib/systemd/system/docker.service`, `disabled`, `TriggeredBy=docker.socket`) ; `docker_serv.service` (UGOS, `enabled`, `ExecStart=/var/targets/docker_serv`, `Wants=docker.socket`) est le gestionnaire de l'app Docker d'UGOS et démarre 3 s après `dockerd`. Le gestionnaire de redémarrage relance donc `docker.service` seul (`systemctl restart docker`), sans toucher à `docker_serv` ni changer l'état `disabled` (c'est le socket qui l'active au démarrage).
- Drop-in sshd : `block`/`rescue` : écriture avec sauvegarde, `sshd -t`, rechargement seulement si valide, sinon restauration.
- Fichiers root écrits avec `backup: true` (retour arrière manuel possible).
- Tailscale : `systemd: name=tailscaled enabled=false state=stopped`, sans `masked` (UGOS peut s'en servir pour décider de l'afficher ; à revoir si une mise à jour le réactive).
- Crontab root : `cron` module en `state: absent` sur les lignes héritées, sous `cleanup_legacy_cron=true` seulement (en phase avec le nettoyage prévu après une semaine verte de restic).

### D7. Unités systemd utilisateur

Copiées depuis le repo (`backup/systemd/*`, unités Hermes depuis la config restaurée), `systemd_service` en `scope: user` avec `XDG_RUNTIME_DIR=/run/user/1000`, linger via `loginctl enable-linger` (root). Les unités `hermes-*.service` sont générées par Hermes (`hermes gateway install`) : Ansible ne les écrit pas, il les active seulement quand elles existent et que `~/.hermes/.env` est présent.

### D8. Hermes

Ordre de préférence pour la configuration : (1) restauration restic de `~/.hermes` (complète : secrets et historique compris, voir `restore.yml`) ; (2) sinon `hermes-vault/` (config, skills, mémoires, jobs ; pas de secrets). Pour (2), le déchiffrement age se fait sur le contrôleur (`delegate_to: localhost`) avec `hermes_age_identity=<chemin local>` ; le contrôleur décompresse aussi le xz (ni le NAS ni le conteneur n'ont `xz`), envoie un `.tar` simple que le NAS dépaquette, puis tout le clair est supprimé des deux côtés : depuis le Mac, la clé ne touche jamais le NAS. Copie par `cp -a` : `~/.local/bin` n'est jamais écrasé (les wrappers viennent du repo), le reste seulement avec `hermes_restore_overwrite=true`. Rien n'est déchiffré en `--check`. Installation du code par la méthode officielle de la doc Hermes, seulement si l'exécutable est absent.

### D9. Terraform

- Deux racines indépendantes, `terraform/cloudflare` et `terraform/npm` : un NPM arrêté ne bloque pas un `plan` DNS, et inversement.
- Providers épinglés : `cloudflare/cloudflare` 5.x (`cloudflare_dns_record`), `Sander0542/nginxproxymanager` (le plus utilisé). Un spike valide d'abord ce provider sur une copie de NPM : `advanced_config` sur la ressource proxy host, ressource de certificat Let's Encrypt, import par identifiant, compatibilité avec la version de NPM en service. Repli si un point manque : provider `home-devops/nginxproxymanager`, ou certificats laissés à la restauration des données NPM (exigence « certificats » remplie par restic).
- Import : `cf-terraforming generate` + blocs `import {}` pour Cloudflare ; IDs numériques lus dans `database.sqlite` de NPM (copie en lecture seule) pour NPM. Blocs `import` retirés après le premier `apply` sans changement.
- DDNS : `lifecycle { ignore_changes = [content] }` sur les enregistrements dont l'adresse suit l'IP publique.
- **État** : backend `pg` dans `common-postgres` (base `terraform`, rôle dédié, un schéma par racine). Accessible depuis le Mac (port 5000 publié sur le LAN/VPN) comme depuis le conteneur du NAS, verrouillage natif, sauvegardé chaque nuit par le `pg_dump` de nas-backup. Alternatives écartées : état local (lié à une machine) ; dépôt git (secrets possibles dans l'état, repo public) ; état chiffré OpenTofu (oblige à changer d'outil).
- `terraform/tf <racine> <commande>` : lit les identifiants avec `ansible-vault view`, les exporte (`CLOUDFLARE_API_TOKEN`, `NGINX_PROXY_MANAGER_*`, `PG_CONN_STR`) dans l'environnement du seul processus terraform, sans les écrire sur disque.
- Token Cloudflare : `Zone.DNS Edit` + `Zone Read` limités à `soaalex.com`.

### D10. Restauration

`restore.yml` passe par `nas-backup restic|restic-offsite` (même image, même mot de passe), restaure dans `/volume2/@nas-backup/restore-test/out` puis recopie en place via un conteneur root (`cp -a`, propriétaires et modes conservés), comme `backup/README.md` l'indique déjà. Par défaut : liste seule. `restore_confirm=yes` pour écrire, `restore_overwrite=yes` pour écraser un chemin non vide (services concernés arrêtés avant). Postgres : démarre `common` seul, `globals.sql` puis `pg_restore -C` par base, comptage des tables. `restore_rehearsal=yes` : préfixe de test + Postgres jetable `--network none`, rien d'arrêté, tout supprimé à la fin.

Ordre sur un NAS neuf : `site.yml --tags preflight,repo,docker_host,user_tools,secrets` → `restore.yml` → `site.yml` complet → `terraform/tf cloudflare apply` → `terraform/tf npm apply`.

### D11. Vérification

`ansible-lint` + `yamllint` + `--syntax-check` dans l'image de contrôle ; `terraform fmt -check` et `validate`. Validation réelle : `--check --diff` sur le NAS, puis deux exécutions réelles (la deuxième à `changed=0`), `plan` sans changement, répétition de `restore.yml`. Pas de Molecule : un conteneur Debian ne reproduit ni UGOS, ni les montages, ni Docker-dans-l'hôte ; le NAS lui-même en mode vérification est le banc de test le plus fidèle.

## Risks / Trade-offs

- [Vault public attaquable hors ligne] → mot de passe aléatoire ≥ 128 bits, jamais mémorable ; contrôle d'en-tête avant chaque push ; rotation = `ansible-vault rekey`.
- [Compatibilité `docker_compose_v2` avec Compose v5] → spike fait (1.4) : check mode inutilisable à cause des labels posés par la mise à jour auto d'UGOS ; détection par service décrite en D2.
- [Mise à jour automatique d'UGOS à 04:00] → elle tire et recrée des conteneurs hors d'Ansible (dont `common-postgres`, presque chaque nuit). `nas-backup.timer` déplacé de 04:00 à 04:30 le 2026-11-08 (décision d'Alexandre) pour ne plus tomber pendant ces recréations ; le rôle `stacks` reste idempotent.
- [Provider NPM communautaire incomplet ou abandonné] → spike avant engagement, version épinglée, repli décrit en D9.
- [Le mode vérification ment sur les tâches `command`/`shell`] → `check_mode: false` uniquement pour les lectures, `changed_when` explicite partout ; les écritures root passent par des modules (`copy`, `template`, `systemd`, `cron`).
- [Mise à jour UGOS qui écrase des fichiers root] → nas-boot-check les surveille déjà ; relancer `site.yml --tags host` suffit.
- [Commit du contrôleur différent de celui du NAS] → avertissement `preflight` + copie depuis le clone du NAS (D3).
- [Backend Terraform dans le Postgres qu'on reconstruit] → Terraform passe en dernier, après restauration des dumps ; si l'état est perdu, réimport avec les blocs `import`.
- [Dérive révélée à l'adoption, ex. wrapper `eurl-frais`] → on aligne le repo sur la version en service (celle utilisée par le cron), après diff, avant toute exécution réelle.

## Migration Plan

1. Spikes : `docker_compose_v2` en `--check` sur `common` ; provider NPM sur une copie de NPM.
2. Squelette `ansible/` + image de contrôle ; import du vault depuis les `.env` du NAS ; mot de passe du vault enregistré par Alexandre.
3. Rôles un par un, chacun validé en `--check --diff` (zéro écart attendu, sauf les deux fichiers root jamais posés).
4. Rapatrier `mrun`, `dk`, les wrappers `nas-backup`/`nas-boot-check` dans le repo et réaligner `eurl-frais`.
5. Première exécution réelle (avec accord) : pose `10-wait-mounts.conf` et `nas-boot-check.service` ; deuxième exécution à `changed=0`.
6. Terraform : import Cloudflare puis NPM, `plan` sans changement.
7. Répétition `restore.yml` sur le NAS en service.
8. Réécriture de `REBUILD.md` et mise à jour de `README.md` ; commit local, push seulement avec l'accord d'Alexandre après `prepush_audit.py`.

Retour arrière : supprimer `ansible/` et `terraform/` ; les fichiers root modifiés ont une sauvegarde `backup: true` ; l'état Terraform peut être abandonné (`DROP SCHEMA`) sans toucher aux ressources.

## Open Questions

- Qui fait la DDNS (la Livebox, un conteneur, Cloudflare) et quels enregistrements elle met à jour : à relever pendant l'import, ne change que la liste des `ignore_changes`.
- ~~Commande d'installation de Hermes~~ : résolu (tâche 5.1, doc « Installation ») : `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive` (le rôle télécharge le script puis le lance). Attention : sur un checkout existant ce script **met à jour** vers la dernière stable ; le rôle ne le lance que si ni `~/.local/bin/hermes` ni `~/.hermes/hermes-agent` n'existent (incident du 2026-11-08 : un test avec un HOME factice l'a lancé et a fait passer Hermes de v0.22.5 à v0.23.0). Unités : `hermes gateway install --if-missing` crée `hermes-gateway.service` ; `hermes-dashboard.service` est écrite à la main (clone de l'unité gateway, `hermes dashboard --host 0.0.0.0 --port 9119 --no-open --skip-build`) et revient par `hermes-vault/` (`systemd-user/`).
- ~~Nom de l'unité qui démarre Docker sous UGOS~~ : résolu, voir D6 (`docker.service` via `docker.socket`, `docker_serv` = gestionnaire UGOS).
