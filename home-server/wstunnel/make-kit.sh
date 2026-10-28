#!/bin/sh
# Build the macOS client kit: the scripts from macos/ plus wstunnel.env holding the secret
# read from home-server/.env. The kit goes to the git-ignored
# home-server/wireguard/clients/mac-wstunnel/ directory, next to mac-wstunnel.conf (the
# WireGuard profile with the peer keys, see macos/mac-wstunnel.conf.example); copy that whole
# directory to the Mac. Re-run it after changing WSTUNNEL_PATH_PREFIX.
#
#   sh home-server/wstunnel/make-kit.sh [kit-dir]
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
kit=${1:-$root/wireguard/clients/mac-wstunnel}

die() {
  printf 'make-kit: %s\n' "$*" >&2
  exit 1
}

[ -r "$root/.env" ] || die "$root/.env not found"
prefix=$(sed -n 's/^WSTUNNEL_PATH_PREFIX=//p' "$root/.env" | tr -d '\r' | tail -n 1)
case $prefix in
  \"*\")
    prefix=${prefix#\"}
    prefix=${prefix%\"}
    ;;
  \'*\')
    prefix=${prefix#\'}
    prefix=${prefix%\'}
    ;;
esac
[ -n "$prefix" ] || die "WSTUNNEL_PATH_PREFIX is empty or missing in $root/.env"

mkdir -p "$kit"
# The kit holds a secret: never write it where git could pick it up.
case $kit in
  "$root"/*)
    if git -C "$root" rev-parse --git-dir >/dev/null 2>&1 && ! git -C "$root" check-ignore -q "$kit/wstunnel.env"; then
      die "$kit/wstunnel.env is not git-ignored; refusing to write the secret there"
    fi
    ;;
esac

cp "$here/macos/wstunnel-vpn.sh" "$here/macos/install-launchagent.sh" "$here/macos/uninstall-launchagent.sh" "$kit/"
chmod 755 "$kit/wstunnel-vpn.sh" "$kit/install-launchagent.sh" "$kit/uninstall-launchagent.sh"
(
  umask 077
  printf 'WSTUNNEL_URL=wss://vpn.soaalex.com:443\nWSTUNNEL_PATH_PREFIX=%s\n' "$prefix" >"$kit/wstunnel.env"
)
chmod 600 "$kit/wstunnel.env"

echo "Kit written to $kit:"
ls -l "$kit"
[ -e "$kit/mac-wstunnel.conf" ] || echo "Note: mac-wstunnel.conf is missing; build it from macos/mac-wstunnel.conf.example with a free peer's keys." >&2
