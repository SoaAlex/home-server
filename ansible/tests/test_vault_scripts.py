"""Tests of the vault scripts with fake .env files and a fake ansible-vault.

  python3 -m unittest discover -s ansible/tests -v

The fake ansible-vault "encrypts" by hex-encoding behind the real header, so
round trips can be checked byte for byte without a vault password.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import importlib.util  # noqa: E402

import nas_vault as nv  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vault_import = _load("vault-import-env")

FAKE_VAULT = r'''
import sys
args = sys.argv[1:]
data = sys.stdin.buffer.read()
head = b"$ANSIBLE_VAULT;1.1;AES256\n"
if args[0] == "encrypt":
    sys.stdout.buffer.write(head + data.hex().encode() + b"\n")
elif args[0] == "view":
    assert data.startswith(head)
    sys.stdout.buffer.write(bytes.fromhex(data[len(head):].strip().decode()))
'''

# Values that must never show up in any output.
SECRETS = ["s3cr3t-PG-value", "tok{{en}}-with-jinja", "quote\"and\\backslash", "restic-pw-xyz", "rclone-token-abc"]


class VaultImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        fake = root / "fake_vault.py"
        fake.write_text(FAKE_VAULT)
        os.environ["NAS_VAULT_CMD"] = f"{sys.executable} {fake}"
        self.vault = root / "vault.yml"
        self.originals = {}
        for i, rel in enumerate(nv.ENV_FILES):
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # Tricky bytes: no final newline, CRLF, tabs, comments, Jinja.
            content = f"# comment\nPASSWORD={SECRETS[i % 3]}\r\nOTHER=\tx\nEXPORTED_ONLY=1"
            if i % 2:
                content += "\n\n"
            p.write_bytes(content.encode())
            self.originals[rel] = content.encode()
        (self.repo / "maison/.env.example").write_text("PASSWORD=\nOTHER=\nEXAMPLE_ONLY=\n")
        self.restic = root / "restic.pass"
        self.restic.write_text(SECRETS[3] + "\n")
        self.rclone = root / "rclone.conf"
        self.rclone.write_text(f"[gdrive]\ntoken = {SECRETS[4]}\n")

    def tearDown(self):
        os.environ.pop("NAS_VAULT_CMD", None)
        self.tmp.cleanup()

    def run_import(self, *extra_args) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = vault_import.main([
                "--repo", str(self.repo), "--vault-file", str(self.vault),
                "--extra", f"vault_restic_password={self.restic}",
                "--extra", f"vault_rclone_conf={self.rclone}", *extra_args])
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_output_has_names_but_no_values(self):
        output = self.run_import()
        for secret in SECRETS:
            self.assertNotIn(secret, output)
        self.assertIn("common/.env", output)
        self.assertIn("EXPORTED_ONLY: in maison/.env but not in its .env.example", output)
        self.assertIn("EXAMPLE_ONLY: in .env.example but missing from maison/.env", output)

    def test_vault_file_is_encrypted(self):
        self.run_import()
        raw = self.vault.read_bytes()
        self.assertTrue(raw.startswith(b"$ANSIBLE_VAULT;"))
        for secret in SECRETS:
            self.assertNotIn(secret.encode(), raw)

    def test_round_trip_is_byte_identical(self):
        self.run_import()
        data = nv.read_vault(self.vault)
        for rel, original in self.originals.items():
            self.assertEqual(data["vault_env_files"][rel].encode(), original, rel)
        self.assertEqual(data["vault_restic_password"], SECRETS[3] + "\n")

    def test_values_are_tagged_unsafe(self):
        text = nv.render_vault_yaml({"vault_x": "a{{b}}", "vault_env_files": {"a/.env": "K={{v}}\n"}})
        self.assertIn('vault_x: !unsafe "a{{b}}"', text)
        self.assertIn('"a/.env": !unsafe "K={{v}}\\n"', text)

    def test_reimport_keeps_other_entries(self):
        self.run_import()
        data = nv.read_vault(self.vault)
        data["vault_cloudflare_api_token"] = "cf-kept"
        nv.write_vault(data, self.vault)
        self.run_import()
        self.assertEqual(nv.read_vault(self.vault)["vault_cloudflare_api_token"], "cf-kept")

    def test_dry_run_writes_nothing(self):
        output = self.run_import("--dry-run")
        self.assertFalse(self.vault.exists())
        self.assertIn("dry run", output)

    def test_refuses_plaintext_vault(self):
        self.vault.write_text("vault_x: plain\n")
        with self.assertRaises(SystemExit):
            nv.read_vault(self.vault)


class VaultSetTest(unittest.TestCase):
    def test_generate_prints_no_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_vault.py"
            fake.write_text(FAKE_VAULT)
            vault = Path(tmp) / "vault.yml"
            env = {**os.environ, "NAS_VAULT_CMD": f"{sys.executable} {fake}"}
            proc = subprocess.run([sys.executable, str(SCRIPTS / "vault-set.py"), "vault_tf_pg_password",
                                   "--generate", "--vault-file", str(vault)],
                                  capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            os.environ["NAS_VAULT_CMD"] = env["NAS_VAULT_CMD"]
            try:
                value = nv.read_vault(vault)["vault_tf_pg_password"]
            finally:
                os.environ.pop("NAS_VAULT_CMD")
            self.assertEqual(len(value), 64)
            self.assertNotIn(value, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
