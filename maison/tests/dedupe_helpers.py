"""Images synthétiques « photo-like » et annonces de test pour la déduplication."""
import io

import numpy as np
from PIL import Image, ImageDraw


def scene(seed: int, size=(800, 600)) -> Image.Image:
    """Image lisse et structurée (champ aléatoire basse résolution agrandi + formes), propre à chaque graine."""
    rng = np.random.default_rng(seed)
    base = Image.fromarray(rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)).resize(size, Image.Resampling.BICUBIC)
    d = ImageDraw.Draw(base)
    for _ in range(6):
        x, y = int(rng.integers(0, size[0] - 120)), int(rng.integers(0, size[1] - 120))
        w, h = int(rng.integers(40, 200)), int(rng.integers(40, 200))
        col = tuple(int(c) for c in rng.integers(0, 255, 3))
        (d.rectangle if rng.random() < 0.5 else d.ellipse)([x, y, x + w, y + h], fill=col)
    return base


def jpeg(img: Image.Image, quality=90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def thumbnail(img: Image.Image, size=(500, 370)) -> Image.Image:
    return img.resize(size, Image.Resampling.LANCZOS)


def watermark(img: Image.Image) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    d.rectangle([w - 170, h - 50, w - 10, h - 10], fill=(255, 255, 255))
    d.text((w - 160, h - 40), "AGENCE IMMO", fill=(20, 20, 120))
    return out


def crop(img: Image.Image, frac=0.03) -> Image.Image:
    w, h = img.size
    dx, dy = int(w * frac / 2), int(h * frac / 2)
    return img.crop((dx, dy, w - dx, h - dy))


def uniform(color=(250, 250, 250), size=(400, 300)) -> Image.Image:
    img = Image.new("RGB", size, color)
    ImageDraw.Draw(img).text((150, 140), "LOGO", fill=(200, 200, 200))
    return img
