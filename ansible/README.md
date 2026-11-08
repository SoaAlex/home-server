# Ansible: NAS rebuild and convergence

This directory holds the Ansible code that brings the NAS to the state the
repo describes: host configuration, secrets, and (later) Docker stacks and the
Hermes agent. You can run it from a Mac over SSH or from a container on the
NAS itself, with the same playbooks and inventory.

The `docker-compose.yaml` files stay the source of truth for services. Ansible
deploys them; it never rewrites them.

## Requirements

The NAS needs only what UGOS already provides. Ansible checks these items and
never installs them:

- Docker 24.0 or later with Compose 2.20 or later, from the UGOS Docker app.
- `/volume1`, `/volume2`, and `/home` mounted.
- `/usr/bin/python3` (Debian's Python, not the Linuxbrew one on the user
  `PATH`).

The vault password lives in `~/.config/nas-ansible/vault-pass` (mode 600) and
in the owner's password manager. It's never committed.

## Run from the NAS (control container)

The `run` script builds an image with Ansible, Terraform, and age (every
version and checksum pinned), then starts it on the host network. The
container connects back to the host over SSH on `127.0.0.1`, so the playbooks
configure the host, not the container.

To set it up once, create a dedicated key and allow it from localhost only:

```bash
ssh-keygen -q -t ed25519 -N '' -C nas-ansible -f ~/.ssh/nas_ansible
printf 'from="127.0.0.1,::1",no-agent-forwarding,no-X11-forwarding,no-port-forwarding %s\n' \
  "$(cat ~/.ssh/nas_ansible.pub)" >> ~/.ssh/authorized_keys
```

Then run any Ansible command through it:

```bash
cd /volume1/docker/ansible
./run ansible nas -m ping
./run ansible-playbook playbooks/site.yml --check --diff
```

The image tag is a hash of `Dockerfile`, `requirements.txt`, and
`requirements.yml`, so editing one of them rebuilds the image on the next run.
`./run --build` rebuilds it without running anything.

## Run from a Mac

A Mac is the only option on a fresh NAS, before the repo exists there. Install
the pinned tools, then point the inventory at the NAS:

```bash
cd home-server/ansible   # your clone
uv venv && uv pip install --require-hashes -r requirements.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml
ANSIBLE_CONFIG=ansible.cfg .venv/bin/ansible-playbook playbooks/site.yml \
  --ask-vault-pass --check --diff
```

## Secrets

Secrets are in `inventories/nas/group_vars/nas/vault.yml`, an Ansible Vault
file committed encrypted to this public repo. Each `.env` is stored as its raw
content, so the file written on the NAS is byte-identical to the original.

Use these scripts to manage the vault. They print names only, never values:

| Command | What it does |
| --- | --- |
| `scripts/vault-import-env.py --dry-run` | Lists the `.env` files and checks their keys against `.env.example`. |
| `scripts/vault-import-env.py` | Imports the 8 `.env` files, `restic.pass`, and `rclone.conf` into the vault. |
| `scripts/vault-set.py --list` | Lists the vault entries. |
| `scripts/vault-set.py vault_<name>` | Sets one entry from a hidden prompt. |
| `scripts/vault-set.py vault_<name> --generate` | Stores a random `token_hex(32)`. |
| `scripts/check-vault-encrypted.sh --tree` | Fails if a vault file isn't encrypted (also `--staged`, `--range A..B`). |

The vault holds the sudo password (`vault_become_password`), so the playbooks
don't need `-K`.

<!-- prettier-ignore -->
> [!WARNING]
> The vault is public ciphertext. Its password must stay random (at least
> 128 bits, `openssl rand -base64 32`). Rotate it with `ansible-vault rekey`.

## Playbook and tags

`playbooks/site.yml` runs every role in order. `--check --diff` reports each
difference without changing anything; files copied from the repo show a
`diff -u`, and secret files show only their path.

| Tag | Role | What it manages | Guardrails and variables |
| --- | --- | --- | --- |
| (always) | `preflight` | Checks Docker/Compose versions, mounts, and the vault; warns when the controller clone is at another commit than the NAS clone. | Read-only. `preflight_controller_repo` overrides the controller path. |
| `repo` | `repo` | Clones the repo only when `/volume1/docker/.git` is missing; removes group/other write from code paths. | Never pulls, resets, or checks out. Data directories are left alone. |
| `docker_host` | `docker_host` | `/etc/docker/daemon.json` (`data-root` on the SSD). | Fails when it differs and containers run, unless `-e allow_docker_restart=true`. |
| `network_shim` | `network_shim` | Pi-hole macvlan shim script and unit; checks the route through `ph-shim`. | A changed shim waits for the next boot; it's never restarted. |
| `ssh` | `ssh` | Drop-in `AllowTcpForwarding local`. | Validated with `sshd -t` before reload; restored if invalid. |
| `systemd_boot` | `systemd_boot` | `user@1000` wait-for-mounts drop-in, `nas-boot-check.service`, linger, Tailscale stopped and disabled. | `nas-boot-check` is enabled, not started. |
| `user_tools` | `user_tools` | Wrappers in `~/.local/bin` from the repo; restic, rclone, and age at pinned versions. | Downloads only when the version differs; a wrong SHA-256 fails before the binary is touched. |
| `backup_units` | `backup_units` | `nas-backup*` user units and their timers. | Copies the repo files unchanged. |
| `legacy_cron` | `legacy_cron` | Removes the root cron lines listed in `legacy_cron_lines`. | Does nothing without `-e cleanup_legacy_cron=true`. |
| `secrets` | `secrets` | Writes each `.env` from the vault, `1000:10`, mode 600. | Fails before writing when a key of `.env.example` is missing; `no_log` everywhere. |

`--tags host` runs every host role (everything except `secrets`).

For example, to see what cleaning the root crontab would change:

```bash
./run ansible-playbook playbooks/site.yml --check --diff --tags legacy_cron \
  -e cleanup_legacy_cron=true
```

## Tests

The vault scripts have unit tests with fake `.env` files; the
secret scanner checks a captured playbook output against every real value:

```bash
/usr/bin/python3 -m unittest discover -s tests
./run ansible-playbook playbooks/site.yml --check --diff -vvv > "${TMPDIR:-/tmp}/check.txt" 2>&1
/usr/bin/python3 -I tests/scan_output_for_secrets.py "${TMPDIR:-/tmp}/check.txt"
```
