#!/usr/bin/env python3
"""Render the pages written by build.py to PNG files next to this script.

Usage:
  python3 -I render.py --skill DIR [--work DIR] [--local]

By default Chromium runs in a throwaway Playwright container, so you need Docker
and nothing else: the Python package is installed once into WORK/pylib. With
--local, the Playwright package and Chromium of this machine are used instead.
The PNGs are the light theme at 2x, cropped to the title, drawing, legend, and
footer of each page.
"""
import argparse, os, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_WORK = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "home-server-diagrams"
IMAGE = "mcr.microsoft.com/playwright/python:v1.48.0-noble"
PLAYWRIGHT = "playwright==1.48.0"          # must match the browser build inside IMAGE
CONTAINER = "home-server-diagrams-render"


def docker(*args):
    return subprocess.run(["docker", *args], check=True)


def main():
    ap = argparse.ArgumentParser(description="Render the built diagram pages to PNG.")
    ap.add_argument("--skill", required=True, type=pathlib.Path, help="skills/stack-diagram folder of the skill")
    ap.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK, help=f"work folder (default {DEFAULT_WORK})")
    ap.add_argument("--local", action="store_true", help="use the Playwright installed on this machine")
    a = ap.parse_args()
    skill, work = a.skill.expanduser().resolve(), a.work.expanduser().resolve()
    pages = sorted((work / "html").glob("*.html"))
    if not pages:
        raise SystemExit(f"no page in {work / 'html'}: run build.py first")
    render_png = skill / "scripts" / "render_png.py"
    opts = ["--selector", ".page", "--scale", "2", "--single"]

    if a.local:
        for p in pages:
            subprocess.run([sys.executable, str(render_png), str(p), str(HERE / f"{p.stem}.png"), *opts], check=True)
        return

    uid_gid = f"{os.getuid()}:{os.getgid()}"
    if not (work / "pylib").exists():
        docker("run", "--rm", "-v", f"{work}:/w", IMAGE, "sh", "-c",
               f"pip install -q --break-system-packages --root-user-action=ignore --disable-pip-version-check "
               f"--target /w/pylib {PLAYWRIGHT} && chown -R {uid_gid} /w/pylib")
    script = ('for f in /w/html/*.html; do b=$(basename "$f" .html); '
              f'python3 /sk/scripts/render_png.py "$f" "/out/$b.png" {" ".join(opts)} 2>&1 | grep -E "^rendered|rror" ; '
              f'done; chown {uid_gid} /out/*.png')
    try:
        docker("run", "--rm", "--name", CONTAINER, "--memory", "1g", "--oom-score-adj", "1000",
               "-e", "PYTHONPATH=/w/pylib", "-v", f"{work}:/w", "-v", f"{skill}:/sk:ro", "-v", f"{HERE}:/out",
               IMAGE, "sh", "-c", script)
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


if __name__ == "__main__":
    main()
