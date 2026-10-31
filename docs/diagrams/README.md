# Infrastructure diagrams

This folder holds the public infrastructure diagrams shown in the main
[`README.md`](../../README.md), and the scripts that generate them. The figures
follow the hand-drawn variant of the
[diagram-design](https://github.com/cathrynlavery/diagram-design) skill.

<!-- prettier-ignore -->
> [!IMPORTANT]
> Keep this version public-safe: no LAN addresses, VPN peers, hostnames of
> private services, or known weak spots. The full version lives outside the
> repository.

## Files

The generator writes one HTML page; the renderer turns each figure of that page
into a PNG.

| File | Purpose |
|---|---|
| `sketch.py` | SVG primitives: nodes, orthogonal arrows, labels, zones, badges |
| `build.py` | The six figures and the page; writes `infrastructure.html` |
| `render.py` | Renders each figure to `<section-id>.png` at 2x |
| `infrastructure.html` | The page, viewable in any browser |
| `01-overview.png` … `06-storage.png` | The images embedded in the main README |

## Update the diagrams

The diagram content is written in `build.py`, so you update it by hand when the
infrastructure changes. To regenerate the page and the images, follow these
steps:

1. Edit the nodes, arrows, and labels in `build.py`.
2. From this folder, build the page:

   ```bash
   python3 -I build.py
   ```

3. Render the PNGs in a throwaway Playwright container:

   ```bash
   docker run --rm -v "$PWD":/w -w /w \
     mcr.microsoft.com/playwright/python:v1.48.0-noble \
     sh -c 'pip install -q --break-system-packages playwright==1.48.0 \
       && python3 render.py'
   ```

4. Open the PNGs and check that no label overlaps a box or another label.
