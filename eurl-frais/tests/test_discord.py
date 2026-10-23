import json

import httpx
import pytest

from eurl_frais.discord_delivery import DiscordError, send_discord

WEBHOOK = "https://discord.example/api/webhooks/123/secret-token"


def pdf(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 test")
    return path


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_posts_message_and_pdfs_as_one_multipart_request(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    files = [pdf(tmp_path, "RAPPORT_FRAIS_2026-09.pdf"), pdf(tmp_path, "QUITTANCE DE LOYER SEPTEMBRE 2026.pdf")]
    send_discord(WEBHOOK, "Frais septembre", files, client=client_for(handler))

    assert len(seen) == 1
    body = seen[0].content
    assert b'name="payload_json"' in body and b"Frais septembre" in body
    assert b'filename="RAPPORT_FRAIS_2026-09.pdf"' in body
    assert b'name="files[1]"' in body
    assert seen[0].url.params["wait"] == "true"


def test_mentions_are_disabled(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.content)
        return httpx.Response(204)

    send_discord(WEBHOOK, "@everyone test", [], client=client_for(handler))

    payload = seen[0].split(b'name="payload_json"')[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0]
    assert json.loads(payload)["allowed_mentions"] == {"parse": []}


def test_rate_limit_waits_then_retries(tmp_path):
    responses = [httpx.Response(429, headers={"Retry-After": "2"}, json={"retry_after": 2}), httpx.Response(200, json={})]
    sleeps = []

    send_discord(WEBHOOK, "x", [], client=client_for(lambda request: responses.pop(0)), sleep=sleeps.append)

    assert sleeps == [2.0]


def test_server_error_raises_without_leaking_the_webhook(tmp_path):
    with pytest.raises(DiscordError) as error:
        send_discord(WEBHOOK, "x", [], client=client_for(lambda request: httpx.Response(500)))

    assert "secret-token" not in str(error.value)
    assert "500" in str(error.value)


def test_missing_webhook_fails_before_any_request():
    def handler(request):
        raise AssertionError("no request expected")

    with pytest.raises(DiscordError, match="DISCORD_WEBHOOK_URL"):
        send_discord("", "x", [], client=client_for(handler))


def test_long_content_is_truncated_to_discord_limit(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.content)
        return httpx.Response(204)

    send_discord(WEBHOOK, "é" * 2500, [], client=client_for(handler))

    payload = seen[0].split(b'name="payload_json"')[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0]
    assert len(json.loads(payload)["content"]) == 2000
