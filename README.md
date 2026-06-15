# Home Server

Self-hosted services running on Docker, organized into independent stacks sharing a common PostgreSQL database. 


## Architecture

```
docker/
├── common/              Shared infrastructure (PostgreSQL)
├── home-server/         Core services (reverse proxy, DNS, VPN, passwords, streaming)
├── teslamate/           Tesla vehicle tracking & Grafana dashboards
└── qbittorrent/         Torrent client
```

### Networking

| Network | Type | Purpose |
|---------|------|---------|
| `common-network` | bridge | Shared DB access across stacks |
| `home-server-network` | bridge (IPv4+IPv6) | Internal service communication |
| `macvlan_net` | macvlan (`bridge0`) | LAN-visible containers (Pi-hole `192.168.1.100`, WireGuard `192.168.1.105`) |

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
| WireGuard | `linuxserver/wireguard` | 51820/udp | VPN, macvlan `192.168.1.105` |
| AIOStreams | `viren070/aiostreams` | 1080 | Stremio addon aggregator |

### TeslaMate (`teslamate/`)

| Service | Image | Port |
|---------|-------|------|
| TeslaMate | `teslamate/teslamate` | 4000 |
| Grafana | `teslamate/grafana` | 3000 |
| Mosquitto | `eclipse-mosquitto:2` | 1883 |

### qBittorrent (`qbittorrent/`)

| Service | Image | Port |
|---------|-------|------|
| qBittorrent | — | 6881 (BT), WebUI |

## Startup Order

```bash
cd /volume1/docker/common && docker compose up -d
cd /volume1/docker/home-server && docker compose up -d
cd /volume1/docker/teslamate && docker compose up -d
```

## Configuration

Secrets are stored in `.env` files (git-ignored). Each stack has its own:

| File | Variables |
|------|-----------|
| `common/.env` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| `home-server/.env` | `PIHOLE_WEBPASSWORD` |
| `home-server/aiostreams/.env` | AIOStreams-specific config |
| `teslamate/.env` | `TESLAMATE_ENCRYPTION_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |

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
