#!/usr/bin/env python3
"""Report which services of a compose project differ from their compose file.

  compose_drift.py <project dir>     -> JSON on stdout

A service drifts when its container is missing, not running (restart policy
other than "no"), its image differs from the local image of its tag, or its
configuration changed. The configuration check depends on who created the
container:

- Created by Compose (label `com.docker.compose.image` == the container's
  image ID): Compose's own `up --dry-run` decides. It is the only exact check:
  the hash `up` writes for services with an `env_file` cannot be reproduced
  with `docker compose config --hash`.
- Recreated by the UGOS Docker auto-update (that label is a manifest digest):
  `--dry-run` always says "Recreate" there, so the `config-hash` label is
  compared with `config --hash` instead, which is exact only for services
  without `env_file` and for the same Compose version. Otherwise the service
  is noted as "not comparable" and left alone (COMPOSE_DRIFT_STRICT=1 counts
  it as drift).

Read-only (`--dry-run` changes nothing). Stdlib only (host /usr/bin/python3).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

STRICT = os.environ.get("COMPOSE_DRIFT_STRICT") == "1"


def run(*args: str, cwd: str | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(args[:4])} failed: {proc.stderr.strip()[-300:]}")
    return proc.stdout + proc.stderr


def main(project_dir: str) -> int:
    compose_version = run("docker", "compose", "version", "--short").strip().lstrip("v")
    # stdout only: Compose prints warnings (obsolete `version`) on stderr.
    def stdout(*args: str) -> str:
        return subprocess.run(args, cwd=project_dir, capture_output=True, text=True, check=True).stdout

    config = json.loads(stdout("docker", "compose", "config", "--format", "json"))
    project = config["name"]
    hashes = dict(line.split(None, 1) for line in stdout("docker", "compose", "config", "--hash", "*").splitlines()
                  if line.strip())
    ids = run("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").split()
    containers: dict[str, list] = {}
    if ids:
        for c in json.loads(run("docker", "inspect", *ids)):
            labels = c["Config"]["Labels"] or {}
            if labels.get("com.docker.compose.oneoff") == "True":
                continue
            containers.setdefault(labels.get("com.docker.compose.service"), []).append(c)

    # What `up` itself would do, for the services that already have a container.
    recreate: set[str] = set()
    existing = sorted(s for s in config["services"] if s in containers)
    if existing:
        out = run("docker", "compose", "up", "--dry-run", "--detach", "--no-deps", "--pull", "never",
                  *existing, cwd=project_dir)
        recreate = set(re.findall(r"Container (\S+) Recreate\b", out))

    services = []
    for name, svc in sorted(config["services"].items()):
        image = svc.get("image") or f"{project}-{name}"
        local = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", image],
                               capture_output=True, text=True)
        local_id = local.stdout.strip() if local.returncode == 0 else None
        entry = {"service": name, "image": image, "build": bool(svc.get("build")),
                 "image_present": local_id is not None, "reasons": [], "notes": []}
        found = containers.get(name, [])
        restart = svc.get("restart", "no")
        if not found:
            entry["reasons"].append("no container")
        for c in found:
            labels = c["Config"]["Labels"] or {}
            cname = c["Name"].lstrip("/")
            if local_id and c["Image"] != local_id:
                entry["reasons"].append("newer local image")
            if labels.get("com.docker.compose.image") == c["Image"]:
                if cname in recreate and "newer local image" not in entry["reasons"]:
                    entry["reasons"].append("compose config changed")
            elif labels.get("com.docker.compose.config-hash") != hashes.get(name):
                made_by = labels.get("com.docker.compose.version", "?")
                if STRICT or (made_by == compose_version and not svc.get("env_file")):
                    entry["reasons"].append("compose config changed")
                else:
                    entry["notes"].append(f"recreated by UGOS (Compose {made_by}), config not comparable")
            if not c["State"]["Running"] and restart != "no":
                entry["reasons"].append(f"not running ({c['State']['Status']})")
        entry["container"] = [c["Name"].lstrip("/") for c in found]
        services.append(entry)

    networks = [n.get("name", key) for key, n in (config.get("networks") or {}).items() if n.get("external")]
    json.dump({"project": project, "external_networks": networks, "services": services,
               "drifted": [s["service"] for s in services if s["reasons"]]}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
