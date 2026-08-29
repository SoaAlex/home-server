# Rebuild Runbook

How to bring the whole Docker stack back after an **OS reset** or a **NAS replacement**.

> **Scope.** Assumes `/volume1` is intact (RAID 5 survived, or you copied it to the new NAS).
> Everything under `/volume1` — this repo, all bind-mount data, and all Docker named
> volumes — comes back with it. This runbook covers the parts that *don't*.
>
> RAID 5 is redundancy, not backup. It does not protect against accidental deletion,
> ransomware, filesystem corruption, or theft. If `/volume1` is genuinely gone, this
> runbook is not enough — you need the `.env` files and `home-server/wireguard/` from
> somewhere else, and both are git-ignored.

---

## 1. What survives vs. what you rebuild

**Survives (all on `/volume1`)**

| Path | Contents |
|---|---|
| `/volume1/docker/` | This repo: compose files, scripts, `.env` files |
| `/volume1/docker/home-server/wireguard/` | Server key + all peer keys |
| `/volume1/docker/home-server/vw-data/` | Vaultwarden vault |
| `/volume1/docker/home-server/nginx-proxy-manager/` | Proxy hosts DB + Let's Encrypt certs |
| `/volume1/docker/home-server/pihole/` | Blocklists, query history |
| `/volume1/@docker/volumes/` | `common_postgres-data`, `teslamate_teslamate-db`, `teslamate_teslamate-grafana-data` |

**Must be rebuilt (lives outside `/volume1`)**

1. Docker engine
2. `/etc/docker/daemon.json` ← **see §3, this one fails silently**
3. `/etc/systemd/system/macvlan-shim.service`
4. Router configuration (§7)

`/volume2` is a **separate pool** and holds Frigate recordings
(`/volume2/NAS-STORAGE/frigate`). Confirm its redundancy separately — volume1's RAID
says nothing about it.

---

## 2. Before you wipe

Copy off the NAS, or confirm they're in the repo:

- [ ] `common/.env`, `home-server/.env`, `teslamate/.env`, `home-server/aiostreams/.env` — git-ignored
- [ ] `home-server/wireguard/` — git-ignored; losing it means **reconfiguring every VPN client device**
- [ ] Router settings (§7) — recorded nowhere on the NAS
- [ ] `docker volume ls` output, to verify nothing is missing afterwards

---

## 3. OS and Docker

Install the OS, then Docker. **Before starting Docker for the first time**, restore
`/etc/docker/daemon.json`:

```json
{
	"data-root": "/volume1/@docker",
	"features": {
		"containerd-snapshotter": false
	}
}
```

> **Why this is the most dangerous step.** Without it Docker defaults to
> `/var/lib/docker`, creates **empty** named volumes, and every stack starts up looking
> perfectly healthy — just with no TeslaMate history and no Grafana dashboards. The real
> data sits untouched at `/volume1/@docker` where nothing is looking. Nothing errors.

```bash
sudo systemctl restart docker && docker info --format '{{.DockerRootDir}}'
# must print: /volume1/@docker
```

Then confirm IP forwarding, which WireGuard needs to route VPN traffic to the LAN
(Docker normally enables it, but verify):

```bash
cat /proc/sys/net/ipv4/ip_forward   # must be 1
```

---

## 4. Ownership (only when changing NAS)

Bind-mount data is owned `1000:10` (`soaalex:admin`). uid 1000 is a safe bet for the
first user on a new box; **gid 10 for the primary group is not**. A mismatch breaks
Pi-hole (`PIHOLE_GID: 10`), Vaultwarden and WireGuard on permissions.

```bash
id                                    # check your uid:gid
stat -c '%u:%g' /volume1/docker/home-server/vw-data
sudo chown -R 1000:10 /volume1/docker # only if they differ
```

---

## 5. Host-level networking (the macvlan shim)

Pi-hole runs on a **macvlan** interface at `192.168.1.100`, with `bridge0` as parent.
`bridge0` is also the NAS's own LAN interface (`192.168.1.55`). A macvlan child cannot
exchange traffic with its parent's own IP stack, so **without the shim the NAS cannot
reach Pi-hole**, and VPN clients get no DNS.

The shim gives the NAS a second presence (`192.168.1.56`) on the *child* side of that
barrier, where it is a sibling of Pi-hole rather than its parent.

```bash
sudo cp /volume1/docker/home-server/scripts/macvlan-shim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now macvlan-shim.service
systemctl status macvlan-shim --no-pager
```

`RequiresMountsFor=/volume1/docker` in the unit prevents it running before the volume
mounts. Do **not** replace this with a cron `@reboot` job — cron cannot express that
dependency and will silently no-op if it fires too early.

Verify:

```bash
ip route get 192.168.1.100      # expect: dev ph-shim src 192.168.1.56
nslookup github.com 192.168.1.100
```

---

## 5b. SSH TCP forwarding (VS Code Remote-SSH)

UGOS **rewrites `/etc/ssh/sshd_config` on every boot** and forces `AllowTcpForwarding no`
(line 87). Plain SSH still works, so this looks fine from the CLI — but VS Code
Remote-SSH uses dynamic forwarding (`ssh -D`) to reach its server and fails with
*"Failed to set up dynamic port forwarding connection over SSH to the VS Code Server"*
after every reboot.

Don't edit `sshd_config` — it gets overwritten. Use a drop-in instead: `Include` sits at
line 12, and OpenSSH takes the **first** value found for a keyword, so the drop-in beats
line 87.

```bash
printf 'AllowTcpForwarding local\n' | sudo tee /etc/ssh/sshd_config.d/00-tcp-forwarding.conf
sudo chmod 644 /etc/ssh/sshd_config.d/00-tcp-forwarding.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -i allowtcpforwarding   # expect: allowtcpforwarding local
```

`local` rather than `yes` permits `-L` and `-D` while still refusing remote (`-R`)
forwarding.

If UGOS ever starts wiping unknown files in `sshd_config.d/` too, this needs a systemd
unit that reasserts the drop-in and reloads `ssh.service`, same pattern as §5.

---

## 6. Bring up the stacks

**Order matters.** `common-network` is declared `external: true` by `home-server` and
`teslamate`, but it is *created* by `common/`. Starting out of order fails.

```bash
cd /volume1/docker/common      && docker compose up -d
cd /volume1/docker/home-server && docker compose up -d
cd /volume1/docker/teslamate   && docker compose up -d
cd /volume1/docker/minecraft   && docker compose up -d   # optional
```

Each stack needs its `.env` present first:

| File | Variables |
|---|---|
| `common/.env` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |
| `home-server/.env` | `PIHOLE_WEBPASSWORD`, `FRIGATE_RTSP_PASSWORD` |
| `home-server/aiostreams/.env` | `BASE_URL`, `SECRET_KEY`, `DATABASE_URI` |
| `teslamate/.env` | `TESLAMATE_ENCRYPTION_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` |

`teslamate/.env` Postgres credentials must match `common/.env` — TeslaMate connects to
the shared `common-postgres`, not a database of its own.

### Not managed by this repo

**qBittorrent** runs as a UGOS Docker app (`ugreen/qbittorrent:v1`, container
`qbittorrent-app-1`), not via compose. Recreate it through the UGOS UI and point it at
the existing `/volume1/docker/qbittorrent` config directory.

---

## 7. Router configuration

None of this lives on the NAS.

| Setting | Value | Why |
|---|---|---|
| Port forward | `51820/udp` → `192.168.1.55` | WireGuard runs in **host** network mode. Not `.105` — that was the old macvlan address. |
| Protect `.100` and `.56` | keep DHCP from leasing them | Pi-hole and the shim are static; DHCP knows nothing about them. A collision on `.100` takes **LAN-wide DNS down**. |
| LAN DNS | `192.168.1.100` (Pi-hole) | |
| DNS provider | Cloudflare — DNS, DDNS, CDN, WAF | `vpn.soaalex.com` must resolve to the WAN IP |

### Static DHCP leases (Livebox → "Baux DHCP statiques")

| IP | MAC | What |
|---|---|---|
| `192.168.1.55` | `6C:1F:F7:8D:4A:5C` | NAS (`bridge0`) — **required**; see warning below |

**The NAS holds `.55` by DHCP lease, not static config.** `dhclient` runs on `bridge0`,
so this reservation is the only thing keeping the address. Losing it moves the NAS and
breaks the WireGuard port forward plus all five NPM proxy targets at once. Do not
narrow the DHCP pool below `.55` unless the NAS is first given a static IP in the UGOS
network settings.

Do **not** re-add a lease for `192.168.1.105` — that was WireGuard on macvlan; it now
runs in host mode and answers on `.55`.

### `.100` and `.56` are deliberately unreserved

The Livebox's reservation form only offers devices from its **dynamic** lease list.
Pi-hole and the shim set their addresses locally and never request a lease, so the router
has never seen them and cannot offer them. The pinned MACs (`02:42:C0:A8:01:64` and
`02:42:C0:A8:01:38`, in `docker-compose.yaml` and `macvlan-shim.sh`) exist so that
identity stays stable across recreates and reboots — Docker 29.x and `ip link add` both
randomise otherwise — but no reservation currently backs them.

Accepted risk: if the Livebox ever leases `.100` to a new device, LAN-wide DNS fails.
Low probability — 14 dynamic leases currently sit in `.11`–`.32`, and Pi-hole answers ARP
continuously — and the failure is immediate and obvious. To eliminate it: give the NAS a
static IP in UGOS, then shrink the DHCP pool to `.10`–`.49`, putting `.53`, `.55`, `.56`
and `.100` permanently out of range.

The `ip_range: 192.168.1.96/28` on `macvlan_net` is a safety net for *future*
auto-assigned containers. It is inert while Pi-hole is the only macvlan service, since it
has a static address.

---

## 8. Verification

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'   # all healthy
docker volume ls                                     # 3 named volumes present
sudo wg show                                         # wg0 in HOST namespace, 4 peers
ip route get 192.168.1.100                           # via ph-shim
```

From a **VPN client** (the tests that actually matter):

```bash
ssh soaalex@192.168.1.55        # the NAS itself — broken by macvlan before this rebuild
nslookup github.com             # DNS via Pi-hole → needs the shim
ping 192.168.1.21               # another LAN host → needs the masquerade rule
```

Then spot-check data actually came back: a Grafana dashboard with historical TeslaMate
data, and the Vaultwarden vault. Empty ones mean §3 went wrong.

---

## 9. Reference

### Services and ports

| Stack | Service | Image | Host port | Network |
|---|---|---|---|---|
| common | PostgreSQL 17 | `postgres:17` | 5000→5432 | `common-network` |
| home-server | Nginx Proxy Manager | `jc21/nginx-proxy-manager` | 80, 81, 443 | `home-server-network` |
| home-server | Pi-hole | `pihole/pihole` | — (macvlan `.100`) | `macvlan_net` |
| home-server | Vaultwarden | `vaultwarden/server` | 83→80, 3012 | `home-server-network` |
| home-server | WireGuard | `linuxserver/wireguard` | 51820/udp | **host** |
| home-server | AIOStreams | `viren070/aiostreams` | 1080→3000 | `home-server-network`, `common-network` |
| home-server | Frigate | `blakeblackshear/frigate` | 8971 | `home-server-network` |
| teslamate | TeslaMate | `teslamate/teslamate` | 4000 | `default`, `common-network` |
| teslamate | Grafana | `teslamate/grafana` | 3000 | `default`, `common-network` |
| teslamate | Mosquitto | `eclipse-mosquitto:2` | 1883 | `default` |
| minecraft | Minecraft | `itzg/minecraft-server` | 39999→25565 | `default` |

Frigate needs `/dev/dri/renderD128` (N100 iGPU, VAAPI + OpenVINO) — verify it exists on
new hardware.

### Networks

| Network | Driver | Subnet | Notes |
|---|---|---|---|
| `common-network` | bridge | auto | shared DB access; created by `common/` |
| `home-server-network` | bridge + IPv6 | `172.16.238.0/24`, `fd7c:9e4a:1b3f:1::/64` | ULA is internal-only, never routed |
| `macvlan_net` | macvlan on `bridge0` | `192.168.1.0/24`, `ip_range` `.96/28` | Pi-hole `.100` |

### NPM proxy hosts

Rebuild these in the NPM UI if `nginx-proxy-manager/data` is ever lost:

| Domain | Forwards to |
|---|---|
| `bitwarden.soaalex.com` | `http://192.168.1.55:83` |
| `teslamate.soaalex.com` | `http://192.168.1.55:3000` |
| `nas.soaalex.com` | `https://192.168.1.55:9443` |
| `homeassistant.soaalex.com` | `http://192.168.1.21:8123` |
| `aiostreams.soaalex.com`, `*.aiostreams.soaalex.com` | `http://192.168.1.55:1080` |

NPM reaches these over the LAN IP rather than container names because the services sit
on different Docker networks. Putting NPM and its backends on one shared network would
let the `ports:` publishing be dropped — see "Known issues".

### PostgreSQL restore

```bash
docker exec -i common-postgres psql -U teslamate teslamate < /volume2/NAS-STORAGE/TechnicalBackups/teslamate_backup.sql
```

---

## 10. Known issues and traps

| Trap | Symptom | Fix |
|---|---|---|
| `daemon.json` not restored | Stacks healthy but all history empty | §3 |
| Shim missing after reboot | VPN clients have no DNS | §5 |
| WireGuard on macvlan | VPN reaches whole LAN **except** the NAS | Keep `network_mode: host` |
| `wg0.conf` masquerade on `-o eth+` | VPN reaches the NAS but no other LAN host | Rule must be `-s 10.13.13.0/24 ! -o %i` (host mode egress is `bridge0`, not `eth*`) |
| `sysctls` under host mode | Container won't start: *sysctl not allowed in host network namespace* | runc forbids namespaced `net.*` sysctls in host netns — set on the host instead |
| Router still forwards to `.105` | VPN won't connect at all | §7 |
| gid mismatch on new NAS | Permission errors in Pi-hole/Vaultwarden/WireGuard | §4 |

### Cleanup backlog

- `version: '3.8'` in `home-server/` and `minecraft/` is obsolete and warns on every command
- Postgres (`5000`) and Mosquitto (`1883`) publish to the whole LAN though their consumers share `common-network` — drop those `ports:` blocks
- `aio_network` is a stray Docker network with 0 containers — `docker network prune`
- `home-server/frigate/config/backup.db` is runtime state and should be git-ignored
- `.gitignore`'s `/frigate/config/` is root-anchored and does **not** match `home-server/frigate/config/`

---

> **Note:** this file documents LAN topology, hostnames, and internal IPs. Scrub it
> before making the repo public.
