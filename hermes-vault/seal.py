#!/usr/bin/env python3
"""Seal the Hermes agent configuration into age-encrypted archives kept in this public repo.

Two archives, so that the often-changing one stays small in git history:
  hermes-config.tar.xz.age   ~/.hermes: config.yaml, SOUL.md, channel_directory.json, memories/*.md,
                             skills/ (without the .hub/ download cache), cron/jobs.json;
                             ~/.local/bin helper scripts and symlinks (not downloaded ELF binaries);
                             ~/.config/systemd/user hermes-*.service units and their links
  hermes-plugins.tar.xz.age  ~/.hermes/plugins/ (code of the installed plugins, changes rarely)
What stays out: .env, auth.json, mcp-tokens/, shared/, any key/token/secret-named file, state.db,
sessions/, logs/, caches and the Hermes code. The restic backups cover those.

Encryption: age, post-quantum hybrid recipient (ML-KEM-768 + X25519) read from recipient.txt.
The NAS only needs that PUBLIC recipient; the identity (private key) is kept off the NAS
(password manager + paper) and is only needed to restore or to run --verify.

usage:
  python3 -I seal.py                       re-seal each archive whose content changed
  python3 -I seal.py --force               re-seal both
  python3 -I seal.py --verify IDENTITY     decrypt both archives and compare them with the live files
Restore:  age -d -i IDENTITY hermes-config.tar.xz.age | tar -xJ -C <dir>   (same for plugins)
Exit: 0 ok / unchanged, 1 verify mismatch, other = error.
"""
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
RECIPIENT = os.path.join(HERE, "recipient.txt")
STATE_DIR = os.path.join(HOME, ".local", "state", "hermes-vault")
AGE = shutil.which("age") or os.path.join(HOME, ".local", "bin", "age")
HEADER = b"age-encryption.org/v1\n-> mlkem768x25519 "
H = os.path.join(HOME, ".hermes")

SKIP_PARTS = {".hub", ".locks", "__pycache__", ".git", "node_modules", ".venv", "venv", ".pytest_cache"}
SECRET_NAME = re.compile(r"(^\.env($|\.)|\.env$|auth\.json$|token|secret|credential|\.pem$|\.key$|^id_(rsa|ed25519|ecdsa))", re.I)


def walk(prefix, root, rel):
    base = os.path.join(root, rel.rstrip("/"))
    if not os.path.lexists(base):
        return
    if not rel.endswith("/"):
        yield f"{prefix}/{rel}", base
        return
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_PARTS)
        for f in sorted(files):
            yield f"{prefix}/{os.path.relpath(os.path.join(d, f), root)}", os.path.join(d, f)


def config_entries():
    for rel in ["config.yaml", "SOUL.md", "channel_directory.json", "memories/MEMORY.md",
                "memories/USER.md", "skills/", "cron/jobs.json"]:
        yield from walk("hermes", H, rel)
    bindir = os.path.join(HOME, ".local", "bin")
    for f in sorted(os.listdir(bindir)):
        p = os.path.join(bindir, f)
        if os.path.islink(p):
            yield f"local-bin/{f}", p
        elif os.path.isfile(p) and os.path.getsize(p) < 1 << 20:
            with open(p, "rb") as fh:
                if fh.read(4) != b"\x7fELF":
                    yield f"local-bin/{f}", p
    unitdir = os.path.join(HOME, ".config", "systemd", "user")
    for d, _, files in os.walk(unitdir):
        for f in sorted(files):
            if f.startswith("hermes-") and f.endswith(".service"):
                p = os.path.join(d, f)
                yield f"systemd-user/{os.path.relpath(p, unitdir)}", p


def plugin_entries():
    yield from walk("hermes", H, "plugins/")


BUNDLES = {"hermes-config": config_entries, "hermes-plugins": plugin_entries}


def collect(name):
    keep, skipped = [], []
    for arc, p in BUNDLES[name]():
        base = os.path.basename(arc)
        (skipped if base.endswith((".lock", ".pyc")) or SECRET_NAME.search(base) else keep).append((arc, p))
    return sorted(set(keep)), skipped


def manifest(entries):
    rows = []
    for arc, p in entries:
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            rows.append([arc, "link", os.readlink(p)])
        else:
            with open(p, "rb") as fh:
                rows.append([arc, oct(stat.S_IMODE(st.st_mode)), hashlib.sha256(fh.read()).hexdigest()])
    return rows


def build_tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for arc, p in entries:
            ti = tar.gettarinfo(p, arc)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if ti.isreg():
                with open(p, "rb") as fh:
                    tar.addfile(ti, fh)
            else:
                tar.addfile(ti)
    return lzma.compress(buf.getvalue(), preset=9)


def seal(name, force):
    archive = os.path.join(HERE, f"{name}.tar.xz.age")
    state = os.path.join(STATE_DIR, f"{name}.sha256")
    entries, skipped = collect(name)
    rows = manifest(entries)
    digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    last = open(state).read().strip() if os.path.exists(state) else ""
    if digest == last and os.path.exists(archive) and not force:
        print(f"{name}: unchanged ({len(rows)} files)")
        return False
    tmp = archive + ".tmp"
    subprocess.run([AGE, "-R", RECIPIENT, "-o", tmp], input=build_tar(entries), check=True)
    with open(tmp, "rb") as fh:
        head = fh.read(len(HEADER))
    if head != HEADER:
        os.unlink(tmp)
        raise SystemExit(f"{name}: refusing, output is not an age file for a post-quantum recipient")
    os.replace(tmp, archive)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state, "w") as fh:
        fh.write(digest + "\n")
    raw = sum(os.path.getsize(p) for _, p in entries if not os.path.islink(p))
    print(f"{name}: sealed {len(rows)} files ({raw / 1e6:.1f} MB raw) -> {os.path.getsize(archive) / 1e6:.2f} MB;"
          f" skipped by name rule: {len(skipped)}")
    return True


def verify(name, identity):
    archive = os.path.join(HERE, f"{name}.tar.xz.age")
    entries, _ = collect(name)
    live = {r[0]: r for r in manifest(entries)}
    plain = subprocess.run([AGE, "-d", "-i", identity, archive], capture_output=True, check=True).stdout
    archived = {}
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(plain)), mode="r:") as tar:
        for m in tar.getmembers():
            if m.issym():
                archived[m.name] = [m.name, "link", m.linkname]
            else:
                fh = tar.extractfile(m)
                archived[m.name] = [m.name, oct(m.mode & 0o7777), hashlib.sha256(fh.read() if fh else b"").hexdigest()]
    differ = sorted(k for k in archived if k in live and archived[k] != live[k])
    new = sorted(set(live) - set(archived))
    gone = sorted(set(archived) - set(live))
    print(f"{name}: {len(archived)} files in the archive, {len(live)} live; "
          f"{len(differ)} differ, {len(new)} new since sealing, {len(gone)} gone since sealing")
    for label, items in (("differs", differ), ("new", new), ("gone", gone)):
        for k in items[:10]:
            print(f"  {label}: {k}")
    return not (differ or new or gone)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--verify":
        return 0 if all([verify(n, sys.argv[2]) for n in BUNDLES]) else 1
    force = "--force" in sys.argv[1:]
    for n in BUNDLES:
        seal(n, force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
