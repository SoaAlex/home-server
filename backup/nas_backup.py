#!/usr/bin/python3
"""NAS backup: nightly encrypted, versioned copy of the NAS service state to a restic repository on the RAID.

Runs as the NAS user from systemd user timers (see systemd/); everything that needs root (Docker volumes,
root-owned data, the repository itself) happens in throw-away containers built from this folder.

  run            nightly: Postgres dumps (one .dump per database, kept 7 days in plain sight),
                 consistent SQLite copies, restic snapshot, copy of the new snapshots to Google Drive;
                 Discord alert on any failure or warning
  weekly         retention + prune, integrity check (1/8 of the data, rotating), restore test, Discord recap
  monthly        RAID data (photos, documents, personal folders) straight to the Google Drive repository, one
                 snapshot per folder, resumed after any interruption (memory pause, kill, restart); then off-site
                 retention + prune, check of a 5 GB random sample, restore test from Google Drive, recap
  restore-test   restore the latest snapshot's databases and files into throw-away containers, compare with live
  offsite-init   create the Google Drive repository (same password, chunker parameters of the local one)
  offsite-copy   copy the last 7 days of nightly snapshots to Google Drive now
  offsite-restore-test   restore test from Google Drive only
  status         last runs (and progress of a running monthly upload)
  alert-test     send a test message to the Discord webhook
  restic ARGS    any restic command on the local repository (e.g. snapshots, ls latest /volume1/docker, stats)
  restic-offsite ARGS    the same on the Google Drive repository
What is saved, where, and how to restore: README.md. Detailed log of every job: ~/.local/state/nas-backup/logs/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
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
# restic writes each pack to a temporary file before storing it: on the RAID, not in the container layer,
# which lives on the SSD (/volume1/@docker) and would take a write of every byte uploaded
RESTIC_TMP = BASE / "tmp"
HISTORY = STATE / "history.jsonl"
# Detailed log of every job run, one file per job and per month, on the RAID: the systemd journal of this NAS
# (50 MB shared by the whole system) keeps only minutes. A few kB per run; files older than LOG_KEEP_MONTHS go.
LOGS = STATE / "logs"
LOG_KEEP_MONTHS = 12
JOB_LOGS = {"run": "nightly", "weekly": "weekly", "monthly": "monthly", "restore-test": "restore-test",
            "offsite-init": "offsite", "offsite-copy": "offsite", "offsite-restore-test": "offsite"}
CURRENT = {"command": "?", "log": None, "log_path": None}  # this process: its command, open log file, log path
IMAGE = "nas-backup:latest"
PG_CONTAINER = "common-postgres"
PG_TEST = "nas-backup-pgtest"
HOST = "nas-soaalex"
TZ = "Europe/Paris"
KEEP_DUMP_DAYS = 7
RETENTION = ["--keep-daily", "14", "--keep-weekly", "8", "--keep-monthly", "12"]

# Off-site copy: a second restic repository on Google Drive (rclone remote "gdrive", scope drive.file),
# same password as the local one. Google accepts at most 750 GB of uploads per user and per day:
# 3 MiB/s by day and 12 MiB/s at night (~580 GB/day) stays under it and leaves the house's upload alone by day.
OFFSITE_REPO = "rclone:gdrive:nas-backup/restic"
RCLONE_BIN = HOME / ".local/bin/rclone"
RCLONE_CONF = HOME / ".config/rclone/rclone.conf"
OFFSITE_BWLIMIT = "08:00,3M 23:00,12M"
OFFSITE_DNS = ["192.168.1.1", "8.8.8.8"]
OFFSITE_DATA_RETENTION = ["--keep-monthly", "12", "--keep-yearly", "3"]
OFFSITE_SAMPLE = 25  # files of the monthly data snapshot restored from Google Drive and compared with live
PROGRESS = STATE / "monthly-progress.json"
CAMPAIGN = STATE / "monthly-campaign.json"  # monthly upload under way: folders done, so that a restart resumes it
LAST_COPY = STATE / "last-offsite-copy.json"
# Memory (8 GB NAS, swap always nearly full): UGOS's earlyoom SIGKILLs the process with the highest OOM score as
# soon as MemAvailable < 64 MiB. Every backup container runs with --oom-score-adj 1000 (it dies first, never
# Hermes or a service); off-site ones also stop themselves (SIGTERM: restic exits cleanly) below MEM_YIELD
# and only (re)start above MEM_RESUME.
MEM_YIELD = 400 << 20
MEM_RESUME = 900 << 20
DATA_CONTAINER, COPY_CONTAINER = "nas-backup-offsite-data", "nas-backup-offsite-copy"
MONTHLY_COPY = "nas-backup-monthly-copy"
INTERRUPTED = {130, 137, 143, -9, -15}  # restic stopped by SIGINT/SIGTERM, container or docker client SIGKILLed
SUMMARY_KEYS = ("snapshot_id", "files_new", "files_changed", "files_unmodified", "data_added", "data_added_packed",
                "total_files_processed", "total_bytes_processed", "total_duration")
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
    # Obsidian vault: ~/.hermes/memories is a symlink into it, which restic does not follow
    ("/volume2/NAS-STORAGE/CLOUD_NAS/99-OBSIDIAN_VAULT", "/volume2/NAS-STORAGE/CLOUD_NAS/99-OBSIDIAN_VAULT"),
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

_LOG_LOCK = threading.RLock()  # re-entrant: the SIGTERM handler may log while the main thread is logging


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    if CURRENT["log"] is not None:
        with _LOG_LOCK:  # MemoryGuard logs from its own thread
            CURRENT["log"].write(line + "\n")
            CURRENT["log"].flush()


def open_log(job: str) -> None:
    """Append this run's log to LOGS/<YYYY-MM>-<job>.log; delete the files older than LOG_KEEP_MONTHS."""
    LOGS.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    k = today.year * 12 + today.month - 1 - LOG_KEEP_MONTHS
    oldest = f"{k // 12:04d}-{k % 12 + 1:02d}"
    for f in LOGS.glob("*.log"):
        if f.name[:7] < oldest:
            f.unlink(missing_ok=True)
    CURRENT["log_path"] = LOGS / f"{today:%Y-%m}-{job}.log"
    CURRENT["log"] = open(CURRENT["log_path"], "a", encoding="utf-8")


def log_hint() -> str:
    """Where the details are, for the Discord messages."""
    path = CURRENT["log_path"]
    return f"Détails sur le NAS : `{str(path).replace(str(HOME), '~')}`" if path else ""


def on_sigterm(signum, frame):
    log("stopped by SIGTERM (systemctl stop, or the NAS shutting down)")
    raise SystemExit(128 + signum)


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
        if "--name" in cmd:  # killing the docker client does not stop its container
            subprocess.run(["docker", "rm", "-f", str(cmd[cmd.index("--name") + 1])], capture_output=True)
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
            ok = 200 <= r.status < 300
    except Exception as e:  # never log the URL: it is a secret
        log(f"alert not sent: {type(e).__name__}")
        return False
    log(f"alert sent to Discord: {(text.splitlines() or [''])[0]}" if ok else "alert refused by Discord")
    return ok


def ensure_image() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        log(f"building {IMAGE}")
        run(["docker", "build", "-q", "-t", IMAGE, HERE], capture_output=True, timeout=1200)


def box(shell: str, *args, binds=(), user=None) -> list:
    """Command running `shell` in a throw-away container of the backup image (no network)."""
    cmd = ["docker", "run", "--rm", "--network", "none", "--oom-score-adj", "1000"]  # expendable: see MEM_YIELD
    if user:
        cmd += ["--user", user]
    for b in binds:
        cmd += ["-v", b]
    return cmd + [IMAGE, "sh", "-c", shell, "sh", *args]


def restic_cmd(args, *, sources=False, binds=(), name=None, shell=None, offsite=False, env=()) -> list:
    """docker run command for restic on the local repository, or on the Google Drive one with offsite=True
    (network on, rclone and its config mounted; the config is copied to /tmp so that rclone can refresh its
    token without rewriting the host file as root). The local repository stays mounted at /repo, the source
    of `copy`."""
    # earlyoom (UGOS) SIGKILLs the process with the highest oom_score once free RAM drops under 64 MiB with
    # the swap full: the backup must be that process, never Hermes or a service (it fails, alerts, retries)
    cmd = ["docker", "run", "--rm", "--hostname", HOST, "--oom-score-adj", "1000"]
    if offsite:
        for d in OFFSITE_DNS:
            cmd += ["--dns", d]
        # low CPU priority; Go soft memory limit + hard cap: this 8 GB NAS runs with its swap full
        cmd += ["--cpu-shares", "256", "--memory", "1g", "-e", "GOMEMLIMIT=400MiB",
                "-e", f"RESTIC_REPOSITORY={OFFSITE_REPO}", "-e", "RESTIC_FROM_REPOSITORY=/repo",
                "-e", "RESTIC_FROM_PASSWORD_FILE=/run/secrets/restic.pass", "-e", "RESTIC_PACK_SIZE=64",
                "-e", "RCLONE_CONFIG=/tmp/rclone.conf", "-e", f"RCLONE_BWLIMIT={OFFSITE_BWLIMIT}",
                "-e", "RCLONE_DRIVE_USE_TRASH=false", "-e", "GOGC=50",
                "-v", f"{RCLONE_BIN}:/usr/local/bin/rclone:ro", "-v", f"{RCLONE_CONF}:/run/secrets/rclone.conf:ro"]
    else:
        cmd += ["--network", "none", "-e", "RESTIC_REPOSITORY=/repo"]
    cmd += ["-e", "RESTIC_PASSWORD_FILE=/run/secrets/restic.pass", "-e", "RESTIC_CACHE_DIR=/cache",
            "-e", f"TZ={TZ}", "-e", "RESTIC_PROGRESS_FPS=0.0166", "-e", "TMPDIR=/restic-tmp",
            "-v", f"{REPO}:/repo", "-v", f"{CACHE}:/cache", "-v", f"{RESTIC_TMP}:/restic-tmp",
            "-v", f"{PASSFILE}:/run/secrets/restic.pass:ro",
            "-v", f"{RESTIC_BIN}:/usr/local/bin/restic:ro",
            "-v", f"{HERE}:/run/nas-backup:ro"]
    for e in env:
        cmd += ["-e", e]
    if name:
        cmd += ["--name", name]
    if sources:
        for src, dst in SOURCES:
            cmd += ["-v", f"{src}:{dst}:ro"]
    for b in binds:
        cmd += ["-v", b]
    cmd.append(IMAGE)
    if offsite:
        body = shell or 'exec restic "$@"'
        return cmd + ["sh", "-c", "cp /run/secrets/rclone.conf /tmp/rclone.conf || exit 1; " + body, "sh", *args]
    if shell:
        return cmd + ["sh", "-c", shell, "sh", *args]
    return cmd + ["restic", *args]


def paths_script(list_file: str) -> str:
    """Shell body: back up the paths listed in list_file that exist (a missing one is reported, not fatal)."""
    return ('set -e; : > /tmp/paths; grep -v -e "^[[:space:]]*#" -e "^[[:space:]]*$" ' + list_file + ' | '
            'while IFS= read -r p; do if [ -e "$p" ]; then printf "%s\\n" "$p" >> /tmp/paths; '
            'else echo "MISSING-PATH $p" >&2; fi; done; '
            'exec restic backup --files-from-verbatim /tmp/paths "$@"')


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
    line = json.dumps(rep, ensure_ascii=False)
    with open(HISTORY, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    log("report: " + line[:8000])


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


def locked(fn, lock="lock", wait_h=3):
    """Run fn under a lock file: `lock` for the local jobs, `lock-offsite` for everything that uploads to
    Google Drive (never two uploads at once). The monthly upload holds lock-offsite for days; the nights keep
    running beside it (restic lets backups share a repository and makes prune wait: --retry-lock).
    A marker file names the run holding the lock: one left behind means that run was killed (earlyoom, power
    cut...), which the next run writes in the log."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / lock, "w") as fh:
        deadline = time.time() + wait_h * 3600
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise StepError(f"another backup run still holds {lock} after {wait_h} h")
                time.sleep(30)
        marker = STATE / f"{lock}.running"
        if (prev := read_json(marker)):
            log(f"the previous run holding {lock} ({prev.get('command')}, pid {prev.get('pid')}, started "
                f"{prev.get('started')}) never finished: killed (out of memory?) or the NAS restarted")
        write_json(marker, {"command": CURRENT["command"], "pid": os.getpid(), "started": now_iso()})
        try:
            return fn()
        finally:
            marker.unlink(missing_ok=True)


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
    args = ["--host", HOST, "--tag", "nightly", "--exclude-file", "/run/nas-backup/excludes.txt",
            "--exclude-caches", "--retry-lock", "2h", "--json", *extra]
    subprocess.run(["docker", "rm", "-f", "nas-backup-restic"], capture_output=True)  # leftover of a killed run
    p = run(restic_cmd(args, sources=True, name="nas-backup-restic", shell=paths_script("/run/nas-backup/paths.txt")),
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
    clear_stale_locks(rep)  # an exclusive lock left by a killed prune would block the backup
    steps = (("postgres", lambda: dump_postgres(day, rep)), ("sqlite", lambda: copy_sqlite(rep)),
             ("hermes", lambda: copy_hermes_dbs(rep)), ("restic", lambda: restic_backup(rep)),
             ("offsite", lambda: nightly_offsite(rep)))
    for name, fn in steps:  # keep going after a failed step: back up whatever can be
        log(f"step {name}")
        ts = time.time()
        try:
            if name == "offsite":  # no retry: the monthly or the next night takes over (and the night stays short)
                fn()
            else:
                attempt(name, fn, rep)
            log(f"step {name}: ok ({time.time() - ts:.0f} s)")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:600]
            log(f"step {name} FAILED: {msg}")
            if name == "offsite":  # the local backup is done: a missed copy is a warning, the weekly recap tracks it
                rep["offsite"] = {"status": "failed", "error": msg}
                rep["warnings"].append(f"copie Google Drive non faite cette nuit : {msg[:300]}")
            else:
                rep["failed"][name] = msg
    rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
    rep["duration_s"] = round(time.time() - t0)
    record(rep)
    if rep["status"] != "ok" or rep.get("autofix"):
        head = ("❌ **Sauvegarde NAS du {d} : échec**" if rep["status"] == "failed"
                else "⚠️ **Sauvegarde NAS du {d} : terminée avec des avertissements**" if rep["status"] == "warning"
                else "🔧 **Sauvegarde NAS du {d} : OK après correction automatique**").format(d=fr_date(day))
        lines = [head] + [f"• étape {k} : {v}" for k, v in rep["failed"].items()] + [f"• {w}" for w in rep["warnings"][:8]]
        lines += [f"• 🔧 {a}" for a in rep.get("autofix", [])]
        lines.append(log_hint() or "Détails sur le NAS : `journalctl --user -u nas-backup -n 100`")
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
    run(["docker", "run", "-d", "--name", PG_TEST, "--network", "none", "--oom-score-adj", "1000",
         "-e", "POSTGRES_USER=restoretest",
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
    clear_stale_locks(rep)  # forget --prune and check need an exclusive lock

    def forget():  # --retry-lock: the monthly restore test may be reading the repository
        run(restic_cmd(["forget", "--retry-lock", "2h", "--host", HOST, "--tag", "nightly", *RETENTION, "--prune"]),
            capture_output=True, timeout=6 * 3600)

    def check():
        run(restic_cmd(["check", "--retry-lock", "2h", f"--read-data-subset={subset}/8"]),
            capture_output=True, timeout=8 * 3600)

    def stats():
        rep["stats"] = json.loads(run(restic_cmd(["stats", "--mode", "raw-data", "--json"]),
                                      capture_output=True, text=True, timeout=3600).stdout)

    for name, fn in (("forget", forget), ("check", check), ("restore", lambda: restore_test(rep)), ("stats", stats)):
        log(f"step {name}")
        ts = time.time()
        try:
            attempt(name, fn, rep)
            log(f"step {name}: ok ({time.time() - ts:.0f} s)")
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
    by_date = {}  # a night counts once: its last run (a manual re-run replaces a failed one)
    for r in history("nightly", days=7):
        by_date[r["date"]] = r
    nights = [by_date[d] for d in sorted(by_date)]
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
    lines.append("• " + copy_line())
    if (cl := campaign_line()):
        lines.append("• " + cl)
    elif (months := history("monthly", days=45)):
        state = {"ok": "OK", "warning": "avec avertissement", "failed": "en échec"}.get(months[-1].get("status"), "?")
        lines.append(f"• Données du RAID sur Google Drive : dernier envoi mensuel le {fr_date(months[-1]['date'])} ({state})")
    if (pl := progress_line()):
        lines.append(f"• En cours : {pl}")
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
    lines += [f"• 🔧 Corrigé automatiquement : {a}" for a in rep.get("autofix", [])]
    if (rep["status"] != "ok" or rep.get("autofix")) and (hint := log_hint()):
        lines.append(hint)
    return "\n".join(lines)


# ---------------------------------------------------------------- off-site: Google Drive

AUTH_HINT = ("Google refuse l'accès au Drive (jeton expiré ou révoqué) : vérifie que l'app OAuth « Hermes » est "
             "« En production » (Google Auth Platform → Audience), puis lance `rclone config reconnect gdrive:` "
             "sur le NAS")
AUTH_ERROR = re.compile(r"invalid_grant|token (has been )?(expired|revoked)|cannot fetch token|config reconnect", re.I)


class AuthError(StepError):
    pass


def check_auth(text: str) -> None:
    """Raise AuthError (retrying makes no sense) when rclone reports a dead Google token."""
    if AUTH_ERROR.search(text or ""):
        raise AuthError(AUTH_HINT)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, obj) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def try_lock(name: str):
    """The lock file `name`, held (an open file: close it to release), or None when another job holds it."""
    STATE.mkdir(parents=True, exist_ok=True)
    fh = open(STATE / name, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except BlockingIOError:
        fh.close()
        return None


def fresh(name: str) -> str:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # leftover of a killed run
    return name


def mem_available() -> int:
    """MemAvailable in bytes (free RAM + what the kernel can reclaim without swapping)."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 1 << 50  # unknown: never block on it


def wait_for_memory(max_wait: int, *, what: str, on_wait=None) -> None:
    """Return once MemAvailable has stayed above MEM_RESUME for a minute, or after max_wait seconds anyway."""
    deadline, streak, said = time.time() + max_wait, 0, False
    while True:
        streak = streak + 1 if mem_available() >= MEM_RESUME else 0
        if streak >= 3:
            return
        if streak == 0 and not said:
            said = True
            log(f"{what}: waiting for memory ({human(mem_available())} available, {human(MEM_RESUME)} wanted)")
            if on_wait:
                on_wait()
        if time.time() > deadline:
            log(f"{what}: {human(mem_available())} available after {max_wait // 60} min of waiting, going on")
            return
        time.sleep(30)


class MemoryGuard:
    """While active, stop container `name` (docker stop = SIGTERM: restic exits cleanly, code 130) once
    MemAvailable has stayed under MEM_YIELD for 15 s, well before earlyoom starts killing at 64 MiB."""

    def __init__(self, name: str):
        self.name, self.yielded = name, False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self) -> None:
        low = 0
        while not self._stop.wait(5):
            low = low + 1 if mem_available() < MEM_YIELD else 0
            if low >= 3:
                avail = mem_available()
                if subprocess.run(["docker", "stop", "-t", "90", self.name], capture_output=True).returncode == 0:
                    self.yielded = True
                    log(f"memory low ({human(avail)} available): stopped {self.name} to leave room to the NAS")
                    return
                low = 0  # not started yet, or already gone

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:  # never swallows an exception
        self._stop.set()
        self._thread.join(timeout=150)


# ---------------------------------------------------------------- self-repair: automatic fix, then another try

# A run killed by earlyoom (or a power cut) leaves its restic lock in the repository. Every restic here runs as
# PID 1 of its container, so restic cannot see that the owner is dead; such a lock blocks every later prune and
# check (exclusive lock) until removed: 2026-11-01, the weekly waited 2 x 2 h on a 35 h old lock, then failed.
LOCK_ERROR = re.compile(r"already locked|unable to create lock|failed to (create|refresh) lock|exit 11 \(", re.I)
KILLED_ERROR = re.compile(r"exit (-9|-15|130|137|143)\b|signal (killed|terminated)", re.I)
# a human must look: retrying would only hide it (or make it worse)
NO_RETRY_ERROR = re.compile(r"repository contains errors|errors were found|ciphertext verification failed|damaged|"
                            r"wrong password|exit (10|12) \(|timeout after", re.I)
TRIES = 3
RETRY_PAUSE = 300


def unlock_stale(*, offsite: bool = False) -> int:
    """`restic unlock` WITHOUT --remove-all: removes only the locks restic deems stale, i.e. not refreshed for
    30 min (a running restic refreshes its lock every 5 min), so the jobs running beside keep theirs.
    Returns how many were removed; never raises (a failed unlock is logged, the retry tells the rest)."""
    where = "Google Drive" if offsite else "RAID"
    try:
        p = run(restic_cmd(["unlock"], offsite=offsite, name=fresh(f"nas-backup-unlock{'-offsite' if offsite else ''}")),
                capture_output=True, text=True, timeout=1800, check=False)
    except StepError as e:
        log(f"unlock ({where}) not done: {e}")
        return 0
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        log(f"unlock ({where}) exit {p.returncode}: {out.strip()[-300:]}")
        return 0
    m = re.search(r"removed (\d+) lock", out)
    n = int(m.group(1)) if m else 0
    if n:
        log(f"removed {n} stale restic lock(s) from the {where} repository (left by a killed run)")
    return n


def clear_stale_locks(rep: dict, repos=("local",)) -> None:
    """Before the steps that need an exclusive lock: sweep the dead locks, and say so in the recap."""
    for r in repos:
        if (n := unlock_stale(offsite=r == "offsite")):
            rep.setdefault("autofix", []).append(
                f"{n} verrou(s) mort(s) supprimé(s) sur le dépôt {'Google Drive' if r == 'offsite' else 'du RAID'} "
                "(laissé(s) par une exécution tuée)")


def attempt(name: str, fn, rep: dict, *, repos=("local",), tries: int = TRIES, pause: int = RETRY_PAUSE) -> None:
    """Run step fn; if it fails, apply an automatic fix and run it again, `tries` runs in all:
      - restic refused the lock     -> remove the stale locks of `repos` and go again at once (if none was
                                       stale, a live job holds it: wait `pause` s);
      - container killed (earlyoom) -> wait for free memory (up to 2 h), go again;
      - anything else               -> go again after `pause` s (Docker, disk or Google Drive hiccup).
    Never retried: a damaged repository, a wrong password, a timeout, a dead Google token (AuthError), a bug in
    this script. Warnings added by a failed run are dropped (the next run adds them again). A step that only
    succeeded thanks to a fix is listed in rep["autofix"] (recaps)."""
    fixes = []
    for k in range(1, tries + 1):
        mark = len(rep["warnings"])
        try:
            fn()
        except AuthError:
            raise
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if (k == tries or NO_RETRY_ERROR.search(msg)
                    or isinstance(e, (TypeError, KeyError, AttributeError, NameError, IndexError))):
                if fixes:
                    raise StepError(f"{e} [{k} essais ; corrections tentées : {', '.join(fixes)}]") from e
                raise
            del rep["warnings"][mark:]
            if LOCK_ERROR.search(msg):
                n = sum(unlock_stale(offsite=r == "offsite") for r in repos)
                fix = f"{n} verrou(s) mort(s) supprimé(s)" if n else "dépôt verrouillé par une autre tâche, attente"
                if not n:
                    time.sleep(pause)
            elif KILLED_ERROR.search(msg):
                fix = "relancé après un arrêt brutal (mémoire ?)"
                wait_for_memory(2 * 3600, what=name)
            else:
                fix = "relancé"
                time.sleep(pause)
            fixes.append(fix)
            log(f"step {name}: try {k}/{tries} failed ({msg[:300]}); auto-fix: {fix}; trying again")
            continue
        if fixes:
            rep.setdefault("autofix", []).append(f"étape {name} réussie au {k}e essai ({', '.join(fixes)})")
        return


def fmt_int(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def fmt_duration(seconds) -> str:
    s = int(seconds or 0)
    if s >= 86400:
        return f"{s // 86400} j {s % 86400 // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60:02d}"
    return f"{max(1, s // 60)} min"


def snapshots(tag: str, *, offsite: bool) -> list:
    try:
        p = run(restic_cmd(["snapshots", "--retry-lock", "1h", "--host", HOST, "--tag", tag, "--json"],
                           offsite=offsite), capture_output=True, text=True, timeout=1800)
    except StepError as e:
        if offsite:
            check_auth(str(e))
        raise
    return sorted(json.loads(p.stdout or "[]"), key=lambda s: s["time"])


def offsite_copy(rep: dict, *, name: str = COPY_CONTAINER, max_wait: int = 1800) -> None:
    """Copy the nightly snapshots of the last 7 days to Google Drive (restic skips those already there),
    then check that every one of them has its copy."""
    since = time.time() - 7 * 86400
    local = [s for s in snapshots("nightly", offsite=False) if snapshot_epoch(s) >= since]
    if not local:
        raise StepError("no nightly snapshot of the last 7 days to copy")
    wait_for_memory(max_wait, what="copy to Google Drive")
    with MemoryGuard(name) as guard:
        p = run(restic_cmd(["copy", "--retry-lock", "2h", *[s["id"] for s in local]], name=fresh(name), offsite=True),
                capture_output=True, text=True, timeout=4 * 3600, check=False)  # within the nightly unit's 8 h
    if guard.yielded:
        raise StepError("stopped to leave memory to the NAS; the next copy takes over (restic resumes)")
    if p.returncode != 0:
        check_auth(p.stderr)
        raise StepError(f"restic copy exit {p.returncode}: {p.stderr.strip()[-400:]}")
    copied = len(re.findall(r"^snapshot \S+ saved", p.stdout + p.stderr, re.M))
    remote = snapshots("nightly", offsite=True)
    originals = {s.get("original") or s["id"] for s in remote}
    missing = [s["short_id"] for s in local if s["id"] not in originals]
    if missing:
        raise StepError(f"{len(missing)} snapshot(s) still missing on Google Drive: {', '.join(missing[:5])}")
    rep["offsite"] = {"status": "ok", "copied": copied, "remote_snapshots": len(remote), "at": now_iso()}
    write_json(LAST_COPY, rep["offsite"])
    log(f"offsite: {copied} snapshot(s) copied to Google Drive, {len(remote)} nightly snapshots there")


def nightly_offsite(rep: dict) -> None:
    """Nightly step: copy the new snapshot to Google Drive, unless the monthly upload holds Google Drive (it
    copies the nightly snapshots itself, before and after the data): never two uploads at once."""
    fh = try_lock("lock-offsite")
    if fh is None:
        rep["offsite"] = {"status": "skipped", "reason": "monthly upload running"}
        log("offsite: skipped, the monthly Google Drive upload is running and copies the nightly snapshots itself")
        return
    try:
        offsite_copy(rep)
    finally:
        fh.close()


def run_stream(cmd: list, *, name: str, timeout: int, progress: Path | None = None, label: dict | None = None):
    """Run a long `restic ... --json` without keeping its output in memory; the latest status line is
    written to `progress` once a minute. Returns (returncode, summary, errors, stderr)."""
    STATE.mkdir(parents=True, exist_ok=True)
    errpath = STATE / f"{name}.stderr"
    summary, errors, last, started = None, [], 0.0, now_iso()
    deadline = time.time() + timeout
    keys = ("percent_done", "total_files", "files_done", "total_bytes", "bytes_done", "seconds_elapsed",
            "seconds_remaining")
    with open(errpath, "w+b") as err:
        p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=err, text=True)
        assert p.stdout is not None
        try:
            for line in p.stdout:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                kind = m.get("message_type")
                if kind == "summary":
                    summary = m
                elif kind == "error":
                    errors.append(f"{m.get('item', '?')}: {(m.get('error') or {}).get('message', '?')}")
                elif kind == "status" and progress is not None and time.time() - last >= 60:
                    write_json(progress, {"state": "running", **(label or {}), "started": started,
                                          "updated": now_iso(), **{k: m.get(k) for k in keys}})
                    last = time.time()
                if time.time() > deadline:
                    raise StepError(f"timeout after {timeout} s")
            p.wait(timeout=max(1, deadline - time.time()))
        except (StepError, subprocess.TimeoutExpired) as e:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            p.kill()
            raise StepError(f"{name}: {e}") from e
        finally:
            if progress is not None:
                progress.unlink(missing_ok=True)
        err.seek(0)
        stderr = err.read().decode(errors="replace")
    errpath.unlink(missing_ok=True)
    for line in stderr.splitlines():  # with --json, restic prints its per-file errors as JSON on stderr
        if line.startswith("{"):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("message_type") == "error":
                errors.append(f"{m.get('item', '?')}: {(m.get('error') or {}).get('message', '?')}")
    return p.returncode, summary, errors, stderr


def data_groups(rep: dict) -> list:
    """Folders of the monthly data backup, one restic snapshot each: one per line of paths-data.txt, a line
    ending in "/*" giving one per sub-folder (personal folders: no names in this public repo). Paths written
    as exact folders in excludes-data.txt are skipped; a missing path is a warning."""
    wanted = [l.strip() for l in (HERE / "paths-data.txt").read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.strip().startswith("#")]
    excluded = {l.strip().rstrip("/") for l in (HERE / "excludes-data.txt").read_text(encoding="utf-8").splitlines()
                if l.strip().startswith("/") and not any(c in l for c in "*?[")}
    script = ('for p in "$@"; do case "$p" in */\\*) for d in "${p%/*}"/*/; do [ -d "$d" ] && echo "${d%/}"; done ;; '
              '*) if [ -e "$p" ]; then echo "$p"; else echo "MISSING $p"; fi ;; esac; done')
    out = run(box(script, *wanted, binds=["/volume2:/volume2:ro"]), capture_output=True, text=True,
              timeout=600).stdout
    groups = []
    for line in out.splitlines():
        if line.startswith("MISSING "):
            rep["warnings"].append(f"chemin absent : {line[8:]}")
        elif line and line not in excluded and line not in groups:
            groups.append(line)
    if not groups:
        raise StepError("nothing to back up: check paths-data.txt")
    return groups


def active_campaign() -> dict | None:
    """The monthly upload under way (started less than 10 days ago and not finished), if any."""
    c = read_json(CAMPAIGN)
    if c and not c.get("finished") and time.time() - c.get("t0", 0) < 10 * 86400:
        return c
    return None


def backup_group(path: str, label: str, camp: dict) -> dict:
    """restic backup of one folder to Google Drive, started again after any interruption (memory pause,
    earlyoom kill, crash): what was uploaded stays (restic writes its index every few minutes), only this
    folder is read again. Returns the restic summary."""
    args = ["backup", "--host", HOST, "--tag", "monthly-data", "--exclude-file", "/run/nas-backup/excludes-data.txt",
            "--exclude-caches", "--retry-lock", "2h", "--json", path]
    where = {"group": label, "path": path}
    kills = fails = 0
    while True:
        wait_for_memory(8 * 3600, what=label, on_wait=lambda: write_json(PROGRESS, {
            "state": "waiting", **where, "updated": now_iso()}))
        cmd = restic_cmd(args, name=fresh(DATA_CONTAINER), offsite=True, binds=["/volume2:/volume2:ro"])
        with MemoryGuard(DATA_CONTAINER) as guard:
            try:
                rc, summary, errors, stderr = run_stream(cmd, name=DATA_CONTAINER, timeout=5 * 86400,
                                                         progress=PROGRESS, label=where)
            except StepError as e:  # timeout
                rc, summary, errors, stderr = -1, None, [], str(e)
        if rc in (0, 3) and summary:
            res = {k: summary.get(k) for k in SUMMARY_KEYS}
            res["unreadable"] = len(errors) if rc == 3 else 0
            res["unreadable_sample"] = errors[:3] if rc == 3 else []
            return res
        check_auth(stderr)
        if guard.yielded:
            camp["pauses"] = camp.get("pauses", 0) + 1
            write_json(CAMPAIGN, camp)
            continue  # the next round waits for memory first
        if rc in INTERRUPTED:
            kills += 1
            camp["kills"] = camp.get("kills", 0) + 1
            write_json(CAMPAIGN, camp)
            if kills > 12:
                raise StepError(f"{path}: interrupted {kills} times in a row (exit {rc}), left for next month")
            log(f"{label}: restic interrupted (exit {rc}), again in 5 min")
            time.sleep(300)
            continue
        fails += 1
        if fails >= 3:
            raise StepError(f"{path}: restic backup exit {rc}: {stderr.strip()[-400:]}")
        log(f"{label}: restic backup exit {rc}, again in 10 min: {stderr.strip()[-200:]}")
        time.sleep(600)


def offsite_data_backup(rep: dict, camp: dict) -> None:
    """Back up the RAID data straight to Google Drive, one snapshot per folder (data_groups), skipping the
    folders this campaign has already sent."""
    groups = data_groups(rep)
    camp["groups"] = groups
    write_json(CAMPAIGN, camp)
    failed = []
    try:
        for k, path in enumerate(groups, 1):
            if path in camp["done"]:
                continue
            label = f"dossier {k}/{len(groups)}"
            log(f"data {label}: {path}")
            try:
                res = backup_group(path, label, camp)
            except AuthError:
                raise
            except StepError as e:
                failed.append(str(e)[:300])
                log(f"data {label} FAILED: {e}")
                continue
            camp["done"][path] = {**res, "at": now_iso()}
            write_json(CAMPAIGN, camp)
            log(f"data {label}: snapshot {str(res.get('snapshot_id'))[:8]}, {res.get('total_files_processed')} files, "
                f"{human(res.get('total_bytes_processed'))} read, {human(res.get('data_added_packed'))} uploaded")
    finally:
        PROGRESS.unlink(missing_ok=True)
    done = [camp["done"][p] for p in groups if p in camp["done"]]
    rep["data"] = {"folders": len(groups), "done": len(done), "pauses": camp.get("pauses", 0),
                   "kills": camp.get("kills", 0),
                   **{k: sum(d.get(k) or 0 for d in done)
                      for k in ("total_files_processed", "total_bytes_processed", "data_added_packed")}}
    if (n := sum(d.get("unreadable", 0) for d in done)):
        sample = [e for d in done for e in d.get("unreadable_sample", [])]
        rep["warnings"].append(f"{n} fichier(s) illisible(s), ignoré(s) : " + " ; ".join(sample)[:300])
    if failed:
        raise StepError(f"{len(failed)}/{len(groups)} dossier(s) non envoyé(s) : " + " ; ".join(failed))


def offsite_forget_prune(rep: dict) -> None:
    """Retention on Google Drive: the nightly copies as on the RAID, the data per folder (forget groups the
    snapshots by host and paths)."""
    wait_for_memory(8 * 3600, what="forget/prune")
    for tag, policy in (("nightly", RETENTION), ("monthly-data", OFFSITE_DATA_RETENTION)):
        run(restic_cmd(["forget", "--retry-lock", "2h", "--host", HOST, "--tag", tag, *policy],
                       name=fresh("nas-backup-monthly-prune"), offsite=True), capture_output=True, timeout=3 * 3600)
    run(restic_cmd(["prune", "--retry-lock", "2h", "--max-unused", "10%"], name=fresh("nas-backup-monthly-prune"),
                   offsite=True), capture_output=True, timeout=12 * 3600)


def offsite_check(rep: dict) -> None:
    """check with a fresh temporary cache (all metadata re-downloaded from Google Drive) + 5 GB of data packs
    read at random. restic creates and removes that cache itself; a killed run's leftover is swept here.
    Not in SCRATCH: the Sunday restore test may be using it at the same time."""
    sweep = ('find /cache /restic-tmp -maxdepth 1 -name "restic-check-cache-*" -mmin +2880 -exec rm -rf {} \\; ; '
             'exec restic "$@"')
    wait_for_memory(8 * 3600, what="check")
    run(restic_cmd(["check", "--retry-lock", "2h", "--read-data-subset", "5G"], name=fresh("nas-backup-monthly-check"),
                   offsite=True, shell=sweep), capture_output=True, timeout=12 * 3600)


def dump_sha(snap: str, path: str, *, offsite: bool) -> tuple:
    """(sha256, size) of `restic dump snap path`: the file itself, or a tar of a folder; streamed, nothing kept."""
    errpath = STATE / "dump.stderr"
    h, n = hashlib.sha256(), 0
    with open(errpath, "w+b") as err:
        p = subprocess.Popen([str(c) for c in restic_cmd(["dump", "--retry-lock", "1h", snap, path], offsite=offsite)],
                             stdout=subprocess.PIPE, stderr=err)
        out = p.stdout
        assert out is not None
        for chunk in iter(lambda: out.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
        p.wait()
        err.seek(0)
        msg = err.read().decode(errors="replace").strip()[-300:]
    errpath.unlink(missing_ok=True)
    if p.returncode != 0:
        raise StepError(f"restic dump {path}: exit {p.returncode}: {msg}")
    return h.hexdigest(), n


def sample_files(snaps: list, k: int, max_size: int = 256 << 20) -> list:
    """k (snapshot, path) pairs of regular files picked at random (reservoir sampling over `restic ls --json`)
    across the given Google Drive snapshots."""
    picked, seen, rng = [], 0, random.Random()
    for snap in snaps:
        p = subprocess.Popen([str(c) for c in restic_cmd(["ls", "--json", snap], offsite=True)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        assert p.stdout is not None
        for line in p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            path = m.get("path", "")
            if m.get("type") != "file" or not 0 < (m.get("size") or 0) <= max_size or "\n" in path or "|" in path:
                continue
            seen += 1
            if len(picked) < k:
                picked.append((snap, path))
            elif (j := rng.randrange(seen)) < k:
                picked[j] = (snap, path)
        p.wait()
        if p.returncode != 0:
            raise StepError(f"restic ls {snap}: exit {p.returncode}")
    return picked


def offsite_restore_test(rep: dict) -> None:
    """1. The latest nightly copy on Google Drive is bit-identical to its original in the local repository
    (Postgres dumps and SQLite copies, compared as tar streams). 2. Random files of the latest RAID data
    snapshot, restored from Google Drive, match the live files (those modified since are skipped)."""
    wait_for_memory(8 * 3600, what="restore test")
    res = rep.setdefault("offsite_restore", {})
    remote = snapshots("nightly", offsite=True)
    if not remote:
        raise StepError("no nightly snapshot on Google Drive yet")
    rs = remote[-1]
    if not rs.get("original"):
        raise StepError(f"{rs['short_id']} on Google Drive is not a copy of a local snapshot")
    p = run(restic_cmd(["ls", rs["short_id"], str(DUMPS), "--json"], offsite=True),
            capture_output=True, text=True, timeout=1800)
    dirs = [m["path"] for m in json_lines(p.stdout) if m.get("type") == "dir"
            and Path(m.get("path", "/")).parent == DUMPS and DATED.match(Path(m["path"]).name)]
    if not dirs:
        raise StepError("no Postgres dump folder in the latest copy")
    total = 0
    for path in (max(dirs), str(STAGING / "sqlite")):
        remote_sum = dump_sha(rs["short_id"], path, offsite=True)
        if remote_sum != dump_sha(rs["original"], path, offsite=False):
            raise StepError(f"{path}: the Google Drive copy differs from the local backup")
        total += remote_sum[1]
    res["nightly"] = {"snapshot": rs["short_id"], "bytes": total}
    log(f"offsite restore test: nightly copy {rs['short_id']} identical to the local backup ({human(total)})")
    latest = {}  # newest snapshot of each folder
    for s in snapshots("monthly-data", offsite=True):
        latest[tuple(s.get("paths") or ())] = s
    if not latest:
        return
    epoch = {s["short_id"]: snapshot_epoch(s) for s in latest.values()}
    sample = sample_files(list(epoch), OFFSITE_SAMPLE)
    script = ('for p in "$@"; do if [ -f "$p" ]; then echo "$(stat -c %Y "$p")|$(sha256sum "$p" | cut -c1-64)|$p"; '
              'else echo "0||$p"; fi; done')
    out = run(box(script, *[p for _, p in sample], binds=["/volume2:/volume2:ro"]), capture_output=True, text=True,
              timeout=3600).stdout
    live = {}
    for line in out.splitlines():
        mtime, digest, path = line.split("|", 2)
        live[path] = (int(mtime or 0), digest)
    same, changed, bad = 0, 0, []
    for sid, path in sample:
        digest = dump_sha(sid, path, offsite=True)[0]
        mtime, live_digest = live.get(path, (0, ""))
        if digest == live_digest:
            same += 1
        elif not live_digest or mtime > epoch[sid]:
            changed += 1
        else:
            bad.append("/".join(path.split("/")[:4]) + "/…")  # top folders only: no private file names on Discord
    res["data"] = {"snapshots": len(epoch), "files": len(sample), "identical": same, "changed_since": changed,
                   "mismatch": len(bad)}
    log(f"offsite restore test: {same}/{len(sample)} random data files identical to live, {changed} changed since")
    if bad:
        raise StepError(f"{len(bad)} file(s) restored from Google Drive differ from the live ones: {', '.join(bad[:3])}")


def offsite_stats(rep: dict) -> None:
    rep["stats"] = json.loads(run(restic_cmd(["stats", "--mode", "raw-data", "--json"], offsite=True),
                                  capture_output=True, text=True, timeout=3 * 3600).stdout)
    rep["snapshots"] = {tag: len(snapshots(tag, offsite=True)) for tag in ("nightly", "monthly-data")}


def monthly() -> int:
    """The monthly campaign: the nightly copies first (the critical set), the RAID data folder by folder, the
    nightly copies made meanwhile, retention, check, restore test, recap. A restarted run (systemd restarts it
    after a kill) resumes the campaign: folders already sent are skipped."""
    camp = active_campaign()
    if camp:
        log(f"resuming the campaign of {camp['started']}: {len(camp['done'])} folder(s) already sent")
    else:
        camp = {"started": now_iso(), "t0": time.time(), "done": {}, "finished": None}
    camp["runs"] = camp.get("runs", 0) + 1
    write_json(CAMPAIGN, camp)
    rep = {"kind": "monthly", "date": dt.date.today().isoformat(), "started": camp["started"], "warnings": [],
           "failed": {}}
    ensure_image()

    def first_copy():  # a miss here is made up by the copy after the data
        try:
            offsite_copy(rep, name=MONTHLY_COPY, max_wait=8 * 3600)
        except AuthError:
            raise
        except StepError as e:
            log(f"first copy not done: {e}")

    # repositories whose stale locks a retry may clear (the data step and the first copy retry on their own)
    both, gdrive = ("local", "offsite"), ("offsite",)
    steps = (("copy", first_copy, None), ("data", lambda: offsite_data_backup(rep, camp), None),
             ("copy-end", lambda: offsite_copy(rep, name=MONTHLY_COPY, max_wait=8 * 3600), both),
             ("unlock", lambda: clear_stale_locks(rep, both), None),  # runs killed during the upload left theirs
             ("forget", lambda: offsite_forget_prune(rep), gdrive), ("check", lambda: offsite_check(rep), gdrive),
             ("restore", lambda: offsite_restore_test(rep), both), ("stats", lambda: offsite_stats(rep), gdrive))
    for name, fn, repos in steps:
        log(f"step {name}")
        ts = time.time()
        try:
            if repos:
                attempt(name, fn, rep, repos=repos)
            else:
                fn()
            log(f"step {name}: ok ({time.time() - ts:.0f} s)")
        except AuthError as e:
            rep["failed"][name] = str(e)
            log(f"step {name} FAILED: Google refused the token, stopping here")
            break
        except Exception as e:
            rep["failed"][name] = f"{type(e).__name__}: {e}"[:600]
            log(f"step {name} FAILED: {rep['failed'][name]}")
    camp["finished"] = now_iso()
    write_json(CAMPAIGN, camp)
    rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
    rep["duration_s"] = round(time.time() - camp["t0"])
    rep["runs"] = camp["runs"]
    record(rep)
    discord(monthly_message(rep))
    log(f"done: {rep['status']} after {fmt_duration(rep['duration_s'])} ({camp['runs']} run(s))")
    return 1 if rep["status"] == "failed" else 0


def monthly_message(rep: dict) -> str:
    icon = {"ok": "✅", "warning": "⚠️", "failed": "❌"}[rep["status"]]
    lines = [f"{icon} **Sauvegarde hors site (Google Drive) : récap du mois** ({fr_date(rep['date'])})"]
    d = rep.get("data") or {}
    if d:
        extra = [f"{d['pauses']} pause(s) pour laisser de la mémoire"] if d.get("pauses") else []
        extra += [f"{d['kills']} reprise(s) après un arrêt brutal"] if d.get("kills") else []
        lines.append(f"• Données du RAID : {d.get('done')}/{d.get('folders')} dossiers, "
                     f"{fmt_int(d.get('total_files_processed'))} fichiers, {human(d.get('total_bytes_processed'))} "
                     f"lus, +{human(d.get('data_added_packed'))} envoyés en {fmt_duration(rep.get('duration_s'))}" +
                     (f" ({', '.join(extra)})" if extra else ""))
    lc = read_json(LAST_COPY) or {}
    if lc.get("at"):
        lines.append(f"• Copies de nuit (bases, config, Hermes) : dernière le {fr_date(lc['at'][:10])}, "
                     f"{lc.get('remote_snapshots', '?')} sur Google Drive")
    st, sn = rep.get("stats") or {}, rep.get("snapshots") or {}
    if st:
        lines.append(f"• Dépôt Google Drive : {human(st.get('total_size'))}, {sn.get('monthly-data', '?')} sauvegarde(s) "
                     f"des données et {sn.get('nightly', '?')} de nuit" +
                     (" ; intégrité vérifiée (5 Go relus au hasard)" if "check" not in rep["failed"] else ""))
    r = rep.get("offsite_restore") or {}
    if r.get("nightly"):
        lines.append("• Test de restauration depuis Google Drive : dumps Postgres et bases SQLite de la nuit "
                     "identiques à la sauvegarde locale")
    if r.get("data"):
        x = r["data"]
        lines.append(f"• {x['files']} fichiers tirés au hasard restaurés depuis Google Drive : {x['identical']} "
                     f"identiques au réel" + (f", {x['changed_since']} modifiés depuis" if x["changed_since"] else ""))
    lines += [f"• étape {k} en échec : {v}" for k, v in rep["failed"].items()]
    lines += [f"• {w}" for w in rep["warnings"][:6]]
    lines += [f"• 🔧 Corrigé automatiquement : {a}" for a in rep.get("autofix", [])]
    if (rep["status"] != "ok" or rep.get("autofix")) and (hint := log_hint()):
        lines.append(hint)
    return "\n".join(lines)


def progress_line() -> str | None:
    g = read_json(PROGRESS)
    if not g:
        return None
    where = f"{g.get('group', '?')} ({g.get('path', '?')})"
    if g.get("state") == "waiting":
        return (f"envoi vers Google Drive, {where} : en pause, attend de la mémoire libre "
                f"[depuis {g.get('updated', '?')[11:16]}]")
    pct = 100 * (g.get("percent_done") or 0)
    left = g.get("seconds_remaining")
    return (f"envoi vers Google Drive, {where} : {pct:.0f} % ({human(g.get('bytes_done'))} sur "
            f"{human(g.get('total_bytes'))})" + (f", reste ~{fmt_duration(left)}" if left else "") +
            f" [mis à jour {g.get('updated', '?')[11:16]}]")


def copy_line() -> str:
    """When the nightly set last reached Google Drive (whichever job copied it), for the recaps and `status`."""
    at = (read_json(LAST_COPY) or {}).get("at")
    if at and time.time() - dt.datetime.fromisoformat(at).timestamp() < 3 * 86400:
        return f"Copie sur Google Drive : dernière le {fr_date(at[:10])}"
    last = f"dernière le {fr_date(at[:10])}" if at else "aucune encore"
    if active_campaign():
        return f"Copie sur Google Drive : faite par l'envoi mensuel en cours ({last})"
    return f"⚠️ Copie sur Google Drive : {last}"


def campaign_line() -> str | None:
    c = active_campaign()
    if not c:
        return None
    done, total = len(c.get("done") or {}), len(c.get("groups") or []) or "?"
    state = subprocess.run(["systemctl", "--user", "is-active", "nas-backup-monthly.service"],
                           capture_output=True, text=True).stdout.strip()
    since = fr_date(c["started"][:10])
    if state in ("active", "activating", "reloading"):
        return f"Envoi mensuel du RAID vers Google Drive en cours depuis le {since} : {done}/{total} dossiers envoyés"
    return (f"⚠️ Envoi mensuel du RAID interrompu (commencé le {since}, {done}/{total} dossiers) : "
            "`systemctl --user start nas-backup-monthly` le reprend")


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
        for a in r.get("autofix") or []:
            print(f"    auto-fix: {a[:200]}")
    print(copy_line())
    if (cl := campaign_line()):
        print(cl)
    if (pl := progress_line()):
        print("running: " + pl)
    print(f"detailed logs: {str(LOGS).replace(str(HOME), '~')}/<YYYY-MM>-<job>.log")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "weekly", "monthly", "restore-test", "offsite-init", "offsite-copy",
                                        "offsite-restore-test", "status", "alert-test", "restic", "restic-offsite"])
    ap.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    CURRENT["command"] = a.command
    if a.command not in JOB_LOGS:  # status, alert-test, restic ...: interactive, no log file
        return command(a)
    open_log(JOB_LOGS[a.command])
    signal.signal(signal.SIGTERM, on_sigterm)
    log(f"start {a.command} (pid {os.getpid()})")
    try:
        rc = command(a)
    except KeyboardInterrupt:
        log("interrupted (Ctrl-C)")
        raise
    except Exception:
        log("CRASH\n" + traceback.format_exc())
        raise
    log(f"end {a.command}: exit code {rc}")
    return rc


def command(a) -> int:
    if a.command == "status":
        return status()
    if a.command == "alert-test":
        return 0 if discord("🧪 Test des alertes de sauvegarde du NAS : si tu lis ce message, elles arrivent bien. "
                            "Rien à faire.") else 1
    if a.command in ("restic", "restic-offsite"):
        ensure_image()
        return subprocess.run([str(c) for c in restic_cmd(a.args, binds=[f"{SCRATCH}:/scratch"],
                                                          offsite=a.command == "restic-offsite")]).returncode
    if a.command == "run":
        return locked(nightly)
    if a.command == "weekly":
        return locked(weekly)
    if a.command == "monthly":
        return locked(monthly, lock="lock-offsite", wait_h=6)
    if a.command == "offsite-init":
        ensure_image()
        if subprocess.run([str(c) for c in restic_cmd(["cat", "config"], offsite=True)],
                          capture_output=True).returncode == 0:
            log("Google Drive repository already initialised")
            return 0
        p = run(restic_cmd(["init", "--copy-chunker-params"], offsite=True), capture_output=True, text=True,
                timeout=900)
        log("\n".join(l for l in p.stdout.splitlines() if l.startswith("created restic repository")))
        return 0
    if a.command in ("offsite-copy", "offsite-restore-test"):
        def one_shot():
            rep = {"kind": a.command, "date": dt.date.today().isoformat(), "started": now_iso(),
                   "warnings": [], "failed": {}}
            t0 = time.time()
            ensure_image()
            try:
                (offsite_copy if a.command == "offsite-copy" else offsite_restore_test)(rep)
            except Exception as e:
                rep["failed"][a.command] = f"{type(e).__name__}: {e}"[:600]
            rep["status"] = "failed" if rep["failed"] else "ok"
            rep["duration_s"] = round(time.time() - t0)
            record(rep)
            print(json.dumps({k: v for k, v in rep.items() if k not in ("kind", "date")}, indent=2, ensure_ascii=False))
            return 1 if rep["failed"] else 0
        return locked(one_shot, lock="lock-offsite")

    def only_restore():
        rep = {"kind": "restore-test", "date": dt.date.today().isoformat(), "started": now_iso(),
               "warnings": [], "failed": {}, "restore": {}}
        t0 = time.time()
        ensure_image()
        try:
            restore_test(rep)
        except Exception as e:
            rep["failed"]["restore"] = f"{type(e).__name__}: {e}"[:600]
        rep["status"] = "failed" if rep["failed"] else "warning" if rep["warnings"] else "ok"
        rep["duration_s"] = round(time.time() - t0)
        record(rep)
        print(json.dumps({k: rep[k] for k in ("status", "restore", "warnings", "failed")}, indent=2, ensure_ascii=False))
        return 1 if rep["failed"] else 0
    return locked(only_restore)


if __name__ == "__main__":
    sys.exit(main())
