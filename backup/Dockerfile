# Helper image for backup/nas_backup.py (tag nas-backup:latest, built automatically when missing).
# sqlite3 makes consistent copies of live databases; restic is bind-mounted from the host
# (~/.local/bin/restic, checksum-verified release binary) so host and container use the same version.
FROM alpine:3.24
RUN apk add --no-cache sqlite tzdata
