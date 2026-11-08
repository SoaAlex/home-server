#!/usr/bin/env python3
"""Set one entry of the Ansible Vault without the value ever being echoed.

  ansible/scripts/vault-set.py vault_become_password          # hidden prompt, typed twice
  ansible/scripts/vault-set.py vault_tf_pg_password --generate # random token_hex(32)
  ansible/scripts/vault-set.py --list                         # entry names only

The value is read with getpass (or generated), piped to ansible-vault and
never printed.
"""
from __future__ import annotations

import argparse
import getpass
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nas_vault as nv  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="entry name, must start with vault_")
    ap.add_argument("--generate", action="store_true", help="store a random secrets.token_hex(32)")
    ap.add_argument("--keep", action="store_true", help="do nothing if the entry already exists")
    ap.add_argument("--list", action="store_true", help="print entry names and exit")
    ap.add_argument("--vault-file", type=Path, default=nv.VAULT_FILE)
    args = ap.parse_args(argv)

    data = nv.read_vault(args.vault_file)
    if args.list:
        for key in sorted(data):
            extra = f" ({len(data[key])} files)" if isinstance(data[key], dict) else ""
            print(key + extra)
        return 0
    if not args.name or not re.fullmatch(r"vault_[a-z0-9_]+", args.name):
        ap.error("give an entry name like vault_become_password")
    if args.keep and args.name in data:
        print(f"{args.name} already set: kept")
        return 0

    if args.generate:
        value = secrets.token_hex(32)
    else:
        value = getpass.getpass(f"{args.name}: ")
        if value != getpass.getpass("again: "):
            print("values differ: nothing written", file=sys.stderr)
            return 1
        if not value:
            print("empty value: nothing written", file=sys.stderr)
            return 1
    existed = args.name in data
    data[args.name] = value
    nv.write_vault(data, args.vault_file)
    print(f"{args.name} {'updated' if existed else 'set'} in {args.vault_file.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
