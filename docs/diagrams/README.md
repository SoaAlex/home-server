# Infrastructure diagrams

This folder holds the public infrastructure diagrams shown in the main
[`README.md`](../../README.md), and the scripts that generate them. The figures
are drawn with the [stack-diagram](https://github.com/caru-ini/stack-diagram)
skill, which puts real logos on a graph-paper canvas and routes the arrows
around the labels.

<!-- prettier-ignore -->
> [!IMPORTANT]
> Keep this version public-safe: no LAN addresses, VPN peers, hostnames of
> private services, or known weak spots. `build.py` stops when a label holds an
> IP address, a domain name, or a word from your own deny list. The full version
> lives outside the repository.

## Files

The builder writes one self-contained HTML page per figure, and the renderer
turns each page into a PNG.

| File | Purpose |
|---|---|
| `build.py` | The seven figures, the layout checks, and the page builder |
| `render.py` | Renders each page to a PNG at 2x, in the light theme |
| `icons.txt` | The logos to download, one `fetch_icons.py` spec per line |
| `01-overview.png` … `07-storage.png` | The images embedded in the main README |

## Requirements

To rebuild the images, you need the following:

- Python 3.9 or later. Pillow is optional: it shrinks two large logos.
- Docker, to render in a throwaway Playwright container. Without Docker, run
  `render.py --local` on a machine with Playwright and Chromium.
- Network access, because the logos are downloaded at build time. They aren't
  stored in this repository.
- A clone of the skill. The scripts were tested with commit `1ed53e3` of
  November 3, 2026:

  ```bash
  git clone https://github.com/caru-ini/stack-diagram ~/stack-diagram
  ```

## Update the diagrams

The diagram content is written in `build.py`, so you update it by hand when the
infrastructure changes. To rebuild the images, follow these steps:

1. Edit the nodes, arrows, and labels in `build.py`. Keep each role under 20
   characters per line, or the text wraps and the arrows no longer line up.
2. From this folder, build the pages. The first run downloads the logos into
   `~/.cache/home-server-diagrams`:

   ```bash
   python3 -I build.py --skill ~/stack-diagram/skills/stack-diagram
   ```

3. Render the PNGs next to the scripts:

   ```bash
   python3 -I render.py --skill ~/stack-diagram/skills/stack-diagram
   ```

4. Open the PNGs and check that every logo shows and no arrow crosses a label.

Add `--refresh-icons` to the build command to download the logos again, and
`--work DIR` to either command to use another work folder. Unchanged figures
render to identical files, so `git status` lists only the figures you changed.

## What the builder checks

Before it writes a page, `build.py` checks the layout with the rules of the
skill and stops with the list of problems it found:

- No arrow crosses the tile or the text of a node, or the label of a group.
- No label sits on a group border or on another label.
- No name or role is long enough to wrap onto a second line.
- Every node sits inside its group and inside the canvas.
- No label holds an IP address, a domain name, or a word from your deny list.
  The list is optional: put one regular expression per line, such as a family
  name or a private hostname, in `~/.config/home-server-diagrams/deny.txt`.
  Keep that file out of the repository.

## Licenses and trademarks

The skill is MIT licensed, and its fonts (Geist and JetBrains Mono) use the SIL
Open Font License 1.1. The logos in the figures belong to their owners, and
showing one doesn't imply endorsement. They come from [svgl](https://svgl.app),
[devicon](https://devicon.dev), the project that owns each logo, and
[Lucide](https://lucide.dev) for the pictograms that have no brand.
