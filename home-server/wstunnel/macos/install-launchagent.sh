#!/bin/sh
# Install the wstunnel client as a macOS LaunchAgent: it starts at login and launchd
# restarts it if it dies, so the only manual step left is switching the WireGuard
# tunnel on and off. Safe to re-run: it replaces the previous install.
#
#   sh install-launchagent.sh
#
# Needs `brew install wstunnel` and the settings (the secret) in wstunnel.env next to this
# script, or already installed in ~/.config/wstunnel-vpn/env by a previous run.
# Everything stays in your home directory; no admin rights.
set -eu

LABEL=com.soaalex.wstunnel-vpn
APP="$HOME/Library/Application Support/wstunnel-vpn"
CFG="$HOME/.config/wstunnel-vpn"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/wstunnel-vpn.log"
DOMAIN="gui/$(id -u)"
PORT=51820

die() {
  printf 'install-launchagent: %s\n' "$*" >&2
  exit 1
}

xml() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

agent_running() {
  launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -q 'state = running'
}

# True when something listens on the local UDP port (a connected socket does not count).
port_busy() {
  netstat -an -p udp 2>/dev/null |
    awk -v a="127.0.0.1.$PORT" -v b="*.$PORT" '$4 == a || $4 == b { f = 1 } END { exit !f }'
}

[ "$(uname -s)" = Darwin ] || die "this installer is for macOS"
SRC=$(cd "$(dirname "$0")" && pwd)
PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH
command -v wstunnel >/dev/null 2>&1 || die "wstunnel not found; run: brew install wstunnel"
[ -r "$SRC/wstunnel-vpn.sh" ] || die "wstunnel-vpn.sh must sit next to this script"
if [ ! -r "$SRC/wstunnel.env" ] && [ ! -r "$CFG/env" ]; then
  die "no settings found; create wstunnel.env next to this script (see wstunnel.env.example)"
fi

# Stop a previous install first: it holds the port.
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
sleep 1
if port_busy; then
  die "UDP port $PORT is already in use, probably wstunnel-vpn.sh still running in a terminal; stop it (Ctrl+C) and run this again"
fi

mkdir -p "$APP" "$CFG" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
chmod 700 "$CFG"
# A LaunchAgent cannot reliably read ~/Documents, ~/Desktop, ~/Downloads or network
# volumes (macOS privacy rules), so the script runs from a copy kept in Application Support.
cp "$SRC/wstunnel-vpn.sh" "$APP/wstunnel-vpn.sh"
chmod 755 "$APP/wstunnel-vpn.sh"
if [ -r "$SRC/wstunnel.env" ]; then
  (umask 077 && cp "$SRC/wstunnel.env" "$CFG/env")
  chmod 600 "$CFG/env"
fi
(umask 077 && : >>"$LOG")

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>$(xml "$APP/wstunnel-vpn.sh")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>WSTUNNEL_LOG_FILE</key>
    <string>$(xml "$LOG")</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>StandardOutPath</key>
  <string>$(xml "$LOG")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$LOG")</string>
</dict>
</plist>
EOF
chmod 644 "$PLIST"
if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST" >/dev/null || die "the generated $PLIST is invalid"
fi

launchctl enable "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$DOMAIN" "$PLIST" || die "launchctl bootstrap failed; inspect with: launchctl print $DOMAIN/$LABEL"

i=0
while [ "$i" -lt 10 ] && ! agent_running; do
  i=$((i + 1))
  sleep 1
done
agent_running || die "the agent is loaded but not running; read $LOG"
sleep 2
if agent_running && port_busy; then
  echo "OK: wstunnel runs as a LaunchAgent and listens on 127.0.0.1:$PORT."
  echo "Just switch the WireGuard tunnel on; no terminal needed."
else
  echo "The agent is running but UDP $PORT is not bound yet; if the tunnel does not come up, read $LOG" >&2
  exit 1
fi
