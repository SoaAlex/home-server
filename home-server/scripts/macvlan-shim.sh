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
# Installed to /usr/local/bin and run at boot by macvlan-shim.service. It must live on
# the root filesystem, NOT /volume1: UGOS mounts /volume1 from its own script rather than
# fstab, so systemd only synthesises volume1.mount after the fact and RequiresMountsFor=
# has nothing to order against. Running from /volume1 fails at boot with 203/EXEC.
set -eu

SHIM_IF=ph-shim
SHIM_IP=192.168.1.56   # must be free AND outside the router's DHCP pool
# Pinned MAC: `ip link add` generates a random one each boot, which would break
# the router's static DHCP lease for SHIM_IP. 02:42 + IP in hex (0x38 = 56);
SHIM_MAC=02:42:c0:a8:01:38
PARENT=bridge0
TARGETS="192.168.1.100"   # Pi-Hole; add more macvlan container IPs as needed
SHIM_IP6=fd7c:9e4a:1b3f:2::56
TARGETS_V6="fd7c:9e4a:1b3f:2::100"

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

# Applied unconditionally so an interface left over from an older run gets
# corrected too. Must be done while down, hence the explicit down first.
ip link set dev "$SHIM_IF" down
ip link set dev "$SHIM_IF" address "$SHIM_MAC"

ip addr replace "$SHIM_IP/32" dev "$SHIM_IF"
ip -6 addr replace "$SHIM_IP6/64" dev "$SHIM_IF"
ip link set "$SHIM_IF" up

# /32 routes beat the kernel's 192.168.1.0/24-via-bridge0 route by longest-prefix
# match, so only these destinations are diverted through the shim.
for t in $TARGETS; do
  ip route replace "$t/32" dev "$SHIM_IF"
done

for t in $TARGETS_V6; do
  ip -6 route replace "$t/128" dev "$SHIM_IF"
done

echo "shim up: $SHIM_IF ($SHIM_IP, $SHIM_IP6) -> $TARGETS, $TARGETS_V6"
