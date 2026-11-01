from pathlib import Path


def download(client, listing_id: int, urls: list[str], dest: Path, limit: int = 12,
             hashes: dict | None = None) -> list[str]:
    """Télécharge jusqu'à `limit` photos dans dest/<id>/NN.jpg ; renvoie les chemins relatifs.
    Si `hashes` est fourni, y range {rang: (pHash, dHash, écart type)} des photos téléchargées, calculés sur
    les octets en mémoire (déduplication : aucune relecture du disque). Les fichiers déjà présents sont gardés."""
    out = []
    d = Path(dest) / str(listing_id)
    d.mkdir(parents=True, exist_ok=True)
    for i, u in enumerate(urls[:limit]):
        f = d / f"{i:02d}.jpg"
        if not f.exists():
            try:
                r = client.get(u, follow_redirects=True, timeout=30)
                if r.status_code != 200 or not r.content:
                    continue
                f.write_bytes(r.content)
            except Exception:
                continue
            if hashes is not None:
                from app.dedupe import image_hashes      # import tardif : numpy/Pillow seulement si utile
                try:
                    hashes[i] = image_hashes(r.content)
                except Exception:
                    pass
        out.append(f"{listing_id}/{f.name}")
    return out
