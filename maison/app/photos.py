from pathlib import Path


def download(client, listing_id: int, urls: list[str], dest: Path, limit: int = 12) -> list[str]:
    """Télécharge jusqu'à `limit` photos dans dest/<id>/NN.jpg ; renvoie les chemins relatifs."""
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
        out.append(f"{listing_id}/{f.name}")
    return out
