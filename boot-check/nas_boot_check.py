#!/usr/bin/env python3
"""nas-boot-check: did the NAS come back by itself after a reboot or an update?

    nas-boot-check run [--json]        one pass over every check (exit 1 on a failure)
    nas-boot-check boot [--force]      wait until the box settles (15 min), start what is safe
                                       to start, post the verdict on Discord
    nas-boot-check snapshot [--critical-port N/tcp|udp ...]
                                       record the current, healthy state as the expectation
    nas-boot-check show                print the recorded expectation

What "back" means is recorded from the live system (snapshot), not written by hand, in
~/.local/state/nas-boot-check/expected.json. It stays out of git: it holds container names,
ports and LAN addresses. Re-run `snapshot` after any intentional change (new container, port,
service, a container stopped on purpose).

Stdlib only. Read-only, except `boot`, which may start the user units and the containers that
the snapshot expects to be up (never anything else, never a system unit: that needs root).
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("NAS_BOOT_CHECK_STATE") or HOME / ".local/state/nas-boot-check")
EXPECTED = STATE / "expected.json"
LAST = STATE / "last.json"
HISTORY = STATE / "history.jsonl"
WEBHOOK_ENV = Path(os.environ.get("NAS_BOOT_CHECK_ENV") or "/volume1/docker/backup/.env")
GATEWAY_STATE = HOME / ".hermes" / "gateway_state.json"
USER_UNIT_DIR = HOME / ".config" / "systemd" / "user"

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌", INFO: "ℹ️"}

# System units worth watching when active at snapshot time (UGOS starts docker via docker_serv,
# so docker.service is "disabled" in systemd although it runs).
SYSTEM_CANDIDATES = [
    "docker.service", "containerd.service", "docker_serv.service", "storage_serv.service",
    "ugos_serv.service", "macvlan-shim.service", "ssh.service", "cron.service", "smbd.service",
    "nmbd.service", "earlyoom.service", "libvirtd.service",
]
MUST_STAY_OFF = ["tailscaled.service"]  # stopped on purpose (unpatched CVE): an update must not revive it
CUSTOM_FILES = [  # root-level customisations an update could wipe
    "/etc/systemd/system/macvlan-shim.service", "/usr/local/bin/macvlan-shim.sh",
    "/etc/systemd/system/tailscaled.service.d/10-unset-godebug.conf",
    "/etc/systemd/system/user@1000.service.d/10-wait-mounts.conf",
    "/etc/systemd/system/nas-boot-check.service",
]
HEAL_AT = (150, 480)  # seconds after boot: start what should be up, if it still is not
# Never speak HTTP to these: databases, brokers, SSH and SMB would log or choke on it.
NON_HTTP_PORTS = {22, 53, 139, 445, 1883, 5432, 5433, 5434, 6379}


@dataclass
class Check:
    group: str
    status: str
    text: str


# ----------------------------------------------------------------------------- small helpers

def sh(cmd, timeout=20, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", f"{type(e).__name__}: {e}"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def user_env():
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    return env


def fmt_dur(sec: float) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} j {h} h"
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def uptime_s() -> float:
    return float(Path("/proc/uptime").read_text().split()[0])


# ----------------------------------------------------------------------------- observations

def docker_containers():
    rc, out, _ = sh(["docker", "ps", "-aq"])
    if rc:
        return None
    ids = out.split()
    if not ids:
        return {}
    rc, out, _ = sh(["docker", "inspect", *ids], timeout=60)
    if rc:
        return None
    res = {}
    for c in json.loads(out):
        st, hc = c.get("State", {}), c.get("HostConfig", {})
        ports = set()
        for key, binds in (c.get("NetworkSettings", {}).get("Ports") or {}).items():
            for b in binds or []:
                ports.add((int(b["HostPort"]), key.split("/")[1]))
        res[c["Name"].lstrip("/")] = {
            "policy": (hc.get("RestartPolicy") or {}).get("Name") or "no",
            "status": st.get("Status"), "running": bool(st.get("Running")),
            "restarting": bool(st.get("Restarting")),
            "health": (st.get("Health") or {}).get("Status"), "has_health": bool(st.get("Health")),
            "exit": st.get("ExitCode"), "net": hc.get("NetworkMode"),
            "image": (c.get("Config") or {}).get("Image", ""),
            "ports": sorted(ports),
            "networks": {n: v.get("IPAddress") for n, v in
                         (c.get("NetworkSettings", {}).get("Networks") or {}).items()},
        }
    return res


def macvlan_networks() -> set:
    rc, out, _ = sh(["docker", "network", "ls", "--filter", "driver=macvlan", "--format", "{{.Name}}"])
    return set(out.split()) if rc == 0 else set()


def parse_ss(text: str) -> set:
    ports = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            m = re.search(r":(\d+)$", parts[3])
            if m:
                ports.add(int(m.group(1)))
    return ports


def listening(proto: str) -> set:
    rc, out, _ = sh(["ss", "-H", "-ltn" if proto == "tcp" else "-lun"])
    return parse_ss(out) if rc == 0 else set()


def interfaces() -> dict:
    rc, out, _ = sh(["ip", "-j", "-4", "addr", "show"])
    res = {}
    if rc == 0:
        for itf in json.loads(out):
            name = itf["ifname"]
            if re.match(r"(lo$|veth|br-|docker|virbr)", name):
                continue
            ips = sorted(a["local"] for a in itf.get("addr_info", []))
            if ips:
                res[name] = ips
    return res


def route_dev(ip: str):
    rc, out, _ = sh(["ip", "-j", "route", "get", ip])
    if rc == 0:
        try:
            return json.loads(out)[0].get("dev")
        except (ValueError, IndexError):
            return None
    return None


def parse_mdstat(text: str) -> dict:
    res, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^(md\d+)\s*:", line)
        if m:
            cur = m.group(1)
            continue
        m = re.search(r"\[\d+/\d+\]\s+\[([U_]+)\]", line)
        if cur and m:
            res[cur] = m.group(1)
            cur = None
    return res


def raid_status() -> dict:
    p = Path("/proc/mdstat")
    return parse_mdstat(p.read_text()) if p.exists() else {}


def unit_state(unit: str, user: bool = False) -> dict:
    cmd = ["systemctl"] + (["--user"] if user else []) + [
        "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "LoadState",
        "-p", "UnitFileState", "-p", "NextElapseUSecRealtime"]
    rc, out, _ = sh(cmd, env=user_env() if user else None)
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l) if rc == 0 else {}


def failed_units(user: bool = False) -> set:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["--failed", "--no-legend", "--plain"]
    rc, out, _ = sh(cmd, env=user_env() if user else None)
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if parts:
            names.add(parts[0].lstrip("●* "))
    names.discard("")
    return names


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def http_probe(host: str, port: int, path: str = "/", timeout: float = 5.0):
    req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"User-Agent": "nas-boot-check/1.0"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def dns_query(server: str, name: str = "example.com", timeout: float = 3.0) -> bool:
    tid = int.from_bytes(os.urandom(2), "big")
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for label in name.split("."):
        q += bytes([len(label)]) + label.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(q, (server, 53))
            data, _ = s.recvfrom(1024)
    except OSError:
        return False
    if len(data) < 12:
        return False
    rid, flags, _qd, an = struct.unpack(">HHHH", data[:8])
    return rid == tid and bool(flags & 0x8000) and (flags & 0xF) == 0 and an > 0


def internet() -> dict:
    res = {"resolve": False, "connect": False}
    try:
        infos = socket.getaddrinfo("discord.com", 443, type=socket.SOCK_STREAM)
        res["resolve"] = True
        addr = infos[0][4]
        with socket.create_connection((str(addr[0]), int(addr[1])), timeout=5):
            res["connect"] = True
    except OSError:
        pass
    return res


def gateway_state():
    try:
        d = json.loads(GATEWAY_STATE.read_text())
        d["_age"] = time.time() - GATEWAY_STATE.stat().st_mtime
        return d
    except (OSError, ValueError):
        return None


def versions() -> dict:
    rc, out, _ = sh(["docker", "version", "--format", "{{.Server.Version}}"])
    return {"kernel": os.uname().release, "docker": out if rc == 0 else "?"}


def mem_available_mib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return -1


def observe(exp: dict) -> dict:
    obs: dict = {"now": time.time(), "uptime": uptime_s()}
    obs["mounts"] = {p: os.path.ismount(p) for p in exp.get("mounts", [])}
    obs["disk"] = {}
    for p, mounted in obs["mounts"].items():
        if mounted:
            u = shutil.disk_usage(p)
            obs["disk"][p] = round(100 * u.used / u.total)
    obs["raid"] = raid_status()
    obs["docker"] = docker_containers()
    obs["tcp"], obs["udp"] = listening("tcp"), listening("udp")
    obs["http"] = {h["port"]: http_probe(h["host"], h["port"], h.get("path", "/"))
                   for h in exp.get("http", [])}
    obs["ifaces"] = interfaces()
    mv = exp.get("macvlan", [])
    obs["routes"] = {m["ip"]: route_dev(m["ip"]) for m in mv}
    obs["dns"] = {m["ip"]: dns_query(m["ip"]) for m in mv if m.get("dns")}
    obs["net"] = internet()
    names = set(exp.get("system_active", [])) | set(exp.get("system_inactive", []))
    obs["sys_units"] = {u: unit_state(u) for u in sorted(names)}
    obs["failed_sys"] = failed_units()
    obs["files"] = {p: os.path.exists(p) for p in exp.get("files", [])}
    uu = exp.get("user_units", {})
    obs["user_manager"] = sh(["systemctl", "--user", "is-system-running"], env=user_env())[1] in (
        "running", "degraded", "starting")
    obs["user_units"] = {u: unit_state(u, user=True) for u in uu.get("services", []) + uu.get("timers", [])}
    obs["failed_user"] = failed_units(user=True) if obs["user_manager"] else set()
    obs["linger"] = Path("/var/lib/systemd/linger", pwd.getpwuid(os.getuid()).pw_name).exists()
    obs["gateway"] = gateway_state()
    obs["pg"] = {}
    for name in exp.get("postgres", []):
        obs["pg"][name] = sh(["docker", "exec", name, "pg_isready", "-q"], timeout=15)[0] == 0
    obs["mem_mib"] = mem_available_mib()
    rc, out, _ = sh(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    obs["ntp"] = (out == "yes") if rc == 0 else None
    obs["versions"] = versions()
    return obs


# ----------------------------------------------------------------------------- evaluation (pure)

def evaluate(exp: dict, obs: dict) -> list:
    out: list = []

    def add(group, status, text):
        out.append(Check(group, status, text))

    # storage -----------------------------------------------------------------------------
    unmounted = [p for p, m in obs["mounts"].items() if not m]
    if unmounted:
        add("stockage", FAIL, "non monté : " + ", ".join(unmounted))
    elif obs["mounts"]:
        add("stockage", OK, "montés : " + " ".join(obs["mounts"]))
    if obs["raid"]:
        degraded = [f"{md} [{pat}]" for md, pat in obs["raid"].items() if "_" in pat]
        if degraded:
            add("stockage", FAIL, "RAID dégradé : " + ", ".join(degraded))
        else:
            add("stockage", OK, "RAID " + ", ".join(f"{md} [{pat}]" for md, pat in obs["raid"].items()))
    for path, pct in obs["disk"].items():
        if pct >= 98:
            add("stockage", FAIL, f"{path} plein à {pct} %")
        elif pct >= 90:
            add("stockage", WARN, f"{path} plein à {pct} %")

    # docker ------------------------------------------------------------------------------
    d = obs["docker"]
    exp_c = exp.get("containers", {})
    if d is None:
        add("docker", FAIL, "daemon Docker injoignable")
    else:
        problems = []
        for name, meta in exp_c.items():
            c = d.get(name)
            if c is None:
                problems.append(f"{name} : absent")
            elif c["restarting"]:
                problems.append(f"{name} : redémarre en boucle")
            elif not c["running"]:
                problems.append(f"{name} : arrêté ({c['status']}, exit {c['exit']})")
            elif meta.get("has_health") and c["health"] != "healthy":
                problems.append(f"{name} : {c['health'] or 'sans état de santé'}")
        total = len(exp_c)
        if problems:
            add("conteneurs", FAIL, f"{total - len(problems)}/{total} OK — " + " ; ".join(problems))
        else:
            add("conteneurs", OK, f"{total}/{total} en marche (healthchecks verts)")
        stray = [n for n, c in d.items() if n not in exp_c and n not in exp.get("stopped_on_purpose", [])
                 and c["policy"] != "no" and not c["running"] and c["exit"] not in (0, None)]
        if stray:
            add("conteneurs", WARN, "arrêtés hors référence : " + ", ".join(sorted(stray)))
        new = [n for n, c in d.items() if c["running"] and n not in exp_c]
        if new:
            add("conteneurs", INFO, "en marche mais absents de la référence : " + ", ".join(sorted(new)))
    for name, ok in obs["pg"].items():
        add("postgres", OK if ok else FAIL, f"{name} " + ("accepte les connexions" if ok else "ne répond pas à pg_isready"))

    # ports + http ------------------------------------------------------------------------
    ports = exp.get("ports", {})
    miss = [f"{p}/{pr}" for p, pr in ports.get("critical", []) if p not in obs[pr]]
    if miss:
        add("ports", FAIL, "à l'écoute ? non : " + ", ".join(miss))
    elif ports.get("critical"):
        add("ports", OK, f"{len(ports['critical'])} ports critiques à l'écoute")
    miss_o = [f"{p}/{pr}" for p, pr in ports.get("other", []) if p not in obs[pr]]
    if miss_o:
        add("ports", WARN, "autres ports absents : " + ", ".join(miss_o))
    bad_http = [f"{p} → {s if s is not None else 'pas de réponse'}" for p, s in obs["http"].items()
                if s is None or s >= 500]
    if bad_http:
        add("http", FAIL, "services web en erreur : " + ", ".join(bad_http))
    elif obs["http"]:
        add("http", OK, f"{len(obs['http'])} services web répondent")

    # network -----------------------------------------------------------------------------
    nic = []
    for name, ips in exp.get("interfaces", {}).items():
        cur = obs["ifaces"].get(name)
        if cur is None:
            nic.append(f"{name} absente")
        elif set(ips) - set(cur):
            nic.append(f"{name} sans {', '.join(sorted(set(ips) - set(cur)))}")
    if nic:
        add("réseau", FAIL, "interfaces : " + " ; ".join(nic))
    elif exp.get("interfaces"):
        add("réseau", OK, "interfaces " + " ".join(exp["interfaces"]) + " présentes avec leurs adresses")
    for m in exp.get("macvlan", []):
        dev = obs["routes"].get(m["ip"])
        if dev != m.get("dev"):
            add("réseau", FAIL, f"route vers {m['container']} ({m['ip']}) par {dev or '?'} au lieu de {m.get('dev')}")
        elif m.get("dns") and not obs["dns"].get(m["ip"]):
            add("réseau", FAIL, f"{m['container']} ({m['ip']}) ne répond pas en DNS")
        else:
            add("réseau", OK, f"{m['container']} joignable via {dev}" + (", DNS répond" if m.get("dns") else ""))
    net = obs["net"]
    if net["resolve"] and net["connect"]:
        add("internet", OK, "résolution DNS + connexion HTTPS sortante")
    else:
        add("internet", FAIL, "pas de résolution DNS" if not net["resolve"] else "pas de connexion sortante")
    if obs["ntp"] is False:
        add("horloge", WARN, "horloge pas encore synchronisée (NTP)")

    # system units ------------------------------------------------------------------------
    down = [u for u in exp.get("system_active", []) if obs["sys_units"].get(u, {}).get("ActiveState") != "active"]
    if down:
        add("système", FAIL, "unités arrêtées : " + ", ".join(down))
    elif exp.get("system_active"):
        add("système", OK, f"{len(exp['system_active'])} unités système actives")
    for u in exp.get("system_inactive", []):
        st = obs["sys_units"].get(u, {})
        if st.get("ActiveState") == "active" or st.get("UnitFileState") == "enabled":
            add("sécurité", FAIL, f"{u} est revenu ({st.get('ActiveState')}/{st.get('UnitFileState')}) alors qu'il doit rester arrêté")
        else:
            add("sécurité", OK, f"{u} reste arrêté et désactivé")
    lost = [p for p, ok in obs["files"].items() if not ok]
    if lost:
        add("personnalisations", FAIL, "fichiers root disparus (mise à jour ?) : " + ", ".join(lost))
    elif obs["files"]:
        add("personnalisations", OK, f"{len(obs['files'])} fichiers root personnalisés toujours là")
    shim = obs["sys_units"].get("macvlan-shim.service", {})
    if shim and shim.get("UnitFileState") not in (None, "enabled"):
        add("personnalisations", FAIL, f"macvlan-shim.service n'est plus activé ({shim.get('UnitFileState')})")
    base = exp.get("failed_baseline", {})
    new_sys = sorted(obs["failed_sys"] - set(base.get("system", [])))
    new_usr = sorted(obs["failed_user"] - set(base.get("user", [])))
    if new_sys or new_usr:
        add("système", WARN, "nouvelles unités en échec : " + ", ".join(new_sys + new_usr))

    # user units + Hermes -----------------------------------------------------------------
    uu = exp.get("user_units", {})
    if uu:
        if not obs["linger"]:
            add("utilisateur", FAIL, "linger désactivé : rien ne démarre sans session ouverte")
        if not obs["user_manager"]:
            add("utilisateur", FAIL, "le gestionnaire systemd --user ne tourne pas")
        else:
            off = [u for u in uu.get("services", []) if obs["user_units"].get(u, {}).get("ActiveState") != "active"]
            off += [u for u in uu.get("timers", []) if obs["user_units"].get(u, {}).get("ActiveState") != "active"
                    or not obs["user_units"].get(u, {}).get("NextElapseUSecRealtime")]
            n = len(uu.get("services", [])) + len(uu.get("timers", []))
            if off:
                add("utilisateur", FAIL, "unités utilisateur arrêtées : " + ", ".join(off))
            else:
                add("utilisateur", OK, f"{n} unités utilisateur actives (gateway, dashboard, minuteries)")
    gw = obs["gateway"]
    if exp.get("hermes_gateway"):
        if gw is None:
            add("hermes", FAIL, "gateway_state.json illisible : gateway pas démarrée ?")
        else:
            problems = []
            if gw.get("gateway_state") != "running":
                problems.append(f"état {gw.get('gateway_state')}")
            try:
                os.kill(int(gw.get("pid", 0)), 0)
            except (OSError, ValueError):
                problems.append("processus absent")
            for pf, st in (gw.get("platforms") or {}).items():
                if st.get("state") != "connected":
                    problems.append(f"{pf} : {st.get('state')}")
            if problems:
                add("hermes", FAIL, "gateway : " + ", ".join(problems))
            else:
                add("hermes", OK, "gateway en marche, " + ", ".join(
                    f"{pf} connecté" for pf in (gw.get("platforms") or {})) )
            if gw["_age"] > 1800:
                add("hermes", WARN, f"gateway_state.json pas rafraîchi depuis {fmt_dur(gw['_age'])}")

    # resources + versions ----------------------------------------------------------------
    if 0 <= obs["mem_mib"] < 300:
        add("mémoire", WARN, f"seulement {obs['mem_mib']} Mio disponibles (earlyoom tue sous 64)")
    prev = exp.get("versions", {})
    cur = obs["versions"]
    changed = [f"{k} {prev[k]} → {cur[k]}" for k in ("kernel", "docker") if prev.get(k) and prev[k] != cur.get(k)]
    if changed:
        add("mise à jour", INFO, "détectée : " + ", ".join(changed) + " (refaire `snapshot` si tout est vert)")
    return out


# ----------------------------------------------------------------------------- healing

def plan_heal(exp: dict, obs: dict) -> dict:
    plan = {"user_units": [], "containers": []}
    if obs["user_manager"]:
        uu = exp.get("user_units", {})
        plan["user_units"] = [u for u in uu.get("services", []) + uu.get("timers", [])
                              if obs["user_units"].get(u, {}).get("ActiveState") != "active"]
    d = obs["docker"] or {}
    down = [n for n in exp.get("containers", {}) if n in d and not d[n]["running"] and not d[n]["restarting"]]
    first = [n for n in down if n in ("common-postgres", "common-redis")]
    plan["containers"] = first + [n for n in down if n not in first]
    return plan


def do_heal(plan: dict, dry: bool = False) -> list:
    done = []
    if plan["user_units"]:
        if not dry:
            sh(["systemctl", "--user", "daemon-reload"], env=user_env())
            sh(["systemctl", "--user", "start", *plan["user_units"]], env=user_env(), timeout=90)
        done.append("unités utilisateur lancées : " + ", ".join(plan["user_units"]))
    for name in plan["containers"]:
        if not dry:
            sh(["docker", "start", name], timeout=60)
        done.append(f"conteneur démarré : {name}")
    return done


# ----------------------------------------------------------------------------- expectation

def load_previous() -> dict:
    try:
        return json.loads(EXPECTED.read_text())
    except (OSError, ValueError):
        return {}


def load_expected() -> dict:
    exp = load_previous()
    if not exp:
        sys.exit(f"no expectation at {EXPECTED}: run `nas-boot-check snapshot` on a healthy box first")
    return exp


def lan_ip(ifaces: dict):
    for name in ("bridge0", "eth0"):
        if ifaces.get(name):
            return ifaces[name][0]
    return next((v[0] for v in ifaces.values() if v), None)


def cmd_snapshot(args) -> int:
    prev = load_previous()
    d = docker_containers()
    if d is None:
        sys.exit("docker is unreachable: nothing to record")
    keep = ("always", "unless-stopped")
    exp: dict = {"taken": datetime.now().astimezone().isoformat(timespec="seconds")}
    exp["mounts"] = [p for p in ("/volume1", "/volume2", "/home") if os.path.ismount(p)]
    exp["containers"] = {n: {"has_health": c["has_health"]} for n, c in sorted(d.items())
                         if c["running"] and c["policy"] in keep}
    exp["stopped_on_purpose"] = sorted(n for n, c in d.items() if not c["running"] and c["policy"] in keep)
    exp["postgres"] = sorted(n for n in exp["containers"] if d[n]["image"].startswith("postgres"))

    critical = {tuple(p) for p in prev.get("ports", {}).get("critical_extra", [])}
    for spec in args.critical_port or []:
        port, _, proto = spec.partition("/")
        critical.add((int(port), proto or "tcp"))
    extra = sorted(critical)
    for n in exp["containers"]:
        critical.update(d[n]["ports"])
    tcp, udp = listening("tcp"), listening("udp")
    critical = {(p, pr) for p, pr in critical if p in (tcp if pr == "tcp" else udp)}
    other = {(p, "tcp") for p in tcp if p < 32768} | {(p, "udp") for p in udp if p < 32768}
    exp["ports"] = {"critical": sorted(critical), "critical_extra": [list(x) for x in extra],
                    "other": sorted(other - critical)}
    exp["ports"]["critical"] = [list(x) for x in exp["ports"]["critical"]]
    exp["ports"]["other"] = [list(x) for x in exp["ports"]["other"]]

    ifs = interfaces()
    exp["interfaces"] = ifs
    lan = lan_ip(ifs)
    hosts = ["127.0.0.1"] + ([lan] if lan else [])
    exp["http"] = []
    for port, proto in sorted(critical):
        if proto != "tcp" or port in NON_HTTP_PORTS:
            continue
        for h in hosts:
            if http_probe(h, port) is not None:
                exp["http"].append({"host": h, "port": port, "path": "/"})
                break
    mv = macvlan_networks()
    exp["macvlan"] = []
    for n, c in sorted(d.items()):
        if n in exp["containers"]:
            for net, ip in c["networks"].items():
                if net in mv and ip:
                    exp["macvlan"].append({"container": n, "ip": ip, "dev": route_dev(ip), "dns": dns_query(ip)})
    exp["system_active"] = [u for u in SYSTEM_CANDIDATES if unit_state(u).get("ActiveState") == "active"]
    exp["system_inactive"] = [u for u in MUST_STAY_OFF if unit_state(u).get("ActiveState") != "active"]
    exp["files"] = [p for p in CUSTOM_FILES if os.path.exists(p)]
    exp["failed_baseline"] = {"system": sorted(failed_units()), "user": sorted(failed_units(user=True))}
    services, timers = [], []
    if USER_UNIT_DIR.is_dir():
        for f in sorted(USER_UNIT_DIR.glob("*.service")) + sorted(USER_UNIT_DIR.glob("*.timer")):
            st = unit_state(f.name, user=True)
            if st.get("ActiveState") == "active":
                (timers if f.suffix == ".timer" else services).append(f.name)
    exp["user_units"] = {"services": services, "timers": timers}
    exp["hermes_gateway"] = "hermes-gateway.service" in services
    exp["versions"] = versions()
    STATE.mkdir(parents=True, exist_ok=True)
    EXPECTED.write_text(json.dumps(exp, indent=1, ensure_ascii=False))
    EXPECTED.chmod(0o600)
    print(f"recorded {EXPECTED}: {len(exp['containers'])} containers, {len(exp['ports']['critical'])} critical ports "
          f"(+{len(exp['ports']['other'])} others), {len(exp['http'])} web probes, {len(exp['macvlan'])} macvlan, "
          f"{len(exp['system_active'])} system units, {len(services)} user services, {len(timers)} timers")
    print("stopped on purpose (not expected up):", ", ".join(exp["stopped_on_purpose"]) or "none")
    return 0


# ----------------------------------------------------------------------------- reporting

def render_cli(checks: list) -> str:
    return "\n".join(f"{ICON[c.status]} {c.group:<18} {c.text}" for c in checks)


def webhook_url() -> str:
    try:
        for line in WEBHOOK_ENV.read_text().splitlines():
            if line.startswith("DISCORD_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def send_discord(text: str) -> bool:
    url = webhook_url()
    if not url.startswith("https://"):
        return False
    body = json.dumps({"content": text[:1990], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                          "User-Agent": "nas-boot-check/1.0"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
            return 200 <= r.status < 300
    except Exception:  # never log the URL: it is a secret
        return False


def render_discord(checks: list, uptime: float, took: float, healed: list) -> str:
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    infos = [c for c in checks if c.status == INFO]
    boot = datetime.fromtimestamp(time.time() - uptime).strftime("%d/%m %H:%M")
    what = "NAS redémarré" if uptime < 1800 else f"Contrôle manuel (NAS allumé depuis {fmt_dur(uptime)})"
    if fails:
        lines = [f"❌ **{what} : {len(fails)} problème(s) après {fmt_dur(took)}** (boot {boot})"]
        lines += [f"• {c.group} : {c.text}" for c in fails]
    else:
        lines = [f"✅ **{what} : tout est revenu** ({'vert après ' + fmt_dur(uptime) if uptime < 1800 else 'tout est vert'}, boot {boot})"]
        lines += [f"• {c.group} : {c.text}" for c in checks if c.status == OK and c.group in
                  ("conteneurs", "hermes", "utilisateur", "réseau", "sécurité", "stockage")][:8]
    if warns:
        lines.append("⚠️ " + " ; ".join(f"{c.group} : {c.text}" for c in warns))
    if healed:
        lines.append("🔧 relancé automatiquement : " + " ; ".join(healed))
    if infos:
        lines.append("ℹ️ " + " ; ".join(f"{c.group} {c.text}" for c in infos))
    if fails:
        lines.append("À reprendre en SSH : `nas-boot-check run`, puis `systemctl --user daemon-reload && "
                     "systemctl --user start hermes-gateway hermes-dashboard` si Hermes manque.")
    return "\n".join(lines)


def save_last(obs: dict, checks: list) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    LAST.write_text(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "versions": obs["versions"], "uptime": obs["uptime"],
                                "checks": [c.__dict__ for c in checks]}, ensure_ascii=False, indent=1))


def cmd_run(args) -> int:
    exp = load_expected()
    obs = observe(exp)
    checks = evaluate(exp, obs)
    if args.json:
        print(json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=1))
    else:
        print(f"nas-boot-check — NAS allumé depuis {fmt_dur(obs['uptime'])} · noyau {obs['versions']['kernel']} · "
              f"docker {obs['versions']['docker']} · référence du {exp.get('taken', '?')[:16]}")
        print(render_cli(checks))
        n = {s: sum(1 for c in checks if c.status == s) for s in (OK, WARN, FAIL)}
        print(f"\n{n[OK]} ok · {n[WARN]} avertissement(s) · {n[FAIL]} échec(s)")
    save_last(obs, checks)
    return 1 if any(c.status == FAIL for c in checks) else 0


def cmd_boot(args) -> int:
    up0 = uptime_s()
    if up0 > 1800 and not args.force:
        print(f"NAS allumé depuis {fmt_dur(up0)}: ce n'est pas un démarrage (--force pour contrôler quand même)")
        return 0
    exp = load_expected()
    started = time.time()
    t_boot = started - up0
    deadline = started + args.timeout
    healed, heals_left = [], list(HEAL_AT)
    heal_allowed = not args.no_heal and (up0 < 1800 or args.heal)  # a manual check on a long-running box only looks
    while True:
        obs = observe(exp)
        checks = evaluate(exp, obs)
        fails = [c for c in checks if c.status == FAIL]
        if not fails or time.time() >= deadline:
            break
        if heals_left and heal_allowed and time.time() - t_boot >= heals_left[0]:
            heals_left.pop(0)
            healed += [h for h in do_heal(plan_heal(exp, obs), dry=args.dry_run) if h not in healed]
        time.sleep(args.interval)
    took = time.time() - t_boot if up0 < 1800 else time.time() - started
    msg = render_discord(checks, obs["uptime"], took, healed)
    print(msg)
    sent = False
    if args.dry_run:
        print("(dry run: nothing sent to Discord)")
    else:
        for attempt in range(10):  # the network can be late right after a boot
            sent = send_discord(msg)
            if sent:
                break
            time.sleep(30)
        print("Discord:", "sent" if sent else "NOT sent")
    save_last(obs, checks)
    STATE.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a") as f:
        f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "uptime_at_start": round(up0), "seconds_to_verdict": round(took),
                            "fail": len(fails), "warn": sum(1 for c in checks if c.status == WARN),
                            "healed": healed, "discord": sent, **obs["versions"]}, ensure_ascii=False) + "\n")
    return 1 if fails else 0


def guarded_boot(args) -> int:
    """A watcher that dies silently is the worst failure: say so on Discord if `boot` itself crashes."""
    try:
        return cmd_boot(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — last line of defence
        text = f"❌ **nas-boot-check a planté** ({type(e).__name__}: {str(e)[:300]}) — contrôle à faire à la main : `nas-boot-check run`"
        print(text, file=sys.stderr)
        if not args.dry_run:
            send_discord(text)
        return 2


def cmd_show(_args) -> int:
    print(json.dumps(load_expected(), indent=1, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nas-boot-check", description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="one pass over every check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("boot", help="wait for the box to settle, heal, post the verdict on Discord")
    p.add_argument("--force", action="store_true", help="run even if the NAS has been up for > 30 min")
    p.add_argument("--dry-run", action="store_true", help="start nothing, send nothing")
    p.add_argument("--no-heal", action="store_true")
    p.add_argument("--heal", action="store_true", help="with --force: also start what the expectation says should be up")
    p.add_argument("--timeout", type=int, default=900, help="seconds to wait for green (default 900)")
    p.add_argument("--interval", type=int, default=20)
    p.set_defaults(fn=guarded_boot)
    p = sub.add_parser("snapshot", help="record the current healthy state as the expectation")
    p.add_argument("--critical-port", action="append", metavar="PORT/PROTO",
                   help="a port that must listen again (host-network services such as 8480/tcp, 51820/udp)")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("show", help="print the recorded expectation")
    p.set_defaults(fn=cmd_show)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
