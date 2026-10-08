#!/usr/bin/env python3
"""Run a shell command inside a helper container that has the docker CLI.

The Hermes sandbox has /var/run/docker.sock but no docker binary and no apt access,
so we drive the host daemon via its HTTP API: a long-lived `hermes-dockercli`
container (image docker:cli) with the socket and /volume1/docker mounted at the
same paths, and `docker exec`-equivalent calls through the API.

Usage: dk 'cd /volume1/docker/maison && docker compose ps'
"""
import http.client, json, socket, struct, sys

SOCK = "/var/run/docker.sock"
NAME = "hermes-dockercli"
IMAGE = "docker:cli"


class UConn(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=3600)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(SOCK)


def api(method, path, body=None, raw=False):
    c = UConn()
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    if raw:
        return r.status, data
    try:
        return r.status, json.loads(data) if data else None
    except ValueError:
        return r.status, data.decode(errors="replace")


def ensure():
    st, info = api("GET", f"/containers/{NAME}/json")
    if st == 200:
        if not info["State"]["Running"]:
            api("POST", f"/containers/{NAME}/start")
        return
    api("POST", f"/images/create?fromImage=docker&tag=cli", raw=True)
    st, res = api("POST", f"/containers/create?name={NAME}", {
        "Image": IMAGE, "Cmd": ["sleep", "infinity"], "WorkingDir": "/volume1/docker",
        "HostConfig": {"Binds": ["/var/run/docker.sock:/var/run/docker.sock",
                                 "/volume1/docker:/volume1/docker"],
                       "RestartPolicy": {"Name": "unless-stopped"}}})
    if st != 201:
        sys.exit(f"create failed: {st} {res}")
    api("POST", f"/containers/{NAME}/start")


def run(cmd):
    ensure()
    st, ex = api("POST", f"/containers/{NAME}/exec", {
        "Cmd": ["sh", "-c", cmd], "AttachStdout": True, "AttachStderr": True})
    st, data = api("POST", f"/exec/{ex['Id']}/start", {"Detach": False, "Tty": False}, raw=True)
    i = 0
    while i + 8 <= len(data):
        stream, size = data[i], struct.unpack(">I", data[i + 4:i + 8])[0]
        chunk = data[i + 8:i + 8 + size].decode(errors="replace")
        (sys.stderr if stream == 2 else sys.stdout).write(chunk)
        i += 8 + size
    sys.stdout.flush()
    _, info = api("GET", f"/exec/{ex['Id']}/json")
    return info.get("ExitCode", 1)


if __name__ == "__main__":
    sys.exit(run(" ".join(sys.argv[1:])))
