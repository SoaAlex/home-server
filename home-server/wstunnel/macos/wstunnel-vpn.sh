#!/bin/sh
# wstunnel client for the NAS WireGuard server: carries WireGuard's UDP packets inside
# WebSocket/TLS on TCP 443, for networks that block UDP (office Wi-Fi).
#
#   by hand:     sh wstunnel-vpn.sh          (Ctrl+C to stop)
#   background:  sh install-launchagent.sh   (macOS LaunchAgent, starts at login)
#
# Then switch the WireGuard tunnel on (its Endpoint is 127.0.0.1:51820).
#
# Settings come from the first of these files that exists:
#   $WSTUNNEL_ENV_FILE
#   wstunnel.env next to this script
#   ~/.config/wstunnel-vpn/env
# One KEY=value per line; a variable already set in the environment wins.
#   WSTUNNEL_PATH_PREFIX  required, the secret (= WSTUNNEL_PATH_PREFIX in home-server/.env)
#   WSTUNNEL_URL          default wss://vpn.soaalex.com:443
#   WSTUNNEL_LOG_LVL      default WARN (INFO logs more connection details)
#   HTTP_PROXY            optional, only if the network forces an HTTP proxy
set -eu

die() {
  printf 'wstunnel-vpn: %s\n' "$*" >&2
  exit 1
}

here=$(cd "$(dirname "$0")" && pwd)
envfile=
for f in "${WSTUNNEL_ENV_FILE:-}" "$here/wstunnel.env" "$HOME/.config/wstunnel-vpn/env"; do
  if [ -n "$f" ] && [ -r "$f" ]; then
    envfile=$f
    break
  fi
done

if [ -n "$envfile" ]; then
  cr=$(printf '\r')
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    line=${line#export }
    case $line in '' | '#'*) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    case $val in
      \"*\")
        val=${val#\"}
        val=${val%\"}
        ;;
      \'*\')
        val=${val#\'}
        val=${val%\'}
        ;;
    esac
    case $key in
      WSTUNNEL_PATH_PREFIX | WSTUNNEL_URL | WSTUNNEL_LOG_LVL | HTTP_PROXY)
        eval "[ -n \"\${$key:-}\" ] || $key=\$val"
        ;;
    esac
  done <"$envfile"
fi

: "${WSTUNNEL_URL:=wss://vpn.soaalex.com:443}"
: "${WSTUNNEL_LOG_LVL:=WARN}"
if [ -z "${WSTUNNEL_PATH_PREFIX:-}" ]; then
  die "WSTUNNEL_PATH_PREFIX is not set; put it in wstunnel.env next to this script or in ~/.config/wstunnel-vpn/env"
fi

# launchd starts agents with a minimal PATH that lacks Homebrew.
PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH
command -v wstunnel >/dev/null 2>&1 || die "wstunnel not found; run: brew install wstunnel"

# The secret goes through the environment: command-line arguments are visible in ps.
WSTUNNEL_HTTP_UPGRADE_PATH_PREFIX=$WSTUNNEL_PATH_PREFIX
export WSTUNNEL_HTTP_UPGRADE_PATH_PREFIX
if [ -n "${HTTP_PROXY:-}" ]; then export HTTP_PROXY; fi

# Under launchd (WSTUNNEL_LOG_FILE set by the LaunchAgent) keep the log file small: when the
# server refuses the client, e.g. a stale secret, wstunnel retries in a tight loop and logs
# thousands of lines a second. The loop ends with this process (exec below keeps the PID).
if [ -n "${WSTUNNEL_LOG_FILE:-}" ]; then
  (
    while kill -0 "$$" 2>/dev/null; do
      sleep "${WSTUNNEL_LOG_CHECK_SEC:-10}"
      size=$(wc -c <"$WSTUNNEL_LOG_FILE" 2>/dev/null) || size=0
      [ "$((size + 0))" -le "${WSTUNNEL_LOG_MAX_BYTES:-1000000}" ] || : >"$WSTUNNEL_LOG_FILE"
    done
  ) &
fi

# --websocket-ping-frequency: a connection that dies silently (sleep, Wi-Fi change) is dropped
# after about 4 ping periods: 10 s means roughly 40 s of outage instead of 2 minutes with the default 30 s.
exec wstunnel client \
  -L 'udp://127.0.0.1:51820:127.0.0.1:51820?timeout_sec=0' \
  --tls-verify-certificate \
  --websocket-ping-frequency 10s \
  --log-lvl "$WSTUNNEL_LOG_LVL" \
  "$WSTUNNEL_URL"
