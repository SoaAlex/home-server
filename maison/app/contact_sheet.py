"""Planche contact pour l'agent (design D11) : au plus 12 photos locales du bien, numérotées dans l'ordre de
l'annonce, en vignettes de 240 × 180 sur 4 colonnes (960 px de large au plus), lisibles en un appel de vision."""
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from app.aerial import font

MAX_PHOTOS, COLS, THUMB_W, THUMB_H = 12, 4, 240, 180


def local_photos(row: dict) -> list[str]:
    """Photos locales du bien dans l'ordre affiché : celles du groupe fusionné (sans doublon visuel), sinon les
    siennes. Chemins relatifs au dossier des photos."""
    cl = row.get("cluster") or {}
    if cl.get("photos"):
        return [p[len("/photos/"):] for p in cl["photos"] if p.startswith("/photos/")]
    return list(row.get("photos") or [])


def build(base: Path, rels: list[str]) -> bytes | None:
    """JPEG de la planche, ou None si aucune des photos n'est lisible. Le numéro d'une vignette est l'indice de la
    photo dans la liste du bien (une photo illisible laisse un trou dans la numérotation)."""
    thumbs = []
    for i, rel in enumerate(rels[:MAX_PHOTOS]):
        try:
            with Image.open(base / rel) as im:
                im.draft("RGB", (THUMB_W * 2, THUMB_H * 2))         # décodage JPEG réduit : rapide
                thumbs.append((i, ImageOps.pad(im.convert("RGB"), (THUMB_W, THUMB_H), color=(0, 0, 0))))
        except (OSError, ValueError):
            continue
    if not thumbs:
        return None
    n = len(thumbs)
    sheet = Image.new("RGB", (min(n, COLS) * THUMB_W, -(-n // COLS) * THUMB_H), (0, 0, 0))
    d = ImageDraw.Draw(sheet)
    f = font(22)
    for k, (i, im) in enumerate(thumbs):
        x, y = (k % COLS) * THUMB_W, (k // COLS) * THUMB_H
        sheet.paste(im, (x, y))
        label = str(i)
        box = d.textbbox((0, 0), label, font=f)
        d.rectangle((x, y, x + box[2] + 12, y + box[3] + 8), fill=(255, 220, 0))
        d.text((x + 6, y + 2), label, font=f, fill=(0, 0, 0))
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=82)
    return out.getvalue()
