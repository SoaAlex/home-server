# backup/: encrypted backups of the NAS (restic, on the RAID and on Google Drive)

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

Exact lists: [`paths.txt`](paths.txt), [`excludes.txt`](excludes.txt). Each new snapshot is then copied
to Google Drive the same night. The RAID data (photos, documents, personal folders) is not in this set: it
goes straight to Google Drive once a month (below).

## Off-site copy: Google Drive

A second restic repository, `rclone:gdrive:nas-backup/restic` (folder `nas-backup/restic` of the Google
Drive account, rclone remote `gdrive` in `~/.config/rclone/rclone.conf`, own OAuth client, scope
`drive.file`: rclone sees only the files it created). Same password as the local repository, same
chunker parameters (created with `nas-backup offsite-init`).

- **Every night**, after the local snapshot: `restic copy` of the nightly snapshots of the last 7 days
  (those already there are skipped). A failed copy is a warning; the weekly recap shows the last good one.
- **Every month (1st, 01:00):** a `monthly-data` snapshot of the RAID data ([`paths-data.txt`](paths-data.txt):
  every personal folder of `/volume2/@home` including *Photos archivées*, `/volume2/Photos`,
  `/volume2/Photos Partages`, `NAS-STORAGE`), minus [`excludes-data.txt`](excludes-data.txt): recycle bins,
  Frigate recordings, the `CRACKS` games, the old on-box docker copies, Homebrew, caches. Then `forget --prune`
  (nightly copies: same retention as local; data: 12 monthly + 3 yearly), `check` with a fresh cache and 5 GB
  of data read at random, a restore test from Google Drive (the latest nightly copy must be bit-identical to
  the local backup; 25 random data files must match the live ones) and a recap on Discord.
- **Bandwidth:** Google accepts at most 750 GB of uploads per account and per day. rclone is capped at
  3 MiB/s from 08:00 to 23:00 and 12 MiB/s at night (`OFFSITE_BWLIMIT`, ~580 GB/day); the first data upload
  (~1.2 TB, started 2026-10-30) takes two to three days. `nas-backup status` shows its progress;
  `systemctl --user stop nas-backup-monthly` stops it (restarting resumes: what is uploaded stays).
- **Resources:** the NAS has 8 GB of RAM and runs with its swap full; UGOS's `earlyoom` SIGKILLs the process
  with the highest OOM score once free RAM falls under 64 MiB. Every restic container therefore runs with
  `--oom-score-adj 1000` (the backup is killed first, never Hermes or a service), off-site ones also with
  `--cpu-shares 256`, a 1 GB cap and `GOMEMLIMIT=400MiB`. restic's temporary pack files go to
  `/volume2/@nas-backup/tmp` (RAID) for every job, never to the container layer on the SSD.
- **Status (2026-10-30):** the monthly timer is disabled. Its first run, started next to a manual
  `offsite-copy`, drove earlyoom into killing the Hermes gateway for 20 minutes; to be re-enabled once the data
  upload survives a kill (retry) and never overlaps another off-site job.

## Where

- **Repository:** `/volume2/@nas-backup/restic`, root-only and outside every shared folder, so a client
  with SMB access (even an admin) cannot delete or encrypt it. Encrypted (AES-256) with the password in
  `~/.config/nas-backup/restic.pass` (600); keep a copy in the password manager: without it the
  repository cannot be read. Retention: 14 daily, 8 weekly, 12 monthly snapshots.
- **Consistent SQLite copies:** `/volume2/@nas-backup/staging` (inside every snapshot).
- **Off-site:** Google Drive, see above. The OAuth client ID and secret are in `~/.config/rclone/rclone.conf`;
  keep a copy in the password manager too (a new client limited to `drive.file` cannot see these files).
- **Logs:** `journalctl --user -u nas-backup -u nas-backup-weekly -u nas-backup-monthly`; history: `nas-backup status`.
- **Alerts:** Discord (`DISCORD_WEBHOOK_URL` in `backup/.env`): one message for any failure or warning,
  a recap every Sunday and one after each monthly Google Drive run. No recap on a Sunday morning means the
  job itself is not running.

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
nas-backup monthly        # monthly Google Drive job now (systemctl --user start --no-block nas-backup-monthly)
nas-backup offsite-copy   # copy the recent nightly snapshots to Google Drive now
nas-backup offsite-restore-test
nas-backup status         # last runs, progress of a running monthly upload
nas-backup alert-test     # test message on Discord
nas-backup restic snapshots
nas-backup restic ls latest /volume1/docker/home-server/vw-data
nas-backup restic-offsite snapshots   # any restic command on the Google Drive repository
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
- **From Google Drive:** the same commands with `restic-offsite` (e.g.
  `nas-backup restic-offsite restore latest --tag monthly-data --target /scratch/out --include "/volume2/Photos"`).
  Downloads are not capped by Google like uploads, but a full restore of ~1 TB still takes hours.
- **New NAS:** clone this repo (see `REBUILD.md`), install restic and rclone, get the repository (the RAID
  if it survived, otherwise Google Drive: `rclone config` a `gdrive` remote with the saved OAuth client ID
  and secret, or with a fresh client and the full `drive` scope) and the password from the password manager,
  then restore `/volume1/docker`, the volumes, the dumps and the RAID data as above.
