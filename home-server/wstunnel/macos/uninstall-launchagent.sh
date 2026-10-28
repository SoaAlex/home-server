#!/bin/sh
# Remove the wstunnel LaunchAgent installed by install-launchagent.sh.
#   sh uninstall-launchagent.sh           keeps the saved settings (the secret) and the log
#   sh uninstall-launchagent.sh --purge   deletes them too
set -eu

LABEL=com.soaalex.wstunnel-vpn
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/Library/Application Support/wstunnel-vpn"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.config/wstunnel-vpn"
  rm -f "$HOME/Library/Logs/wstunnel-vpn.log"
  echo "LaunchAgent, saved settings and log removed."
else
  echo "LaunchAgent removed. Kept ~/.config/wstunnel-vpn/env (the secret) and ~/Library/Logs/wstunnel-vpn.log; use --purge to delete them."
fi
