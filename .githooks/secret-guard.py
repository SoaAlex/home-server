#!/usr/bin/env python3
"""Refuse files and text headed for git that carry a secret known to this machine.

Two checks. Neither parses git's patch output, which hides added lines behind quoted names,
custom diff prefixes, external diff drivers and lines that look like headers.

  names   the path is a secret carrier whatever it holds: .env files (templates such as
          .env.example pass), Terraform state and variables, vault password files, key
          stores, SSH private keys
  values  the content holds the value of a secret from a git-ignored .env file of this
          machine (UTF-8 and UTF-16, binary files included). This catches secrets that have
          no recognizable shape, which gitleaks cannot.

    secret-guard.py [--staged]       files of the staged change (pre-commit hook)
    secret-guard.py --message FILE   a commit message (commit-msg hook)
    secret-guard.py --tree           tracked and untracked-but-not-ignored files of the work tree
    secret-guard.py --history        every blob reachable from any ref, and every path in them

It prints paths, line numbers and variable names, never a value. Write `secret-guard:allow` (or
`gitleaks:allow`) on a line to accept a value that is meant to be there. Run it with `python3 -I`.
Exit status: 0 clean, 1 something found, 2 the check itself failed (treat it as a failure).
"""
import os
import re
import subprocess
import sys

MIN_LENGTH = 8
MAX_BLOB_BYTES = 50_000_000
MAX_REPORT = 40
ALLOW_MARKERS = (b"secret-guard:allow", b"gitleaks:allow")

# --- names ----------------------------------------------------------------------------------
TEMPLATE = re.compile(r".*\.(?:example|sample|template|dist)|.*\.(?:example|sample|template)\.env", re.S)
CARRIER = re.compile(
    r".*\.env|\.env\..*|.*\.tfstate|.*\.tfstate\..*|.*\.tfvars|\.vault-pass.*"
    r"|.*\.(?:kdbx|p12|pfx|jks|keystore|key)|id_(?:rsa|dsa|ecdsa|ed25519)",
    re.S,
)
ENV_FILE = re.compile(r".*\.env|\.env\..*", re.S)


def basename(path):
    return path.rsplit("/", 1)[-1].lower()


def carrier_name(path):
    name = basename(path)
    return TEMPLATE.fullmatch(name) is None and CARRIER.fullmatch(name) is not None


def env_file_name(path):
    name = basename(path)
    return TEMPLATE.fullmatch(name) is None and ENV_FILE.fullmatch(name) is not None


# --- values ---------------------------------------------------------------------------------
# A variable holds a secret when one of its words says so (AUTHOR, KEYCLOAK_*, PRIVATE_REGISTRY
# do not), plus the path prefix of a tunnel. A URL that embeds credentials is also checked on its
# password, whatever the variable is called; the user part may be empty (redis://:password@host).
SECRET_WORDS = {"PASS", "PASSWORD", "PASSWD", "PWD", "SECRET", "TOKEN", "KEY", "APIKEY", "WEBHOOK",
                "CRED", "CREDS", "CREDENTIAL", "CREDENTIALS", "SALT", "DSN"}
SECRET_SUFFIXES = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "APIKEY")
URL_PASSWORD = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:([^@/\s]+)@")
ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
PLACEHOLDERS = {"changeme", "change-me", "password", "passw0rd", "secret", "example", "postgres", "admin",
                "localhost", "redis", "root", "true", "false", "none", "null"}


class GuardError(Exception):
    pass


def show(text):
    """Printable form of a path: control characters and undecodable bytes become \\x escapes."""
    return "".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in text)


def warn(message):
    print(f"secret-guard: warning: {message}", file=sys.stderr)


def git(*args):
    try:
        done = subprocess.run(["git", *args], capture_output=True)
    except OSError as exc:
        raise GuardError(f"cannot run git: {exc}")
    if done.returncode != 0:
        lines = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GuardError(f"git {args[0]} failed: {lines[0] if lines else 'exit %d' % done.returncode}")
    return done.stdout


def secret_name(name):
    words = name.upper().split("_")
    if any(w in SECRET_WORDS or w.endswith(SECRET_SUFFIXES) for w in words):
        return True
    if words[-1] == "AUTH":
        return True
    return any(a == "PATH" and b == "PREFIX" for a, b in zip(words, words[1:]))


def usable(value):
    return (len(value) >= MIN_LENGTH and value.lower() not in PLACEHOLDERS
            and not value.startswith(("$", "<")) and not re.fullmatch(r"[\d.:/ -]+", value))


def parse_env(text):
    """(name, value) pairs of a .env file: quotes, inline comments, `export`, quoted values over several lines."""
    lines = text.splitlines()
    pairs = []
    i = 0
    while i < len(lines):
        match = ENV_LINE.match(lines[i])
        i += 1
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if value[:1] in ("'", '"'):
            quote, rest, parts = value[0], value[1:], []
            while True:
                end = rest.find(quote)
                if end != -1:
                    parts.append(rest[:end])
                    break
                parts.append(rest)
                if i >= len(lines):
                    break
                rest, i = lines[i], i + 1
            value = "\n".join(parts)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        pairs.append((name, value))
    return pairs


def load_secrets():
    """{value: [(variable, env file), ...]} from every .env file that is not tracked: ignored or not,
    inside ignored directories too."""
    secrets = {}
    for raw in git("ls-files", "-z", "--others").split(b"\0"):
        path = raw.decode("utf-8", "surrogateescape")
        if not path or not env_file_name(path):
            continue
        try:
            with open(path, "rb") as handle:
                text = handle.read().decode("utf-8-sig", "replace")
        except OSError as exc:
            warn(f"cannot read {show(path)} ({exc.strerror}); its values are not checked")
            continue
        for name, value in parse_env(text):
            found = []
            url = URL_PASSWORD.match(value)
            if url:
                found.append(url.group(1))
            if secret_name(name):
                found.append(value)
            for secret in found:
                if usable(secret):
                    secrets.setdefault(secret, []).append((name, path))
    return secrets


def needles_for(secrets):
    out = []
    for value, owners in secrets.items():
        for encoded in {value.encode("utf-8", "replace"), value.encode("utf-16-le", "replace"),
                        value.encode("utf-16-be", "replace")}:
            out.append((encoded, owners))
    return out


def find_hits(data, needles):
    """[(line number, owners)] for each needle found on a line that carries no allow marker."""
    hits = []
    for needle, owners in needles:
        start = 0
        while True:
            at = data.find(needle, start)
            if at < 0:
                break
            begin = data.rfind(b"\n", 0, at) + 1
            end = data.find(b"\n", at)
            line = data[begin:end if end >= 0 else len(data)]
            if not any(marker in line for marker in ALLOW_MARKERS):
                hits.append((data.count(b"\n", 0, at) + 1, owners))
                break
            start = at + len(needle)
    return hits


class Blobs:
    """Reads blobs by id through one `git cat-file --batch` process."""

    SKIPPED = object()

    def __init__(self):
        try:
            proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise GuardError(f"cannot run git: {exc}")
        if proc.stdin is None or proc.stdout is None:
            raise GuardError("cannot talk to git cat-file")
        self.proc, self.stdin, self.stdout = proc, proc.stdin, proc.stdout

    def _discard(self, count):
        while count > 0:
            chunk = self.stdout.read(min(count, 1 << 20))
            if not chunk:
                raise GuardError("git cat-file ended early")
            count -= len(chunk)

    def get(self, sha):
        """The blob's bytes, SKIPPED when it is too large, None when the object is not a blob."""
        self.stdin.write(sha.encode() + b"\n")
        self.stdin.flush()
        header = self.stdout.readline().split()
        if len(header) == 2 and header[1] == b"missing":
            raise GuardError(f"object {sha[:10]} is missing")
        if len(header) != 3:
            raise GuardError("unexpected output from git cat-file")
        size = int(header[2])
        if header[1] != b"blob":
            self._discard(size + 1)      # the content must be consumed or the stream loses sync
            return None
        if size > MAX_BLOB_BYTES:
            self._discard(size + 1)
            return self.SKIPPED
        data = self.stdout.read(size)
        self.stdout.read(1)
        return data

    def close(self):
        self.stdin.close()
        self.proc.wait()


class Report:
    def __init__(self, needles):
        self.needles = needles
        self.carriers = []
        self.hits = {}
        self.skipped = []

    def carrier(self, where):
        self.carriers.append(where)

    def content(self, where, data):
        if data is Blobs.SKIPPED:
            self.skipped.append(where)
            return
        for line, owners in find_hits(data, self.needles):
            self.hits.setdefault((where, line), set()).update(f"{name} ({show(path)})" for name, path in owners)

    def failed(self):
        return bool(self.carriers or self.hits)

    def print(self):
        if self.carriers:
            print("secret-guard: these files hold secrets by their name and must not be committed:", file=sys.stderr)
            for where in sorted(set(self.carriers))[:MAX_REPORT]:
                print(f"  {show(where)}", file=sys.stderr)
            print("  Git-ignore them, or commit an *.example template instead.", file=sys.stderr)
        if self.hits:
            print("secret-guard: this content holds the value of a secret from a git-ignored .env file:",
                  file=sys.stderr)
            for (where, line), owners in sorted(self.hits.items())[:MAX_REPORT]:
                print(f"  {show(where)}:{line}  -> {', '.join(sorted(owners))}", file=sys.stderr)
            if len(self.hits) > MAX_REPORT:
                print(f"  ... and {len(self.hits) - MAX_REPORT} more", file=sys.stderr)
            print("  Remove the value, or write 'secret-guard:allow' on a line that must keep it.", file=sys.stderr)
        if self.skipped:
            warn(f"{len(self.skipped)} object(s) over {MAX_BLOB_BYTES // 1_000_000} MB were not read: "
                 + ", ".join(show(w) for w in self.skipped[:5]))


def staged_entries():
    """(new mode, new blob id, path) of every staged change except deletions."""
    raw = git("diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", "--no-ext-diff", "--diff-filter=d")
    parts = raw.split(b"\0")
    i = 0
    while i + 1 < len(parts):
        if not parts[i].startswith(b":"):
            i += 1
            continue
        fields = parts[i][1:].split()
        yield fields[1].decode(), fields[3].decode(), parts[i + 1].decode("utf-8", "surrogateescape")
        i += 2


def scan_staged(report, blobs):
    for mode, sha, path in staged_entries():
        if carrier_name(path):
            report.carrier(path)
        if mode != "160000":
            data = blobs.get(sha)
            if data is not None:
                report.content(path, data)


def scan_tree(report):
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for raw in listing.split(b"\0"):
        path = raw.decode("utf-8", "surrogateescape")
        if not path:
            continue
        if carrier_name(path):
            report.carrier(path)
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            if os.path.getsize(path) > MAX_BLOB_BYTES:
                report.skipped.append(path)
                continue
            with open(path, "rb") as handle:
                report.content(path, handle.read())
        except OSError as exc:
            warn(f"cannot read {show(path)} ({exc.strerror})")


def scan_history(report, blobs):
    paths = {}
    for line in git("rev-list", "--objects", "--all").decode("utf-8", "surrogateescape").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, set()).add(path)
    for sha, names in paths.items():
        for path in names:
            if carrier_name(path):
                report.carrier(f"{path} (in history)")
        data = blobs.get(sha)
        if data is not None:
            report.content(f"{sorted(names)[0]} [blob {sha[:10]}]", data)
    return len(paths)


def main(argv):
    mode = argv[0] if argv else "--staged"
    if mode not in ("--staged", "--message", "--tree", "--history") or (mode == "--message") != (len(argv) == 2):
        print(__doc__, file=sys.stderr)
        return 2
    message = os.path.abspath(argv[1]) if mode == "--message" else None
    os.chdir(git("rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip())

    secrets = load_secrets()
    if not secrets and mode in ("--tree", "--history"):
        print("secret-guard: no .env values found on this machine: only file names are checked.")
    report = Report(needles_for(secrets))
    blobs = None
    try:
        if mode == "--staged":
            blobs = Blobs()
            scan_staged(report, blobs)
        elif mode == "--message":
            assert message is not None
            with open(message, "rb") as handle:
                report.content("commit message", handle.read())
        elif mode == "--tree":
            scan_tree(report)
        else:
            blobs = Blobs()
            scan_history(report, blobs)
    finally:
        if blobs:
            blobs.close()
    report.print()
    if report.failed():
        if mode == "--history":
            print("  Find the commits with: git log --all --oneline --find-object=<blob id>", file=sys.stderr)
        return 1
    if mode in ("--tree", "--history"):
        print(f"secret-guard: {len(secrets)} secret value(s) checked ({mode[2:]}): no hit.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GuardError as error:
        print(f"secret-guard: {error}", file=sys.stderr)
        sys.exit(2)
    except OSError as error:
        print(f"secret-guard: {error.strerror or error}", file=sys.stderr)
        sys.exit(2)
