# Home Server

Self-hosted services running on Docker, organized into independent stacks sharing a common PostgreSQL database. 


## Architecture

```
docker/
├── common/              Shared infrastructure (PostgreSQL)
├── home-server/         Core services (reverse proxy, DNS, VPN, passwords, streaming)
├── teslamate/           Tesla vehicle tracking & Grafana dashboards
├── qbittorrent/         Torrent client (managed by the UGOS app, not compose)
├── maison/              Private house-search crawler + UI (port 8090, LAN/VPN only) — see maison/README.md
├── eurl-frais/          Monthly EURL expenses CLI (Orange invoices, rent receipt, summary → Discord) — see eurl-frais/README.md
└── hermes-vault/        Encrypted configuration of the Hermes agent (age, post-quantum) — see hermes-vault/README.md
```

To rebuild everything after an OS reset or NAS swap, follow **[REBUILD.md](REBUILD.md)**.

### Networking

| Network | Type | Purpose |
|---------|------|---------|
| `common-network` | bridge | Shared DB access across stacks |
| `home-server-network` | bridge (IPv4+IPv6) | Internal service communication |
| `macvlan_net` | macvlan (`bridge0`) | LAN-visible containers (Pi-hole `192.168.1.100`) |

A macvlan child cannot reach its parent's own IP stack, and `bridge0` is also the NAS's
LAN interface (`192.168.1.55`). Two consequences:

- WireGuard runs in **host** network mode, not macvlan — on macvlan the VPN could reach
  the entire LAN *except* the NAS itself.
- A macvlan shim (`192.168.1.56`) is required for the NAS to reach Pi-hole. Installed as
  a systemd unit — see [REBUILD.md](REBUILD.md).

## Stacks

### Common (`common/`)

| Service | Image | Port |
|---------|-------|------|
| PostgreSQL 17 | `postgres:17` | 5432 (internal only) |

### Home Server (`home-server/`)

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| Nginx Proxy Manager | `jc21/nginx-proxy-manager` | 80, 81, 443 | Reverse proxy + SSL |
| Pi-hole | `pihole/pihole` | 8080 (web) | DNS ad-blocking, macvlan `192.168.1.100` |
| Vaultwarden | `vaultwarden/server` | 83, 3012 | Bitwarden-compatible password manager |
| Code Server | `linuxserver/code-server` | 8443 | VS Code for `soaalex`; LAN/VPN access |
| WireGuard | `linuxserver/wireguard` | 51820/udp | VPN, **host** network mode |
| wstunnel | `erebe/wstunnel` | 8480 (behind NPM) | WireGuard over WebSocket/TLS for UDP-blocking networks; secret path prefix `WSTUNNEL_PATH_PREFIX` in `.env` |
| AIOStreams | `viren070/aiostreams` | 1080 | Stremio addon aggregator |
| Frigate | `blakeblackshear/frigate` | 8971 | NVR; needs `/dev/dri/renderD128` |
| PTZ preset | `python:3.12-alpine` | 8095 | Named CAM-PT2 positions for Home Assistant |

Remote access from a network that blocks UDP (office Wi-Fi): WireGuard carried over HTTPS by
wstunnel, with a macOS client and LaunchAgent. See **[home-server/wstunnel/README.md](home-server/wstunnel/README.md)**.

### TeslaMate (`teslamate/`)

| Service | Image | Port |
|---------|-------|------|
| TeslaMate | `teslamate/teslamate` | 4000 |
| Grafana | `teslamate/grafana` | 3000 |
| Mosquitto | `eclipse-mosquitto:2` | 1883 |

### EURL expenses (`eurl-frais/`)

One-shot CLI container (no port), run on the 10th of each month by a Hermes cron job through
`/workspace/bin/eurl-frais monthly`. Mounts only `CLOUD_NAS/05-Travail/<EURL folder>/{Loyer,Charges}` (the NAS folder synced with OneDrive; host path = `EURL_CLOUD_DIR` in the git-ignored `eurl-frais/.env`).

### qBittorrent (`qbittorrent/`)

| Service | Image | Port |
|---------|-------|------|
| qBittorrent | — | 6881 (BT), WebUI |

## Startup Order

```bash
cd /volume1/docker/common && docker compose up -d
cd /volume1/docker/home-server && docker compose up -d
cd /volume1/docker/teslamate && docker compose up -d
cd /volume1/docker/maison && docker compose up -d
```

## Configuration

Secrets are stored in `.env` files (git-ignored). Each stack has its own:

| File | Variables |
|------|-----------|
| `common/.env` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| `home-server/.env` | `PIHOLE_WEBPASSWORD`, `FRIGATE_RTSP_PASSWORD`, `CODE_SERVER_PASSWORD`, `PTZ_CAMERA_USER`, `PTZ_API_TOKEN`, `WSTUNNEL_PATH_PREFIX` |
| `home-server/aiostreams/.env` | `BASE_URL`, `SECRET_KEY`, `DATABASE_URI` |
| `maison/.env` | `DATABASE_URL`, `DISCORD_WEBHOOK_URL`, `ENRICH_TOKEN`, `IMAP_USER`, `IMAP_PASSWORD`, … |
| `teslamate/.env` | `TESLAMATE_ENCRYPTION_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| `eurl-frais/.env` | `ORANGE_USERNAME`, `ORANGE_PASSWORD`, `ORANGE_SHARE`, `GAS_MONTHLY_EUR`, `ELECTRICITY_MONTHLY_EUR`, `DISCORD_WEBHOOK_URL`, … |

Create from templates:
```bash
cp common/.env.example common/.env
cp home-server/.env.example home-server/.env
cp teslamate/.env.example teslamate/.env
```

## Backups

- **PostgreSQL:** Automated dump to `/volume2/NAS-STORAGE/TechnicalBackups/teslamate_backup.sql`
- **Restore:**
  ```bash
  docker exec -i common-postgres psql -U teslamate teslamate < /path/to/teslamate_backup.sql
  ```

## Hardware

- **NAS:** UGREEN DXP4800 Plus (4 bays)
- **Storage:** `/volume1` (Docker + apps), `/volume2` (NAS storage + backups)

## Providers
- CloudFlare: DNS, DDNS, CDN, WAF
