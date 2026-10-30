# backup/: nightly encrypted backup of the NAS (restic)

`nas_backup.py` runs as the NAS user from systemd **user** timers. Everything that needs root (Docker
volumes, root-owned data, the repository itself) runs in throw-away containers of the image
`nas-backup:latest` (built from this folder when missing; never `docker image prune -a` it away).

## What is saved, every night at 04:00

| Item | How |
|---|---|
| Every database of `common-postgres` (teslamate, maison, aiostreams, aiometadata, …) | `pg_dump -Fc`: one `.dump` per database + `globals.sql` (roles), each checked with `pg_restore --list`; kept 7 days in plain sight in `/volume2/NAS-STORAGE/TechnicalBackups/postgres/<date>/` (restore steps: `LISEZMOI.txt` there) |
| Vaultwarden, Nginx Proxy Manager, Pi-hole (gravity) and Grafana SQLite databases | SQLite online backup while the apps run (consistent copy), run as the file's owner, then `integrity_check` |
| Hermes SQLite databases (`state.db`, cron, kanban…) | same, from the host |
| `/volume1/docker` (compose files, `.env`, bind-mounted data, WireGuard keys, certificates, maison photos), Docker named volumes, VM disks (skipped the nights a VM runs) | files; restic deduplicates, so unchanged files cost nothing |
| `~/.hermes` without code and toolchains, `~/.ssh`, `~/.config`, `~/.local/bin`, agent CLI settings, dotfiles | files |
| Host settings: `/etc/docker`, the macvlan shim unit, `/etc/ssh`, shares, root crontab | files (under `/hostfs/` in the snapshots) |

Exact lists: [`paths.txt`](paths.txt), [`excludes.txt`](excludes.txt). Not in this set: the RAID data
(photos, documents), which belongs to the monthly off-site copy (Google Drive, to be set up).

## Where

- **Repository:** `/volume2/@nas-backup/restic`, root-only and outside every shared folder, so a client
  with SMB access (even an admin) cannot delete or encrypt it. Encrypted (AES-256) with the password in
  `~/.config/nas-backup/restic.pass` (600); keep a copy in the password manager: without it the
  repository cannot be read. Retention: 14 daily, 8 weekly, 12 monthly snapshots.
- **Consistent SQLite copies:** `/volume2/@nas-backup/staging` (inside every snapshot).
- **Logs:** `journalctl --user -u nas-backup -u nas-backup-weekly`; history: `nas-backup status`.
- **Alerts:** Discord (`DISCORD_WEBHOOK_URL` in `backup/.env`): one message for any failure or warning,
  and a recap every Sunday. No recap on a Sunday morning means the job itself is not running.

## Every Sunday at 05:30

1. `restic forget --prune` with the retention above.
2. `restic check --read-data-subset=k/8`, `k` rotating with the week number: all data is re-read every 8 weeks.
3. Restore test, with no service stopped: the latest snapshot's Postgres dumps are restored into a
   throw-away Postgres container (`--network none`, data on the RAID) and every table's row count is compared
   with the live database; the SQLite copies must pass `integrity_check`; the Vaultwarden and WireGuard folders
   are restored and compared byte for byte with the live ones.
4. Recap on Discord.

## Commands

```bash
nas-backup run            # nightly job now (NAS_BACKUP_NO_ALERT=1 to keep Discord quiet)
nas-backup weekly         # weekly job now
nas-backup restore-test   # restore test only
nas-backup status         # last runs
nas-backup alert-test     # test message on Discord
nas-backup restic snapshots
nas-backup restic ls latest /volume1/docker/home-server/vw-data
```
`nas-backup` = `~/.local/bin/nas-backup` → `/usr/bin/python3 -I /volume1/docker/backup/nas_backup.py`.
Units: `systemd/` (copied to `~/.config/systemd/user/`, timers enabled; linger is on).

## Restore

- **A Postgres database:** follow `LISEZMOI.txt` next to the dumps (stop the services, `dropdb`,
  `pg_restore -C`). Older than 7 days: restore the dump folder from a snapshot first (below).
- **Files from a snapshot:**
  ```bash
  nas-backup restic snapshots                       # pick a snapshot (or use latest)
  nas-backup restic restore latest --target /scratch/out --include /volume1/docker/home-server/vw-data
  # restored as root under /volume2/@nas-backup/restore-test/out/<original path>; copy back as root, e.g.
  docker run --rm -v /volume2/@nas-backup/restore-test/out:/out:ro -v /volume1/docker:/volume1/docker \
    alpine cp -a /out/volume1/docker/home-server/vw-data/. /volume1/docker/home-server/vw-data/
  ```
- **Vaultwarden:** stop `bitwarden`, restore `vw-data` (above), then put the consistent copy
  `/volume2/@nas-backup/staging/sqlite/vaultwarden.sqlite3` from the same snapshot in place of
  `vw-data/db.sqlite3` (delete `db.sqlite3-wal` and `db.sqlite3-shm`), start it again. Same idea for
  Nginx Proxy Manager (`data/database.sqlite`), Pi-hole (`pihole/gravity.db`), Grafana (`grafana.db` in its volume)
  and Hermes (`~/.hermes/...`, copies under `staging/hermes/`).
- **New NAS:** clone this repo (see `REBUILD.md`), install restic, get the repository (the RAID if it
  survived, otherwise the off-site copy) and the password from the password manager, then restore
  `/volume1/docker`, the volumes and the dumps as above.
