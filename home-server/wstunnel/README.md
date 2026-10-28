# wstunnel: WireGuard through HTTPS

Some networks (the office Wi-Fi) block WireGuard's UDP on every port. wstunnel wraps the
WireGuard packets in a WebSocket over TLS on TCP 443, which passes like ordinary HTTPS.

```
Mac                                              Internet           NAS (192.168.1.55)
WireGuard app ──UDP──▶ 127.0.0.1:51820
                       wstunnel client ──wss://vpn.soaalex.com:443──▶ router :443
                                                  ──▶ Nginx Proxy Manager (TLS, WebSockets)
                                                  ──▶ wstunnel server :8480
                                                  ──UDP──▶ WireGuard 127.0.0.1:51820
```

The Mac profile is a **split tunnel** (`10.13.13.0/24` + `192.168.1.0/24`): Internet traffic and
the office DNS are left alone, and the wstunnel connection cannot loop back into the tunnel.

## What lives where

| Piece | Where | In git |
|---|---|---|
| wstunnel server container | `home-server/docker-compose.yaml`, service `wstunnel` | yes |
| Secret path prefix | `home-server/.env`, `WSTUNNEL_PATH_PREFIX` (template: `home-server/.env.example`) | **no** |
| NPM proxy host `vpn.soaalex.com` | NPM database (settings below, also in `REBUILD.md`) | no |
| DNS `vpn.soaalex.com` | Cloudflare, **DNS only** (grey cloud), points at the box | no |
| Router | `443/tcp` (and optionally `443/udp`) forwarded to `192.168.1.55` (`REBUILD.md` §7) | no |
| UDP 443 → 51820 redirect (optional) | `PostUp`/`PostDown` of `home-server/wireguard/config/wg_confs/wg0.conf` | no (rule below) |
| Mac client scripts | `home-server/wstunnel/macos/` | yes |
| Mac WireGuard profile | `home-server/wireguard/clients/mac-wstunnel/mac-wstunnel.conf` (peer keys) | **no** (template: `macos/mac-wstunnel.conf.example`) |
| Mac secret | `wstunnel.env` in that kit, then `~/.config/wstunnel-vpn/env` on the Mac | **no** |

`home-server/wireguard/`, every `.env` and every `wstunnel.env` are git-ignored.
`make-kit.sh` refuses to write the secret anywhere that is not.

## Server side

- **Container**: `ghcr.io/erebe/wstunnel:v11.1.0`, host network, listens on `ws://0.0.0.0:8480`.
  It can only forward to the local WireGuard (`--restrict-to=127.0.0.1:51820`) and only accepts
  the secret upgrade path (`--restrict-http-upgrade-path-prefix`). Compose refuses to start
  without `WSTUNNEL_PATH_PREFIX`.
  `cd home-server && docker compose up -d wstunnel`
- **Secret**: random, URL-safe, e.g. `openssl rand -hex 16`, in `home-server/.env`.
- **NPM proxy host** (admin UI, port 81):
  - Domain `vpn.soaalex.com`, scheme `http`, forward `192.168.1.55` port `8480`.
  - **Websockets Support on**, Block Common Exploits on, Cache Assets off.
  - SSL: new Let's Encrypt certificate, HTTP/2 off. Force SSL is not needed (the client always uses `wss`).
  - Advanced: `proxy_read_timeout 1d;` and `proxy_send_timeout 1d;` (long-lived WebSocket).
- **UDP 443 (optional, not enough for the office)**: WireGuard itself can also answer on UDP 443
  through this rule, added to `PostUp` (and removed with `-D` in `PostDown`) of `wg0.conf`:
  `iptables -t nat -A PREROUTING -p udp --dport 443 -m addrtype --dst-type LOCAL -j REDIRECT --to-ports 51820`.
  If `wg0.conf` is ever regenerated, put it back.

Check the server:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://vpn.soaalex.com/   # 400 = DNS, TLS, NPM and wstunnel all answer
docker exec wireguard wg show wg0 endpoints                          # a Mac behind wstunnel shows 127.0.0.1:<port>
docker exec wireguard wg show wg0 latest-handshakes                  # seconds since epoch of each peer's last handshake
```

`502` means the `wstunnel` container is down; a TLS "unrecognized name" or certificate error
means the NPM host or its certificate is missing.

**Never paste raw `docker logs wstunnel`**: at INFO the server prints the secret prefix for every
accepted tunnel. Filter it: `docker logs wstunnel 2>&1 | grep -v 'path prefix'`.

## Mac client

### Once

1. `brew install wstunnel`, and the WireGuard app from the Mac App Store.
2. On the NAS: `sh home-server/wstunnel/make-kit.sh`. It fills
   `home-server/wireguard/clients/mac-wstunnel/` with the scripts and `wstunnel.env` (the secret,
   read from `home-server/.env`). `mac-wstunnel.conf` (the WireGuard profile) lives there too;
   build it from `macos/mac-wstunnel.conf.example` with the keys of a **free peer** (two devices
   on one peer kick each other off).
3. Copy that directory to the Mac. It holds the secret and the peer keys: no cloud folder, no chat.
4. Import `mac-wstunnel.conf` into the WireGuard app.

### Run it

- **By hand**: `sh wstunnel-vpn.sh` (Ctrl+C to stop), then switch the WireGuard tunnel on.
- **As a LaunchAgent** (recommended): `sh install-launchagent.sh`, once. From then on wstunnel
  runs by itself and you only switch the WireGuard tunnel on and off.
  If a `wstunnel-vpn.sh` is still running in a terminal the installer stops with a message:
  Ctrl+C it and run the installer again.

### What the LaunchAgent does

- Label `com.soaalex.wstunnel-vpn`, file `~/Library/LaunchAgents/com.soaalex.wstunnel-vpn.plist`.
  It starts at login (`RunAtLoad`) and launchd restarts it whenever it exits (`KeepAlive`,
  at most every 30 s).
- It runs `/bin/sh ~/Library/Application Support/wstunnel-vpn/wstunnel-vpn.sh`, a copy of the
  script: a LaunchAgent cannot reliably read `~/Documents`, `~/Downloads` or network volumes.
- The secret sits in `~/.config/wstunnel-vpn/env` (mode 600), never in the plist and not on
  wstunnel's command line (it reaches wstunnel through an environment variable).
- The only thing it opens is UDP `127.0.0.1:51820`. macOS shows a "Background items added" notice once.
- Logs: `~/Library/Logs/wstunnel-vpn.log` (mode 600, level WARN, emptied whenever it passes ~1 MB).

### Behaviour

Measured with the real client against a throw-away test server (not on a Mac):

| Event | What happens |
|---|---|
| Server restarted or briefly down | the next WireGuard packet reopens the tunnel; back within seconds |
| The connection dies silently (sleep, Wi-Fi change: no reset ever arrives) | wstunnel pings every 10 s and drops the dead connection after about 40 s, then the next WireGuard packet reopens it: about a minute of outage |
| Wrong or stale secret | the server answers `400` and wstunnel retries in a tight loop, logging thousands of lines per second (hence the log cap). Fix the secret, or switch the WireGuard tunnel off |
| The agent process crashes | launchd restarts it (at most every 30 s) |

### Day to day

```bash
launchctl print gui/$(id -u)/com.soaalex.wstunnel-vpn | grep -E 'state|pid'   # running?
launchctl kickstart -k gui/$(id -u)/com.soaalex.wstunnel-vpn                  # restart it
sh uninstall-launchagent.sh            # remove it (keeps the secret and the log)
sh uninstall-launchagent.sh --purge    # remove everything
```

Updating the script or the secret: take the new kit and run `sh install-launchagent.sh` again.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Installer: "UDP port 51820 is already in use" | a `wstunnel-vpn.sh` still runs in a terminal (Ctrl+C), or another tool owns the port |
| Tunnel on, no handshake | agent not running (command above); wrong secret (the log shows `Invalid status code: 400`, e.g. a kit older than the last rotation: switch the tunnel off and re-install from a fresh kit); NAS check above |
| TLS error in the log | the network inspects HTTPS (the client verifies the certificate on purpose), or the Mac clock is off |
| The network forces an HTTP proxy | `HTTP_PROXY=http://host:port` in `wstunnel.env` |

## Rotate the secret

1. New value for `WSTUNNEL_PATH_PREFIX` in `home-server/.env`.
2. `cd home-server && docker compose up -d wstunnel` (recreates the container; live tunnels drop).
3. `sh wstunnel/make-kit.sh`, copy the new `wstunnel.env` to the Mac, run `sh install-launchagent.sh` there.

## Security notes

- The prefix is the only thing between the Internet and WireGuard's UDP port through the
  WebSocket; WireGuard's own keys are still required to get any further.
- The client verifies the server certificate (`--tls-verify-certificate`).
- WireGuard stays reachable directly on `51820/udp`; this path is an addition, not a replacement.
