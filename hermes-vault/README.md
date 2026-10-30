# hermes-vault

Encrypted copy of the configuration of the Hermes agent that runs on this NAS, so that it can be
rebuilt from this repo after losing the NAS. The archives are encrypted with
[age](https://age-encryption.org) (≥ 1.3) to a post-quantum hybrid recipient (ML-KEM-768 + X25519):
GitHub and anyone cloning the repo only see opaque files.

| File | Contents |
|---|---|
| `hermes-config.tar.xz.age` | `~/.hermes`: `config.yaml`, `SOUL.md`, `channel_directory.json`, `memories/`, `skills/` (without the `.hub/` download cache), `cron/jobs.json`; the helper scripts of `~/.local/bin`; `~/.config/systemd/user/hermes-*.service` |
| `hermes-plugins.tar.xz.age` | `~/.hermes/plugins/` (kept apart because it rarely changes) |
| `recipient.txt` | the public key the NAS encrypts to (safe to publish) |
| `seal.py` | rebuilds an archive only when its content changed |

**Not in here:** secrets (`.env`, `auth.json`, MCP tokens), the conversation history (`state.db`,
`sessions/`), logs, caches and the Hermes code. Secrets and history live in the restic backups;
the code is reinstalled.

The private key (`AGE-SECRET-KEY-PQ-1…`) is **not** kept on the NAS: it lives in the password
manager and on paper. Without it these files cannot be decrypted.

## Update

```bash
python3 -I hermes-vault/seal.py              # re-seals only the archives whose content changed
git add hermes-vault/*.age && git commit -m "hermes-vault: update"
```

## Restore on a new machine

1. Install Hermes (<https://hermes-agent.nousresearch.com/docs>) and `age` ≥ 1.3.
2. Write the private key to a file only you can read: `identity.txt`, `chmod 600`.
3. Decrypt into a scratch folder, then copy into place:

   ```bash
   mkdir -p ~/hermes-restore
   for f in hermes-vault/*.tar.xz.age; do age -d -i identity.txt "$f" | tar -xJ -C ~/hermes-restore; done
   # hermes/ -> ~/.hermes/   local-bin/ -> ~/.local/bin/   systemd-user/ -> ~/.config/systemd/user/
   ```

4. Restore `~/.hermes/.env` and `~/.hermes/auth.json` from the restic backup (or recreate them), then
   `systemctl --user daemon-reload && systemctl --user enable --now hermes-gateway hermes-dashboard`.
5. `python3 -I hermes-vault/seal.py --verify identity.txt` compares the archives with the live files.
