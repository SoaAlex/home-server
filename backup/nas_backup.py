#!/usr/bin/python3
"""NAS backup: nightly encrypted, versioned copy of the NAS service state to a restic repository on the RAID.

Runs as the NAS user from systemd user timers (see systemd/); everything that needs root (Docker volumes,
root-owned data, the repository itself) happens in throw-away containers built from this folder.

  run            nightly: Postgres dumps (one .dump per database, kept 7 days in plain sight),
                 consistent SQLite copies, restic snapshot; Discord alert on any failure or warning
  weekly         retention + prune, integrity check (1/8 of the data, rotating), restore test, Discord recap
  restore-test   restore the latest snapshot's databases and files into throw-away containers, compare with live
  status         last runs
  alert-test     send a test message to the Discord webhook
  restic ARGS    any restic command on the repository (e.g. snapshots, ls latest /volume1/docker, stats)
What is saved, where, and how to restore: README.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
BASE = Path("/volume2/@nas-backup")
REPO, CACHE, STAGING, SCRATCH = BASE / "restic", BASE / "cache", BASE / "staging", BASE / "restore-test"
DUMPS = Path("/volume2/NAS-STORAGE/TechnicalBackups/postgres")
PASSFILE = HOME / ".config/nas-backup/restic.pass"
RESTIC_BIN = HOME / ".local/bin/restic"
STATE = HOME / ".local/state/nas-backup"
HISTORY = STATE / "history.jsonl"
IMAGE = "nas-backup:latest"
PG_CONTAINER = "common-postgres"
PG_TEST = "nas-backup-pgtest"
HOST = "nas-soaalex"
TZ = "Europe/Paris"
KEEP_DUMP_DAYS = 7
RETENTION = ["--keep-daily", "14", "--keep-weekly", "8", "--keep-monthly", "12"]
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")

# SQLite databases written by containers: copied with SQLite's online backup, run as the file's owner
# (so that a -wal/-shm file created on the way never changes owner under the application).
SQLITE_DBS = {
    "vaultwarden": "/volume1/docker/home-server/vw-data/db.sqlite3",
    "nginx-proxy-manager": "/volume1/docker/home-server/nginx-proxy-manager/data/database.sqlite",
    "pihole-gravity": "/volume1/docker/home-server/pihole/pihole/gravity.db",
    "grafana": "/volume1/@docker/volumes/teslamate_teslamate-grafana-data/_data/grafana.db",
}
HERMES = HOME / ".hermes"
HERMES_SKIP = {"tools", "hermes-agent", "cache", "installs", "lsp", "node_modules", "__pycache__", ".git"}
# Restore test: folders restored from the snapshot and compared byte for byte with the live ones
FILE_CHECK_DIRS = ["/volume1/docker/home-server/vw-data", "/volume1/docker/home-server/wireguard/config"]

# Read-only binds of the backup container (host path, container path); paths.txt lists what is saved
SOURCES = [
    ("/volume1/docker", "/volume1/docker"),
    ("/volume1/@docker/volumes", "/volume1/@docker/volumes"),
    ("/volume1/@kvm", "/volume1/@kvm"),
    (str(DUMPS), str(DUMPS)),
    (str(STAGING), str(STAGING)),
    (str(HOME), str(HOME)),
    ("/etc", "/hostfs/etc"),
    ("/var/spool/cron", "/hostfs/var/spool/cron"),
]

COUNT_SQL = (
    "select format('select %L, count(*) from %I.%I', n.nspname || '.' || c.relname, n.nspname, c.relname) "
    "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
    "where c.relkind in ('r', 'p') and n.nspname not in ('pg_catalog', 'information_schema') "
    "and n.nspname not like 'pg_toast%' order by 1 \\gexec\n"
)


class StepError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def human(n) -> str:
    n = float(n or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(n) < 1024 or unit == "To":
            return (f"{n:.0f} {unit}" if unit in ("o", "Ko") else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024
    return str(n)


def fr_date(day: str) -> str:
    return dt.date.fromisoformat(day).strftime("%d/%m")


def run(cmd, *, timeout=3600, check=True, **kw):
    """subprocess.run with a timeout; on failure raise StepError carrying the end of stderr."""
    if "stderr" not in kw and "capture_output" not in kw:
        kw["stderr"] = subprocess.PIPE
    try:
        p = subprocess.run([str(c) for c in cmd], timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise StepError(f"timeout after {timeout}s: {' '.join(map(str, cmd[:6]))}") from e
    if check and p.returncode != 0:
        err = p.stderr.decode(errors="replace") if isinstance(p.stderr, bytes) else (p.stderr or "")
        raise StepError(f"exit {p.returncode} ({' '.join(map(str, cmd[:6]))}): {err.strip()[-500:]}")
    return p


def env_file() -> dict:
    out = {}
    path = HERE / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip("'\"")
    return out


def discord(text: str) -> bool:
    if os.environ.get("NAS_BACKUP_NO_ALERT"):  # manual test runs
        log("alert suppressed (NAS_BACKUP_NO_ALERT):\n" + text)
        return True
    url = env_file().get("DISCORD_WEBHOOK_URL", "")
    if not url.startswith("https://"):
        log("alert not sent: no DISCORD_WEBHOOK_URL in backup/.env")
        return False
    body = json.dumps({"content": text[:1990], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                          "User-Agent": "nas-backup/1.0"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception as e:  # never log the URL: it is a secret
        log(f"alert not sent: {type(e).__name__}")
        return False


def ensure_image() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        log(f"building {IMAGE}")
        run(["docker", "build", "-q", "-t", IMAGE, HERE], capture_output=True, timeout=1200)


def box(shell: str, *args, binds=(), user=None) -> list:
    """Command running `shell` in a throw-away container of the backup image (no network)."""
    cmd = ["docker", "run", "--rm", "--network", "none"]
    if user:
        cmd += ["--user", user]
    for b in binds:
        cmd += ["-v", b]
    return cmd + [IMAGE, "sh", "-c", shell, "sh", *args]


def restic_cmd(args, *, sources=False, binds=(), name=None, shell=None) -> list:
    cmd = ["docker", "run", "--rm", "--network", "none", "--hostname", HOST,
           "-e", "RESTIC_REPOSITORY=/repo", "-e", "RESTIC_PASSWORD_FILE=/run/secrets/restic.pass",
           "-e", "RESTIC_CACHE_DIR=/cache", "-e", f"TZ={TZ}",
           "-v", f"{REPO}:/repo", "-v", f"{CACHE}:/cache",
           "-v", f"{PASSFILE}:/run/secrets/restic.pass:ro",
           "-v", f"{RESTIC_BIN}:/usr/local/bin/restic:ro",
           "-v", f"{HERE}:/run/nas-backup:ro"]
    if name:
        cmd += ["--name", name]
    if sources:
        for src, dst in SOURCES:
            cmd += ["-v", f"{src}:{dst}:ro"]
    for b in binds:
        cmd += ["-v", b]
    cmd.append(IMAGE)
    if shell:
        return cmd + ["sh", "-c", shell, "sh", *args]
    return cmd + ["restic", *args]


def json_lines(text: str):
    for line in text.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def check_sqlite(path: Path) -> int:
    """quick_check + at least one table; returns the table count. The copy is never modified: immutable."""
    con = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    try:
        if con.execute("pragma quick_check").fetchone()[0] != "ok":
            raise StepError(f"{path.name}: quick_check failed")
        n = con.execute("select count(*) from sqlite_master where type = 'table'").fetchone()[0]
        if n == 0:
            raise StepError(f"{path.name}: no table")
        return n
    finally:
        con.close()


def record(rep: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rep, ensure_ascii=False) + "\n")


def history(kind=None, days=None) -> list:
    if not HISTORY.exists():
        return []
    rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if days:
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = [r for r in rows if r.get("date", "") > since]
    return rows


def locked(fn):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "lock", "w") as fh:
        deadline = time.time() + 3 * 3600
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise StepError("another backup run still holds the lock after 3 h")
                time.sleep(30)
        return fn()


# ---------------------------------------------------------------- nightly steps

def pg_sql(sql: str, db: str = "postgres") -> str:
    if not SAFE_NAME.match(db):
        raise StepError(f"unexpected database name {db!r}")
    p = run(["docker", "exec", "-i", PG_CONTAINER, "sh", "-c",
             f'exec psql -U "$POSTGRES_USER" -d "{db}" -X -A -t -q -v ON_ERROR_STOP=1 -f -'],
            input=sql.encode(), capture_output=True, timeout=3600)
    return p.stdout.decode()


def latest_dump_dir() -> Path | None:
    if not DUMPS.exists():
        return None
    dated = sorted(d for d in DUMPS.iterdir() if d.is_dir() and DATED.match(d.name))
    return dated[-1] if dated else None


def dump_postgres(day: str, rep: dict) -> None:
    rows = [l.split("|") for l in pg_sql(
        "select datname, pg_get_userbyid(datdba) from pg_database where not datistemplate order by 1;").splitlines() if l]
    version = pg_sql("show server_version;").strip()
    DUMPS.mkdir(parents=True, exist_ok=True)
    os.chmod(DUMPS, 0o700)
    prev = latest_dump_dir()
    prev_info = {}
    if prev and (prev / "manifest.json").exists():
        prev_info = json.loads((prev / "manifest.json").read_text()).get("databases", {})
    for stale in list(DUMPS.glob(".*.partial")) + list(DUMPS.glob(".*.old")):  # leftovers of a killed run
        shutil.rmtree(stale, ignore_errors=True)
    tmp = DUMPS / f".{day}.partial"
    tmp.mkdir(mode=0o700)
    info, problems = {}, []
    for db, owner in rows:  # one failing database must not cost the others their dump
        if not SAFE_NAME.match(db):
            problems.append(f"unexpected database name {db!r}")
            continue
        out = tmp / f"{db}.dump"
        try:
            with open(out, "wb") as fh:  # no -t: a TTY would corrupt the binary stream
                run(["docker", "exec", PG_CONTAINER, "sh", "-c", f'exec pg_dump -U "$POSTGRES_USER" -Fc -d "{db}"'],
                    stdout=fh, timeout=3600)
            with open(out, "rb") as fh:  # reads the whole archive header and table of contents
                toc = run(["docker", "exec", "-i", PG_CONTAINER, "pg_restore", "--list"],
                          stdin=fh, capture_output=True, timeout=1800).stdout.decode()
            size = out.stat().st_size
            if size == 0 or "Format: CUSTOM" not in toc or not re.search(r"^;\s+TOC Entries: \d+", toc, re.M):
                raise StepError("unreadable dump")
            objects = sum(1 for l in toc.splitlines() if l.strip() and not l.startswith(";"))  # 0 = empty database
            info[db] = {"owner": owner, "bytes": size, "objects": objects}
            old = prev_info.get(db, {}).get("bytes")
            if old and size < old * 0.5:
                rep["warnings"].append(f"dump {db} : {human(size)} contre {human(old)} la fois précédente")
        except StepError as e:
            out.unlink(missing_ok=True)
            problems.append(f"{db}: {e}")
    if not info:
        shutil.rmtree(tmp, ignore_errors=True)
        raise StepError("no database dumped: " + "; ".join(problems))
    with open(tmp / "globals.sql", "wb") as fh:
        run(["docker", "exec", PG_CONTAINER, "sh", "-c", 'exec pg_dumpall -U "$POSTGRES_USER" --globals-only'],
            stdout=fh, timeout=600)
    if b"CREATE ROLE" not in (tmp / "globals.sql").read_bytes():
        raise StepError("globals.sql contains no role")
    manifest = {"created": now_iso(), "server_version": version, "container": PG_CONTAINER, "databases": info,
                "failed": problems}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for f in tmp.iterdir():
        f.chmod(0o600)
    final = DUMPS / day
    if final.exists():
        old_dir = DUMPS / f".{day}.old"
        shutil.rmtree(old_dir, ignore_errors=True)
        final.rename(old_dir)
        tmp.rename(final)
        shutil.rmtree(old_dir)
    else:
        tmp.rename(final)
    dated = sorted(d for d in DUMPS.iterdir() if d.is_dir() and DATED.match(d.name))
    for d in dated[:-KEEP_DUMP_DAYS]:
        shutil.rmtree(d)
    shutil.copyfile(HERE / "LISEZMOI-postgres.txt", DUMPS / "LISEZMOI.txt")
    rep["postgres"] = {db: v["bytes"] for db, v in info.items()}
    log(f"postgres: {len(info)} dumps, {human(sum(rep['postgres'].values()))} in {final}")
    if problems:
        raise StepError("; ".join(problems))


def file_owner(path: str) -> str:
    p = Path(path)
    out = run(box('stat -c %u:%g "/src/$1"', p.name, binds=[f"{p.parent}:/src:ro"]),
              capture_output=True, timeout=300).stdout.decode().strip()
    if not re.fullmatch(r"\d+:\d+", out):
        raise StepError(f"cannot stat {path}")
    return out


def copy_sqlite(rep: dict) -> None:
    out_dir = STAGING / "sqlite"
    out_dir.mkdir(parents=True, exist_ok=True)
    done, problems = {}, []
    script = ('sqlite3 "/src/$1" ".timeout 30000" ".backup /tmp/copy.db" && '
              '[ "$(sqlite3 /tmp/copy.db "PRAGMA integrity_check;")" = ok ] && cat /tmp/copy.db')
    for name, path in SQLITE_DBS.items():
        p = Path(path)
        tmp = out_dir / f".{name}.sqlite3.partial"
        try:
            owner = file_owner(path)
            with open(tmp, "wb") as fh:
                run(box(script, p.name, binds=[f"{p.parent}:/src"], user=owner), stdout=fh, timeout=1800)
            check_sqlite(tmp)
            os.replace(tmp, out_dir / f"{name}.sqlite3")
            done[name] = (out_dir / f"{name}.sqlite3").stat().st_size
        except (StepError, sqlite3.Error, OSError) as e:
            tmp.unlink(missing_ok=True)
            problems.append(f"{name}: {e}")
    rep["sqlite"] = done
    log(f"sqlite: {len(done)}/{len(SQLITE_DBS)} copies")
    if problems:
        raise StepError("; ".join(problems))


def copy_hermes_dbs(rep: dict) -> None:
    out_root = STAGING / "hermes"
    sources = []
    for d, dirs, files in os.walk(HERMES):
        rel = Path(d).relative_to(HERMES)
        if rel.parts and rel.parts[0] in HERMES_SKIP:
            dirs[:] = []
            continue
        dirs[:] = [x for x in dirs if x not in HERMES_SKIP]
        sources += [Path(d) / f for f in files if f.endswith((".db", ".sqlite", ".sqlite3"))]
    copied = {}
    for src in sources:
        rel = src.relative_to(HERMES)
        dst = out_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".partial")
        try:
            s = sqlite3.connect(str(src), timeout=60)
            t = sqlite3.connect(str(tmp))
            try:
                s.backup(t)
            finally:
                t.close()
                s.close()
            check_sqlite(tmp)
            os.replace(tmp, dst)
            copied[str(rel)] = dst.stat().st_size
        except (sqlite3.Error, StepError, OSError) as e:
            tmp.unlink(missing_ok=True)
            rep["warnings"].append(f"copie Hermes {rel} : {e}")
    for p in list(out_root.rglob("*")):  # drop copies whose database no longer exists
        if p.is_file() and not (HERMES / p.relative_to(out_root)).exists():
            p.unlink()
    rep["hermes"] = copied
    log(f"hermes: {len(copied)} databases copied")


def running_vm_disks() -> list:
    disks = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if b"qemu" not in cmdline:
            continue
        for m in re.finditer(rb"/volume1/@kvm/[^,\x00\s]+\.qcow2", cmdline):
            disks.add(m.group(0).decode(errors="replace"))
    return sorted(disks)


def restic_backup(rep: dict) -> None:
    extra = []
    for disk in running_vm_disks():  # a running VM's disk would be copied mid-write: skip it tonight
        extra += ["--exclude", disk]
        rep["warnings"].append(f"VM en marche, disque non sauvegardé ce soir : {Path(disk).name}")
    keep_existing = ('set -e; : > /tmp/paths; grep -v -e "^[[:space:]]*#" -e "^[[:space:]]*$" /run/nas-backup/paths.txt | '
                     'while IFS= read -r p; do if [ -e "$p" ]; then printf "%s\\n" "$p" >> /tmp/paths; '
                     'else echo "MISSING-PATH $p" >&2; fi; done; '
                     'exec restic backup --files-from-verbatim /tmp/paths "$@"')
    args = ["--host", HOST, "--tag", "nightly", "--exclude-file", "/run/nas-backup/excludes.txt",
            "--exclude-caches", "--json", *extra]
    subprocess.run(["docker", "rm", "-f", "nas-backup-restic"], capture_output=True)  # leftover of a killed run
    p = run(restic_cmd(args, sources=True, name="nas-backup-restic", shell=keep_existing),
            capture_output=True, text=True, timeout=8 * 3600, check=False)
    summary, errors = None, []
    for m in json_lines(p.stdout):
        if m.get("message_type") == "summary":
            summary = m
        elif m.get("message_type") == "error":
            errors.append(f"{m.get('item', '?')}: {(m.get('error') or {}).get('message', '?')}")
    for line in p.stderr.splitlines():
        if line.startswith("MISSING-PATH "):
            rep["warnings"].append(f"chemin absent : {line.split(' ', 1)[1]}")
    if p.returncode == 3 and summary:
        rep["warnings"].append(f"{len(errors)} fichier(s) illisible(s) : " + " ; ".join(errors[:5]))
    elif p.returncode != 0 or not summary:
        raise StepError(f"restic backup exit {p.returncode}: {p.stderr.strip()[-500:]}")
    keys = ("snapshot_id", "files_new", "files_changed", "files_unmodified", "data_added", "data_added_packed",
            "total_files_processed", "total_bytes_processed")
    rep["snapshot"] = {k: summary.get(k) for k in keys}
    log(f"restic: snapshot {str(summary.get('snapshot_id'))[:8]}, {summary.get('total_files_processed')} files, "
        f"{human(summary.get('total_bytes_processed'))} read, {human(summary.get('data_added_packed'))} added")


def nightly() -> int:
    day = dt.date.today().isoformat()
    rep = {"kind": "nightly", "date": day, "started": now_iso(), "warnings": [], "failed": {}}
    t0 = time.time()
    ensure_image()
    steps = (("postgres", lambda: dump_postgres(day, rep)), ("sqlite", lambda: copy_sqlite(rep)),
             ("hermes", lambda: copy_hermes_dbs(rep)), ("restic", lambda: restic_backup(rep)))
    for name, fn in steps:  # keep going after a failed step: back up whatever can be
        log(f"step {name}")
        try:
            fn()
        except Exception as e:
            rep["failed"][name] = f"{type(e).__name__}: {e}"[:600]
            log(f"step {name} FAILED: {rep['failed'][name]}")
    rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
    rep["duration_s"] = round(time.time() - t0)
    record(rep)
    if rep["status"] != "ok":
        head = ("❌ **Sauvegarde NAS du {d} : échec**" if rep["status"] == "failed"
                else "⚠️ **Sauvegarde NAS du {d} : terminée avec des avertissements**").format(d=fr_date(day))
        lines = [head] + [f"• étape {k} : {v}" for k, v in rep["failed"].items()] + [f"• {w}" for w in rep["warnings"][:8]]
        lines.append("Détails sur le NAS : `journalctl --user -u nas-backup -n 100`")
        discord("\n".join(lines))
    log(f"done: {rep['status']} in {rep['duration_s']} s")
    return 1 if rep["status"] == "failed" else 0


# ---------------------------------------------------------------- weekly: retention, check, restore test

def latest_snapshot() -> dict:
    p = run(restic_cmd(["snapshots", "--host", HOST, "--tag", "nightly", "--latest", "1", "--json"]),
            capture_output=True, text=True, timeout=900)
    snaps = json.loads(p.stdout or "[]")
    if not snaps:
        raise StepError("no snapshot in the repository yet")
    return max(snaps, key=lambda s: s["time"])


def snapshot_epoch(snap: dict) -> int:
    t = re.sub(r"(\.\d{6})\d*", r"\1", snap["time"])
    return int(dt.datetime.fromisoformat(t).timestamp())


def scratch_reset() -> None:
    run(box("rm -rf /base/restore-test && mkdir -m 700 /base/restore-test", binds=[f"{BASE}:/base"]),
        capture_output=True, timeout=3600)


def table_counts(cmd: list) -> dict:
    out = run(cmd, input=COUNT_SQL.encode(), capture_output=True, timeout=3600).stdout.decode()
    counts = {}
    for line in out.splitlines():
        name, _, n = line.rpartition("|")
        if name and n.isdigit():
            counts[name] = int(n)
    return counts


def wait_pg_ready(name: str) -> None:
    deadline = time.time() + 300
    while time.time() < deadline:
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stdout
        logs += subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr
        if "init process complete" in logs and subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "restoretest", "-d", "postgres"],
                capture_output=True).returncode == 0:
            return
        time.sleep(2)
    raise StepError("throw-away Postgres did not start within 5 min")


def restore_postgres(snap_dir: str, rep: dict) -> None:
    files = f"{SCRATCH}/files{snap_dir}"
    pg_image = run(["docker", "inspect", "-f", "{{.Config.Image}}", PG_CONTAINER],
                   capture_output=True, text=True).stdout.strip() or "postgres:17"
    subprocess.run(["docker", "rm", "-f", PG_TEST], capture_output=True)
    run(["docker", "run", "-d", "--name", PG_TEST, "--network", "none", "-e", "POSTGRES_USER=restoretest",
         "-e", f"POSTGRES_PASSWORD={secrets.token_hex(16)}", "-v", f"{SCRATCH}/pgdata:/var/lib/postgresql/data",
         "-v", f"{files}:/restore:ro", pg_image], capture_output=True, timeout=600)
    results, problems = {}, []
    try:
        wait_pg_ready(PG_TEST)
        manifest = json.loads(run(["docker", "exec", PG_TEST, "cat", "/restore/manifest.json"],
                                  capture_output=True, text=True).stdout)
        run(["docker", "exec", PG_TEST, "psql", "-U", "restoretest", "-d", "postgres", "-X", "-q",
             "-f", "/restore/globals.sql"], capture_output=True, timeout=600, check=False)
        for db in manifest["databases"]:
            if not SAFE_NAME.match(db):
                raise StepError(f"unexpected database name {db!r}")
            target = ["-d", "postgres"] if db == "postgres" else ["-C", "-d", "postgres"]
            run(["docker", "exec", PG_TEST, "pg_restore", "-U", "restoretest", "--exit-on-error", *target,
                 f"/restore/{db}.dump"], capture_output=True, timeout=3600)
            restored = table_counts(["docker", "exec", "-i", PG_TEST, "psql", "-U", "restoretest", "-d", db,
                                     "-X", "-A", "-t", "-q", "-f", "-"])
            live = table_counts(["docker", "exec", "-i", PG_CONTAINER, "sh", "-c",
                                 f'exec psql -U "$POSTGRES_USER" -d "{db}" -X -A -t -q -f -'])
            off = []
            for table, n_live in live.items():
                n = restored.get(table)
                if n is None:
                    off.append(f"{table} absente")
                elif abs(n_live - n) > max(100, 0.02 * n_live):
                    off.append(f"{table} {n} lignes contre {n_live} en vrai")
            results[db] = {"tables": len(restored), "rows": sum(restored.values()), "live_rows": sum(live.values())}
            problems += [f"{db}: {o}" for o in off]
    finally:
        subprocess.run(["docker", "rm", "-f", PG_TEST], capture_output=True)
    rep["restore"]["postgres"] = results
    if problems:
        rep["warnings"].append("restauration Postgres, écarts avec la base réelle : " + " ; ".join(problems[:6]))
    log(f"restore test postgres: {len(results)} databases, {sum(r['rows'] for r in results.values())} rows")


def restore_sqlite(rep: dict) -> None:
    script = ('cd "/scratch/files$1" || exit 2; for f in *.sqlite3; do '
              'r=$(sqlite3 "file:$PWD/$f?immutable=1" "PRAGMA integrity_check;" 2>&1); '
              't=$(sqlite3 "file:$PWD/$f?immutable=1" "select count(*) from sqlite_master where type=\'table\';" 2>&1); '
              'echo "$f|$r|$t"; done')
    out = run(box(script, str(STAGING / "sqlite"), binds=[f"{SCRATCH}:/scratch:ro"]),
              capture_output=True, text=True, timeout=1800).stdout
    ok, bad = [], []
    for line in out.splitlines():
        name, res, tables = (line.split("|") + ["", ""])[:3]
        (ok if res == "ok" and tables.isdigit() and int(tables) > 0 else bad).append(name)
    rep["restore"]["sqlite_ok"] = ok
    if bad or len(ok) < len(SQLITE_DBS):
        raise StepError(f"SQLite copies restored but not sound: {bad or 'missing'}")


def restore_files(snap: dict, rep: dict) -> None:
    binds = [f"{SCRATCH}:/scratch:ro", f"{HERE}/filecheck.sh:/filecheck.sh:ro"]
    binds += [f"{d}:/live{d}:ro" for d in FILE_CHECK_DIRS]
    out = run(box("sh /filecheck.sh \"$@\"", str(snapshot_epoch(snap)), *FILE_CHECK_DIRS, binds=binds),
              capture_output=True, text=True, timeout=1800).stdout
    totals = {"files": 0, "identical": 0, "changed_since": 0, "mismatch": 0}
    for line in out.splitlines():
        parts = dict(kv.split("=", 1) for kv in line.split() if "=" in kv)
        for k in totals:
            totals[k] += int(parts.get(k, 0))
        if line.startswith("MISMATCH "):
            rep["warnings"].append(f"fichier restauré différent : {line[9:]}")
    rep["restore"]["files"] = totals
    if totals["files"] == 0:
        raise StepError("no file restored for the byte-for-byte check")
    if totals["mismatch"]:
        raise StepError(f"{totals['mismatch']} restored file(s) differ from the live ones")


def restore_test(rep: dict) -> None:
    rep.setdefault("restore", {})
    snap = latest_snapshot()
    sid = snap["short_id"]
    rep["restore"]["snapshot"] = sid
    p = run(restic_cmd(["ls", sid, str(DUMPS), "--json"]), capture_output=True, text=True, timeout=900)
    dirs = [m["path"] for m in json_lines(p.stdout) if m.get("type") == "dir" and m.get("path", "").count("/")
            and Path(m["path"]).parent == DUMPS and DATED.match(Path(m["path"]).name)]
    if not dirs:
        raise StepError("no Postgres dump folder in the latest snapshot")
    dump_dir = max(dirs)
    scratch_reset()
    try:
        includes = ["--include", dump_dir, "--include", str(STAGING / "sqlite")]
        for d in FILE_CHECK_DIRS:
            includes += ["--include", d]
        run(restic_cmd(["restore", sid, "--target", "/scratch/files", *includes], binds=[f"{SCRATCH}:/scratch"]),
            capture_output=True, timeout=3600)
        restore_postgres(dump_dir, rep)
        restore_sqlite(rep)
        restore_files(snap, rep)
    finally:
        scratch_reset()


def weekly() -> int:
    day = dt.date.today().isoformat()
    rep = {"kind": "weekly", "date": day, "started": now_iso(), "warnings": [], "failed": {}, "restore": {}}
    t0 = time.time()
    ensure_image()
    subset = dt.date.today().isocalendar()[1] % 8 + 1
    rep["check_subset"] = f"{subset}/8"

    def forget():
        run(restic_cmd(["forget", "--host", HOST, "--tag", "nightly", *RETENTION, "--prune"]),
            capture_output=True, timeout=6 * 3600)

    def check():
        run(restic_cmd(["check", f"--read-data-subset={subset}/8"]), capture_output=True, timeout=8 * 3600)

    def stats():
        rep["stats"] = json.loads(run(restic_cmd(["stats", "--mode", "raw-data", "--json"]),
                                      capture_output=True, text=True, timeout=3600).stdout)

    for name, fn in (("forget", forget), ("check", check), ("restore", lambda: restore_test(rep)), ("stats", stats)):
        log(f"step {name}")
        try:
            fn()
        except Exception as e:
            rep["failed"][name] = f"{type(e).__name__}: {e}"[:600]
            log(f"step {name} FAILED: {rep['failed'][name]}")
    rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
    rep["duration_s"] = round(time.time() - t0)
    record(rep)
    discord(weekly_message(rep))
    log(f"done: {rep['status']} in {rep['duration_s']} s")
    return 1 if rep["status"] == "failed" else 0


def weekly_message(rep: dict) -> str:
    icon = {"ok": "✅", "warning": "⚠️", "failed": "❌"}[rep["status"]]
    nights = history("nightly", days=7)
    ok = sum(r["status"] == "ok" for r in nights)
    warn = sum(r["status"] == "warning" for r in nights)
    fail = sum(r["status"] == "failed" for r in nights)
    lines = [f"{icon} **Sauvegarde NAS : récap de la semaine** ({fr_date(rep['date'])})"]
    night = f"• Nuits : {ok}/{len(nights)} OK" + (f", {warn} avec avertissement" if warn else "") + \
            (f", {fail} en échec" if fail else "")
    if nights and nights[-1].get("snapshot"):
        last = nights[-1]
        night += (f" (dernière : {fr_date(last['date'])} à {last['started'][11:16]}, "
                  f"+{human(last['snapshot'].get('data_added_packed'))})")
    lines.append(night)
    st = rep.get("stats") or {}
    if st:
        lines.append(f"• Dépôt sur le RAID : {human(st.get('total_size'))} pour {st.get('snapshots_count')} sauvegardes ; "
                     f"intégrité vérifiée ({rep['check_subset']} des données relues)" if "check" not in rep["failed"]
                     else f"• Dépôt sur le RAID : {human(st.get('total_size'))}")
    r = rep.get("restore") or {}
    if "restore" not in rep["failed"] and r.get("postgres") is not None:
        pg = r["postgres"]
        rows = sum(v["rows"] for v in pg.values())
        f = r.get("files", {})
        lines.append(f"• Test de restauration : {len(pg)} bases Postgres ({rows:,} lignes".replace(",", " ") +
                     f"), {len(r.get('sqlite_ok', []))} bases SQLite saines, {f.get('identical', 0)} fichiers "
                     "Vaultwarden/WireGuard identiques au réel")
    lines += [f"• étape {k} en échec : {v}" for k, v in rep["failed"].items()]
    lines += [f"• {w}" for w in rep["warnings"][:6]]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def status() -> int:
    rows = history()[-14:]
    if not rows:
        print("no run recorded yet")
    for r in rows:
        snap = r.get("snapshot") or {}
        extra = f" snapshot {str(snap.get('snapshot_id'))[:8]} +{human(snap.get('data_added_packed'))}" if snap else ""
        print(f"{r['started'][:16]} {r['kind']:<8} {r['status']:<8} {r.get('duration_s', '?')} s{extra}")
        for k, v in (r.get("failed") or {}).items():
            print(f"    failed {k}: {v[:200]}")
        for w in (r.get("warnings") or [])[:5]:
            print(f"    warning: {w[:200]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "weekly", "restore-test", "status", "alert-test", "restic"])
    ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.command == "status":
        return status()
    if a.command == "alert-test":
        return 0 if discord("🧪 Test des alertes de sauvegarde du NAS : si tu lis ce message, elles arrivent bien. "
                            "Rien à faire.") else 1
    if a.command == "restic":
        ensure_image()
        return subprocess.run([str(c) for c in restic_cmd(a.args, binds=[f"{SCRATCH}:/scratch"])]).returncode
    if a.command == "run":
        return locked(nightly)
    if a.command == "weekly":
        return locked(weekly)

    def only_restore():
        rep = {"kind": "restore-test", "date": dt.date.today().isoformat(), "started": now_iso(),
               "warnings": [], "failed": {}, "restore": {}}
        ensure_image()
        try:
            restore_test(rep)
        except Exception as e:
            rep["failed"]["restore"] = f"{type(e).__name__}: {e}"[:600]
        rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
        record(rep)
        print(json.dumps({k: rep[k] for k in ("status", "restore", "warnings", "failed")}, indent=2, ensure_ascii=False))
        return 1 if rep["failed"] else 0
    return locked(only_restore)


if __name__ == "__main__":
    sys.exit(main())
