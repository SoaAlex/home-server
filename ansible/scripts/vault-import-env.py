#!/usr/bin/env python3
"""Fill the Ansible Vault from the secrets already on the NAS.

Reads the 8 stack .env files (raw content), ~/.config/nas-backup/restic.pass
and ~/.config/rclone/rclone.conf, merges them into
ansible/inventories/nas/group_vars/nas/vault.yml (other entries kept) and
encrypts it. Prints only file and variable names, never a value.

  ansible/scripts/vault-import-env.py            # import / refresh
  ansible/scripts/vault-import-env.py --dry-run  # names and key checks only

Vault password: ~/.config/nas-ansible/vault-pass (or ANSIBLE_VAULT_PASSWORD_FILE).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nas_vault as nv  # noqa: E402

HOME = Path.home()
EXTRA_FILES = {
    "vault_restic_password": HOME / ".config/nas-backup/restic.pass",
    "vault_rclone_conf": HOME / ".config/rclone/rclone.conf",
}


def collect(repo: Path, extra: dict[str, Path]) -> tuple[dict, list[str]]:
    """Return the vault entries and the report lines (names only)."""
    env_files: dict[str, str] = {}
    report: list[str] = []
    for rel in nv.ENV_FILES:
        path = repo / rel
        if not path.is_file():
            report.append(f"MISSING  {rel}")
            continue
        text = path.read_bytes().decode("utf-8")
        env_files[rel] = text
        keys = nv.env_keys(text)
        report.append(f"read     {rel} ({len(keys)} variables)")
        example = path.with_name(".env.example")
        if not example.is_file():
            report.append(f"  no .env.example next to {rel}")
            continue
        ex_keys = set(nv.env_keys(example.read_text()))
        for k in sorted(set(keys) - ex_keys):
            report.append(f"  {k}: in {rel} but not in its .env.example")
        for k in sorted(ex_keys - set(keys)):
            report.append(f"  {k}: in .env.example but missing from {rel}")
    entries: dict = {"vault_env_files": env_files}
    for name, path in extra.items():
        if path.is_file():
            entries[name] = path.read_bytes().decode("utf-8")
            report.append(f"read     {path.name} -> {name}")
        else:
            report.append(f"MISSING  {path} ({name})")
    return entries, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=nv.REPO)
    ap.add_argument("--vault-file", type=Path, default=nv.VAULT_FILE)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--extra", action="append", default=[], metavar="NAME=PATH",
                    help="override an extra secret file (tests)")
    args = ap.parse_args(argv)

    extra = dict(EXTRA_FILES)
    for item in args.extra:
        name, _, path = item.partition("=")
        extra[name] = Path(path)

    entries, report = collect(args.repo, extra)
    print("\n".join(report))
    if any(line.startswith("MISSING") for line in report):
        print("Some files are missing: the vault keeps its previous value for them.")
    if args.dry_run:
        print("dry run: vault not written")
        return 0

    data = nv.read_vault(args.vault_file)
    previous_env = data.get("vault_env_files", {})
    data["vault_env_files"] = {**previous_env, **entries.pop("vault_env_files")}
    data.update(entries)
    nv.write_vault(data, args.vault_file)
    print(f"wrote {args.vault_file} (encrypted; entries: {', '.join(sorted(data))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
