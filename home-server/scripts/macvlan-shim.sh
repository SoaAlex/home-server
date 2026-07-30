#!/bin/sh
# macvlan shim
#
# A macvlan child interface cannot exchange traffic with its parent's own IP
# stack. Since bridge0 is both the macvlan parent AND the NAS's LAN interface
# (192.168.1.55), the NAS cannot reach any macvlan container - and vice versa.
#
# Children CAN talk to each other in bridge mode, so this gives the NAS a second
# presence on the child side of that barrier. Traffic to the listed targets is
# then sourced from SHIM_IP instead of 192.168.1.55.
#
# Needed so VPN clients (and the NAS itself) can use Pi-Hole for DNS.
#
# Run as root at boot:
#   DSM > Control Panel > Task Scheduler > Create > Triggered Task > User-defined script
#   Event: Boot-up   User: root   Command: /volume1/docker/home-server/scripts/macvlan-shim.sh
set -eu

SHIM_IF=ph-shim
SHIM_IP=192.168.1.56   # must be free AND outside the router's DHCP pool
PARENT=bridge0
TARGETS="192.168.1.100"   # Pi-Hole; add more macvlan container IPs as needed

# A Boot-up trigger can fire before bridge0 exists, which would abort under
# `set -e`. Wait up to 60s for the parent rather than failing the boot task.
n=0
while ! ip link show "$PARENT" >/dev/null 2>&1; do
  n=$((n + 1))
  [ "$n" -ge 30 ] && { echo "parent $PARENT never appeared" >&2; exit 1; }
  sleep 2
done

ip link show "$SHIM_IF" >/dev/null 2>&1 \
  || ip link add "$SHIM_IF" link "$PARENT" type macvlan mode bridge

ip addr replace "$SHIM_IP/32" dev "$SHIM_IF"
ip link set "$SHIM_IF" up

# /32 routes beat the kernel's 192.168.1.0/24-via-bridge0 route by longest-prefix
# match, so only these destinations are diverted through the shim.
for t in $TARGETS; do
  ip route replace "$t/32" dev "$SHIM_IF"
done

echo "shim up: $SHIM_IF ($SHIM_IP) -> $TARGETS"
