#!/usr/bin/env python3
"""Named PTZ positions for the SONOFF CAM-PT2, over ONVIF AbsoluteMove.

The camera's ONVIF preset store is a stub: SetPreset returns success and
GetPresets stays empty, and the positions saved in the eWeLink app live in
SONOFF's cloud rather than on the device. Absolute positioning, however, is
exact and repeatable, so presets are kept here as coordinates instead.

Auth is WS-Security UsernameToken *digest* - the camera rejects PasswordText -
which needs a fresh nonce and timestamp per request. That is why Home Assistant
cannot simply POST a static SOAP body and talk to the camera directly.

Endpoints (all require the token, as `X-Auth-Token:` or `?token=`):
    GET  /status          current position, and which preset it matches
    GET  /presets         every stored preset
    POST /preset/<name>   move to a stored preset
    POST /capture/<name>  save the current position under <name>
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ["PTZ_HOST"]
USER = os.environ["PTZ_USER"]
PASSWORD = os.environ["PTZ_PASSWORD"]
TOKEN = os.environ["PTZ_TOKEN"]
PROFILE = os.environ.get("PTZ_PROFILE", "Profile_0")
STATE_FILE = os.environ.get("PTZ_STATE_FILE", "/data/presets.json")
LISTEN_PORT = int(os.environ.get("PTZ_PORT", "8095"))

# How close the camera must land for a position to count as "at" a preset.
# Absolute moves settle within ~0.1 deg; this is loose enough to absorb the
# drift from someone nudging the camera in the eWeLink app.
MATCH_TOLERANCE_DEG = 3.0
ARRIVAL_WAIT_SECONDS = 6.0

PTZ_SERVICE = "/onvif/ptz_service"
SOAP_ENV = "http://www.w3.org/2003/05/soap-envelope"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)
BASE64_ENC = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)

_state_lock = threading.Lock()


class OnvifError(RuntimeError):
    pass


def _security_header() -> str:
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + PASSWORD.encode()).digest()
    ).decode()
    return (
        f'<s:Header><wsse:Security xmlns:wsse="{WSSE}" xmlns:wsu="{WSU}">'
        f"<wsse:UsernameToken><wsse:Username>{USER}</wsse:Username>"
        f'<wsse:Password Type="{PASSWORD_DIGEST}">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="{BASE64_ENC}">'
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    )


def onvif_call(body: str) -> str:
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_ENV}">{_security_header()}'
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )
    request = urllib.request.Request(
        f"http://{HOST}{PTZ_SERVICE}",
        data=envelope.encode(),
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=15).read().decode()
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as 502
        raise OnvifError(f"camera unreachable: {exc}") from exc
    if "SOAP-ENV:Fault" in response or "s:Fault" in response:
        reason = re.search(r"<[\w-]*:?Text[^>]*>(.*?)</", response)
        raise OnvifError(reason.group(1) if reason else "SOAP fault")
    return response


def read_position() -> dict:
    response = onvif_call(
        '<GetStatus xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
        f"<ProfileToken>{PROFILE}</ProfileToken></GetStatus>"
    )
    position = re.search(r'<tt:PanTilt x="([-\d.]+)" y="([-\d.]+)"', response)
    if not position:
        raise OnvifError("no PanTilt in GetStatus response")
    move_status = re.search(r"<tt:PanTilt>(\w+)</tt:PanTilt>", response)
    return {
        "pan": round(float(position.group(1)), 4),
        "tilt": round(float(position.group(2)), 4),
        "moving": (move_status.group(1) if move_status else "") != "IDLE",
    }


def absolute_move(pan: float, tilt: float) -> None:
    onvif_call(
        '<AbsoluteMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
        f"<ProfileToken>{PROFILE}</ProfileToken><Position>"
        f'<PanTilt x="{pan}" y="{tilt}" '
        'xmlns="http://www.onvif.org/ver10/schema"/>'
        "</Position></AbsoluteMove>"
    )


def load_presets() -> dict:
    try:
        with open(STATE_FILE) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_presets(presets: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = f"{STATE_FILE}.tmp"
    with open(tmp, "w") as handle:
        json.dump(presets, handle, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def frigate_enabled() -> bool | None:
    """Camera enabled state straight from Frigate's HTTP API.

    Exists because HA's MQTT inbound processing proved unreliable (core bug,
    2026.7.x): the REST sensor polling /status is the state channel instead.
    /api/config reflects runtime toggles, not just the config file.
    """
    url = os.environ.get("PTZ_FRIGATE_URL", "http://frigate:5000")
    try:
        with urllib.request.urlopen(f"{url}/api/config", timeout=4) as response:
            config = json.load(response)
        return bool(config["cameras"]["cam_pt2"]["enabled"])
    except Exception:  # noqa: BLE001 - absent attribute beats a dead endpoint
        return None


def match_preset(position: dict, presets: dict) -> str | None:
    """Name of the preset the camera is currently sitting at, if any."""
    for name, preset in presets.items():
        if (
            abs(position["pan"] - preset["pan"]) <= MATCH_TOLERANCE_DEG
            and abs(position["tilt"] - preset["tilt"]) <= MATCH_TOLERANCE_DEG
        ):
            return name
    return None


def describe(presets: dict) -> dict:
    position = read_position()
    return {
        **position,
        "preset": match_preset(position, presets),
        "frigate_enabled": frigate_enabled(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ptz-preset"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Auth-Token", "")
        if not supplied:
            match = re.search(r"[?&]token=([^&]+)", self.path)
            supplied = match.group(1) if match else ""
        return secrets.compare_digest(supplied, TOKEN)

    def _respond(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> tuple[str, str]:
        path = self.path.split("?", 1)[0].rstrip("/")
        parts = [p for p in path.split("/") if p]
        return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")

    def _handle(self, method: str) -> None:
        if not self._authorized():
            self._respond(401, {"error": "bad or missing token"})
            return
        section, name = self._route()
        try:
            with _state_lock:
                self._dispatch(method, section, name)
        except OnvifError as exc:
            self._respond(502, {"error": str(exc)})

    def _dispatch(self, method: str, section: str, name: str) -> None:
        presets = load_presets()

        if method == "GET" and section == "status":
            self._respond(200, describe(presets))
        elif method == "GET" and section == "presets":
            self._respond(200, presets)
        elif method == "POST" and section == "preset":
            self._goto(name, presets)
        elif method == "POST" and section == "capture":
            self._capture(name, presets)
        else:
            self._respond(404, {"error": f"no route for {method} {self.path}"})

    def _goto(self, name: str, presets: dict) -> None:
        preset = presets.get(name) or presets.get(name.upper())
        if not preset:
            self._respond(
                404,
                {"error": f"unknown preset {name!r}", "known": sorted(presets)},
            )
            return

        # ?wait=N extends the arrival window and, crucially, RE-SENDS the move
        # while waiting: a camera waking from eWeLink sleep silently ignores
        # moves until its motors unlock, so a single send can be lost. Within
        # the window the move is repeated every 5s (only while the camera is
        # idle - it 400s on moves mid-motion) until it lands.
        wait_match = re.search(r"[?&]wait=(\d+)", self.path)
        wait = min(int(wait_match.group(1)), 55) if wait_match else ARRIVAL_WAIT_SECONDS

        absolute_move(preset["pan"], preset["tilt"])
        last_send = time.monotonic()
        deadline = last_send + wait
        position = read_position()
        while time.monotonic() < deadline:
            if not position["moving"] and match_preset(position, presets) == name:
                break
            if not position["moving"] and time.monotonic() - last_send >= 5:
                try:
                    absolute_move(preset["pan"], preset["tilt"])
                except OnvifError:
                    pass
                last_send = time.monotonic()
            time.sleep(0.5)
            position = read_position()

        self._respond(
            200,
            {
                "moved_to": name,
                "target": preset,
                **position,
                "preset": match_preset(position, presets),
                "arrived": match_preset(position, presets) == name,
            },
        )

    def _capture(self, name: str, presets: dict) -> None:
        if not name:
            self._respond(400, {"error": "preset name required"})
            return
        position = read_position()
        if position["moving"]:
            self._respond(409, {"error": "camera is still moving", **position})
            return
        presets[name] = {"pan": position["pan"], "tilt": position["tilt"]}
        save_presets(presets)
        self._respond(200, {"captured": name, **presets[name]})

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("POST")


if __name__ == "__main__":
    print(f"ptz-preset listening on :{LISTEN_PORT} for {HOST}", flush=True)
    ThreadingHTTPServer(("", LISTEN_PORT), Handler).serve_forever()
