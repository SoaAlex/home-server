"""Post the monthly message and its PDFs to a Discord webhook (secret never logged)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Sequence

import httpx

MAX_CONTENT = 2000
MAX_FILES = 10


class DiscordError(RuntimeError):
    """Discord refused or never acknowledged the message."""


def send_discord(
    webhook_url: str,
    content: str,
    files: Sequence[Path],
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> None:
    if not webhook_url.strip():
        raise DiscordError("DISCORD_WEBHOOK_URL manquant dans le .env")
    payload = json.dumps({"content": content[:MAX_CONTENT], "allowed_mentions": {"parse": []}})
    attachments = [Path(path) for path in files][:MAX_FILES]
    own_client = client is None
    http = client or httpx.Client(timeout=60)
    try:
        for _attempt in range(max_attempts):
            # Always multipart (Discord's documented payload_json form), even without attachments.
            multipart = [("payload_json", (None, payload, "application/json"))] + [
                (f"files[{index}]", (path.name, path.read_bytes(), "application/pdf"))
                for index, path in enumerate(attachments)
            ]
            try:
                response = http.post(webhook_url, params={"wait": "true"}, files=multipart)
            except httpx.HTTPError as exc:
                raise DiscordError(f"Discord injoignable ({type(exc).__name__})") from None
            if response.status_code == 429:
                delay = response.headers.get("Retry-After")
                if delay is None:
                    try:
                        delay = response.json().get("retry_after", 1)
                    except ValueError:
                        delay = 1
                sleep(min(float(delay), 30.0))
                continue
            if 200 <= response.status_code < 300:
                return
            raise DiscordError(f"Discord a refusé le message (HTTP {response.status_code})")
        raise DiscordError("Discord limite les envois (HTTP 429) : réessayer plus tard")
    finally:
        if own_client:
            http.close()
