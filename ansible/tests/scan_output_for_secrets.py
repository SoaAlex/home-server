#!/usr/bin/env python3
"""Scan a captured Ansible output for any secret value of the NAS.

  python3 -I ansible/tests/scan_output_for_secrets.py <output-file>

Values come from the vault (decrypted in memory) and the live .env files;
only counts are printed. Exit 1 if any value (>= 6 chars) is found.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nas_vault as nv  # noqa: E402


def values() -> set[str]:
    data = nv.read_vault()
    out: set[str] = set()
    texts = list(data.get("vault_env_files", {}).values())
    for rel in nv.ENV_FILES:
        p = nv.REPO / rel
        if p.is_file():
            texts.append(p.read_text(errors="replace"))
    for text in texts:
        for m in re.finditer(r"(?m)^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=(.*)$", text):
            out.add(m.group(1).strip().strip("'\""))
    for key, val in data.items():
        if isinstance(val, str):
            out.add(val.strip())
            out.update(re.findall(r"=\s*(\S{6,})", val))  # rclone.conf lines
    # Non-secret values that legitimately appear in output: numbers, paths,
    # and user/database names equal to a stack directory or the NAS login
    # (POSTGRES_DB=teslamate shows up in every "teslamate/.env" path).
    names = {part for rel in nv.ENV_FILES for part in Path(rel).parts[:-1]} | {Path.home().name}
    return {v for v in out
            if len(v) >= 6 and v not in names
            and not re.fullmatch(r"[\d.:/ -]+|true|false|production|info", v)}


def main() -> int:
    text = Path(sys.argv[1]).read_text(errors="replace")
    secrets = values()
    hits = sum(1 for v in secrets if v in text)
    print(f"output: {len(text)} chars, secret values checked: {len(secrets)}, found: {hits}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
