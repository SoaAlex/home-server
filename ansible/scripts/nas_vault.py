"""Shared helpers for the NAS Ansible Vault (group_vars/nas/vault.yml).

Values never reach stdout/stderr: plaintext only travels through pipes to
`ansible-vault`, and only names are printed. Stdlib only, so the scripts run
with the host's /usr/bin/python3 as well as inside the control container.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VAULT_FILE = REPO / "ansible/inventories/nas/group_vars/nas/vault.yml"
VAULT_HEADER = b"$ANSIBLE_VAULT;"

# The 8 .env files of the repo, relative to the repo root.
ENV_FILES = [
    "common/.env",
    "home-server/.env",
    "home-server/aiostreams/.env",
    "home-server/aiometadata/.env",
    "teslamate/.env",
    "maison/.env",
    "eurl-frais/.env",
    "backup/.env",
]

KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)


def env_keys(text: str) -> list[str]:
    """Variable names defined in a .env text (comments ignored)."""
    return KEY_RE.findall(text)


def vault_cmd() -> list[str]:
    """ansible-vault in PATH (control container, Mac), else through ansible/run."""
    override = os.environ.get("NAS_VAULT_CMD")
    if override:
        return override.split()
    if shutil.which("ansible-vault"):
        return ["ansible-vault"]
    return [str(REPO / "ansible/run"), "ansible-vault"]


def _run(args: list[str], data: bytes | None) -> bytes:
    proc = subprocess.run(vault_cmd() + args, input=data, capture_output=True)
    if proc.returncode != 0:
        # ansible-vault errors name files and causes, never plaintext.
        raise SystemExit(f"ansible-vault {args[0]} failed (rc {proc.returncode}): "
                         + proc.stderr.decode(errors="replace").strip()[-400:])
    return proc.stdout


def read_vault(path: Path = VAULT_FILE) -> dict:
    """Decrypted vault as a dict ({} if the file does not exist yet)."""
    if not path.exists():
        return {}
    raw = path.read_bytes()
    if not raw.startswith(VAULT_HEADER):
        raise SystemExit(f"{path} is not encrypted: refusing to use it.")
    plain = _run(["view", "-"], raw)
    return parse_vault_yaml(plain.decode())


def parse_vault_yaml(text: str) -> dict:
    """Parse the YAML written by render_vault_yaml (JSON-compatible subset)."""
    try:
        import yaml  # available in the control container

        class _Loader(yaml.SafeLoader):
            pass

        _Loader.add_constructor("!unsafe", lambda l, n: l.construct_scalar(n))
        return yaml.load(text, Loader=_Loader) or {}
    except ImportError:
        pass
    # Fallback without PyYAML: our own format is one `key: <json>` per line.
    out: dict = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(": ")
        out[key] = json.loads(value.replace("!unsafe ", ""))
    return out


def _unsafe(value):
    """!unsafe stops Ansible from templating `{{` found inside a secret."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {_unsafe(v)}" for k, v in sorted(value.items())) + "}"
    return "!unsafe " + json.dumps(value, ensure_ascii=False)


def render_vault_yaml(data: dict) -> str:
    """One `key: value` line per entry; JSON strings are valid YAML and keep
    every byte (trailing newlines, tabs, quotes) exactly."""
    lines = ["# NAS secrets. Edit with ansible/scripts/vault-set.py or vault-import-env.py."]
    for key in sorted(data):
        lines.append(f"{key}: {_unsafe(data[key])}")
    return "\n".join(lines) + "\n"


def write_vault(data: dict, path: Path = VAULT_FILE) -> None:
    """Encrypt through a pipe (no plaintext on disk) and replace atomically."""
    enc = _run(["encrypt", "--output", "-", "-"], render_vault_yaml(data).encode())
    if not enc.startswith(VAULT_HEADER):
        raise SystemExit("ansible-vault did not return an encrypted payload: nothing written.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vault.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(enc)
        os.chmod(tmp, 0o644)  # ciphertext, versioned in git
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
