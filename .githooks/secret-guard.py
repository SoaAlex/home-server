#!/usr/bin/env python3
"""Refuse files and text headed for git that carry a secret known to this machine.

Two checks. Neither parses git's patch output, which hides added lines behind quoted names,
custom diff prefixes, external diff drivers and lines that look like headers.

  names   the path is a secret carrier whatever it holds: .env files and their backups
          (.env.local, .env-old, app.env.bak, .envrc; templates such as .env.example pass),
          Terraform state and variables, vault password files, key stores, SSH private keys
  values  the content holds the value of a secret from a .env file of this machine (UTF-8 and
          UTF-16, binary files and files of any size included). This catches secrets that have
          no recognizable shape, which gitleaks cannot.

    secret-guard.py [--staged]       files of the staged change (pre-commit hook)
    secret-guard.py --message FILE   a commit message, or any text file (commit-msg hook)
    secret-guard.py --range REV...   what a push would send: new blobs, their paths and the commit
                                     messages; REV... are `git rev-list` arguments (pre-push hook)
    secret-guard.py --tree           tracked and untracked-but-not-ignored files of the work tree
    secret-guard.py --history        every blob reachable from any ref, and every path in them

It prints paths, line numbers and variable names, never a value. Write `gitleaks:allow` (or
`secret-guard:allow`) on a line to accept a value that is meant to be there; files over 32 MB are
read in chunks and do not honor it. Run it with `python3 -I`.
Exit status: 0 clean, 1 something found, 2 the check itself failed (treat it as a failure).
SECRET_GUARD_ALLOW_UNREADABLE=1 turns an unreadable .env file from a failure into a warning.
"""
import os
import re
import subprocess
import sys

MIN_LENGTH = 8
MAX_REPORT = 40
ALLOW_MARKERS = (b"secret-guard:allow", b"gitleaks:allow")
# Files up to WHOLE_BYTES are read at once, larger ones in CHUNK_BYTES pieces (the tests shrink both).
WHOLE_BYTES = int(os.environ.get("SECRET_GUARD_WHOLE_BYTES") or 32 * 1024 * 1024)
CHUNK_BYTES = max(1, int(os.environ.get("SECRET_GUARD_CHUNK_BYTES") or 8 * 1024 * 1024))

# --- names ----------------------------------------------------------------------------------
TEMPLATE = re.compile(r".*\.(?:example|sample|template|dist|tpl)|.*\.(?:example|sample|template|dist|tpl)\.env", re.S)
ENV_FILE = re.compile(
    r"\.env(?:[._~-].*)?|\.envrc"
    r"|.*\.env(?:[._-](?:local|bak|backup|old|orig|save|swp|tmp|prod|production|dev|development"
    r"|staging|secret|secrets|private))?~?",
    re.S,
)
CARRIER = re.compile(
    r".*\.tfstate|.*\.tfstate\..*|.*\.tfvars|\.?vault[_-]pass.*"
    r"|.*\.(?:kdbx|p12|pfx|jks|keystore|key)|id_(?:rsa|dsa|ecdsa|ed25519)",
    re.S,
)


def basename(path):
    return path.rsplit("/", 1)[-1].lower()


def env_file_name(path):
    name = basename(path)
    return TEMPLATE.fullmatch(name) is None and ENV_FILE.fullmatch(name) is not None


def carrier_name(path):
    name = basename(path)
    return TEMPLATE.fullmatch(name) is None and (
        ENV_FILE.fullmatch(name) is not None or CARRIER.fullmatch(name) is not None)


# --- values ---------------------------------------------------------------------------------
# A variable holds a secret when one of its words says so (AUTHOR, KEYCLOAK_*, PRIVATE_REGISTRY do
# not), plus the path prefix of a tunnel. A URL that embeds credentials is also checked on its
# password, whatever the variable is called; the user part may be empty (redis://:pw@host).
SECRET_WORDS = {"PASS", "PASSWORD", "PASSWD", "PASSPHRASE", "PWD", "PSK", "SECRET", "TOKEN", "KEY", "APIKEY",
                "WEBHOOK", "CRED", "CREDS", "CREDENTIAL", "CREDENTIALS", "SALT", "DSN"}
SECRET_SUFFIXES = ("PASSWORD", "PASSWD", "PASSPHRASE", "SECRET", "TOKEN", "APIKEY")
URL_PASSWORD = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:([^@/\s]+)@")
NAME = r"[A-Za-z_][A-Za-z0-9_.-]*"
ASSIGN = re.compile(r"^\s*(?:export\s+)?(" + NAME + r")\s*=\s*(.*)$")
COMMENTED = re.compile(r"^\s*#+\s*(?:export\s+)?(" + NAME + r")\s*=\s*(.*)$")
LINE_BREAK = re.compile(r"\r\n|\n|\r")
ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "$": "$", "'": "'", "`": "`"}
MAX_SPAN = 500  # lines a quoted value may cover
OBJECT_ID = re.compile(rb"[0-9a-f]{40}|[0-9a-f]{64}")

PLACEHOLDERS = {"changeme", "change-me", "password", "passw0rd", "secret", "example", "postgres", "admin",
                "localhost", "redis", "root", "true", "false", "none", "null",
                "12345678", "123456789", "1234567890", "87654321", "qwertyui", "qwertyuiop", "abcdefgh", "password1"}
PLACEHOLDER_SHAPES = re.compile(
    r"(?:your|my)?[-_ ]?(?:password|passwd|secret|token|api[-_ ]?key|key|pass)(?:[-_ ]?here)?"
    r"|change[-_ ]?(?:me|it|this)|replace[-_ ]?(?:me|this)(?:[-_ ].*)?"
    r"|(?:your|my)[-_ ].*[-_ ](?:here|goes[-_ ]here)"
    r"|(?:example|sample|dummy|placeholder|todo|fixme)(?:[-_ ]?(?:token|key|secret|password|passwd|pass|value|string|text|here))?[-_ ]?\d*"
    r"|x{4,}|\*{3,}|\.{3,}|<[^>]*>|.*\$\{.*",
    re.I | re.S,
)
VARIABLE_REF = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*(?::?[-+?=][^}]*)?\}?")
NUMBER_SHAPES = re.compile(
    r"\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?(?::\d+)?|\d+(?:\.\d+)+|\d{1,2}:\d{2}(?::\d{2})?|\d+(?::\d+)+|[\d.:/ -]{0,7}")


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


def name_words(name):
    return [w for w in re.split(r"[_.\-]+", name.upper()) if w]


def secret_name(name):
    words = name_words(name)
    if not words or words[-1] == "FILE":  # FOO_PASSWORD_FILE holds a path, not the password
        return False
    if any(w in SECRET_WORDS or w.endswith(SECRET_SUFFIXES) or (w.endswith("KEY") and len(w) > 5) for w in words):
        return True
    if words[-1] == "AUTH":
        return True
    return any(a == "PATH" and b == "PREFIX" for a, b in zip(words, words[1:]))


def usable(value):
    if len(value) < MIN_LENGTH or len(set(value)) <= 2 or value.lower() in PLACEHOLDERS:
        return False
    return not (VARIABLE_REF.fullmatch(value) or NUMBER_SHAPES.fullmatch(value) or PLACEHOLDER_SHAPES.fullmatch(value))


def scan_piece(body, quote):
    """(raw, unescaped, closed) for body, the text after an opening quote up to the closing one."""
    if quote == "'":
        end = body.find("'")
        piece = body if end < 0 else body[:end]
        return piece, piece, end >= 0
    raw, out, k = [], [], 0
    while k < len(body):
        c = body[k]
        if c == "\\" and k + 1 < len(body):
            raw.append(body[k:k + 2])
            out.append(ESCAPES.get(body[k + 1], body[k:k + 2]))
            k += 2
        elif c == '"':
            return "".join(raw), "".join(out), True
        else:
            raw.append(c)
            out.append(c)
            k += 1
    return "".join(raw), "".join(out), False


def quoted_values(lines, i, text, multiline):
    """The values of a quoted assignment: `text` starts with the quote and sits on lines[i]. Both the
    text as written (backslashes kept) and the unescaped one are returned. A quote that never closes
    gives its first line only, so that it cannot swallow the variables below it."""
    quote = text[0]
    raws, outs, j, body = [], [], i, text[1:]
    while True:
        raw, out, closed = scan_piece(body, quote)
        raws.append(raw)
        outs.append(out)
        if closed:
            return list(dict.fromkeys(["\n".join(raws), "\n".join(outs)]))
        j += 1
        if not multiline or j >= len(lines) or j - i >= MAX_SPAN:
            return list(dict.fromkeys([raws[0], outs[0]]))
        body = lines[j]


def parse_env(text):
    """(name, value, commented out) triples of a .env file: quotes with escapes, inline comments,
    `export`, quoted values over several lines, assignments that were commented out. Every line is
    looked at on its own, so a bad quote never hides the lines below it."""
    lines = LINE_BREAK.split(text)
    triples = []
    for i, line in enumerate(lines):
        match, commented = ASSIGN.match(line), False
        if not match:
            match, commented = COMMENTED.match(line), True
            if not match:
                continue
        name, value = match.group(1), match.group(2).strip()
        if value[:1] in ("'", '"'):
            values = quoted_values(lines, i, value, multiline=not commented)
        else:
            values = [re.split(r"\s+#", value, maxsplit=1)[0].strip()]
        triples.extend((name, v, commented) for v in values)
    return triples


def decode_text(data):
    """UTF-8, or UTF-16 recognised by its byte order mark or by its NUL bytes."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    sample = data[:4096]
    if b"\x00" in sample:
        even, odd = sample[0::2].count(0), sample[1::2].count(0)
        if odd > even and odd > len(sample) // 8:
            return data.decode("utf-16-le", "replace")
        if even > odd and even > len(sample) // 8:
            return data.decode("utf-16-be", "replace")
    return data.decode("utf-8-sig", "replace")


def refuse_unreadable(problems):
    if not problems:
        return
    if os.environ.get("SECRET_GUARD_ALLOW_UNREADABLE") == "1":
        warn("cannot read " + ", ".join(problems) + "; skipped (SECRET_GUARD_ALLOW_UNREADABLE=1)")
        return
    raise GuardError("cannot read " + ", ".join(problems) + ": its values would go unchecked. "
                     "Fix the permissions, or set SECRET_GUARD_ALLOW_UNREADABLE=1 to skip it.")


def load_secrets():
    """{value: [(variable, env file), ...]} from every .env file of the work tree that is not a template:
    tracked or not, ignored or not, inside ignored directories too."""
    secrets, unreadable = {}, []
    for raw in git("ls-files", "-z", "--cached", "--others").split(b"\0"):
        path = raw.decode("utf-8", "surrogateescape")
        if not path or not env_file_name(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                text = decode_text(handle.read())
        except OSError as exc:
            unreadable.append(f"{show(path)} ({exc.strerror})")
            continue
        for name, value, commented in parse_env(text):
            found = []
            url = URL_PASSWORD.match(value)
            if url:
                found.append(url.group(1))
            if secret_name(name):
                found.append(value)
            for secret in found:
                if usable(secret):
                    secrets.setdefault(secret, []).append((name + (" (commented out)" if commented else ""), path))
    refuse_unreadable(unreadable)
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


class Stream:
    """Looks for the needles in a stream read in chunks. The last bytes of each chunk are kept, so that a
    value split between two chunks is found, and lines are counted for the report."""

    def __init__(self, needles):
        self.needles = needles
        self.keep = max((len(n) for n, _ in needles), default=1) - 1
        self.tail = b""
        self.lines = 0   # newlines before the start of `tail`
        self.found = {}  # needle index -> line of its first hit

    def feed(self, chunk):
        buf = self.tail + chunk
        for index, (needle, _) in enumerate(self.needles):
            if index not in self.found:
                at = buf.find(needle)
                if at >= 0:
                    self.found[index] = self.lines + buf.count(b"\n", 0, at) + 1
        cut = max(0, len(buf) - self.keep)
        self.lines += buf.count(b"\n", 0, cut)
        self.tail = buf[cut:]


class Big:
    """A blob too large to hold in memory, still attached to the `git cat-file --batch` stream:
    chunks() must be run to its end, or the stream loses sync."""

    def __init__(self, source, size):
        self.source, self.size, self.done = source, size, False

    def chunks(self):
        left = self.size
        while left > 0:
            chunk = self.source.read(min(left, CHUNK_BYTES))
            if not chunk:
                raise GuardError("git cat-file ended early")
            left -= len(chunk)
            yield chunk
        self.source.read(1)  # the newline git writes after every object
        self.done = True


class Blobs:
    """Reads blobs by id through one `git cat-file --batch` process."""

    def __init__(self):
        try:
            proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise GuardError(f"cannot run git: {exc}")
        if proc.stdin is None or proc.stdout is None:
            raise GuardError("cannot talk to git cat-file")
        self.proc, self.stdin, self.stdout = proc, proc.stdin, proc.stdout
        self.pending = None

    def _discard(self, count):
        while count > 0:
            chunk = self.stdout.read(min(count, 1 << 20))
            if not chunk:
                raise GuardError("git cat-file ended early")
            count -= len(chunk)

    def get(self, sha):
        """The blob's bytes, a Big when it is large, None when the object is not a blob."""
        if self.pending is not None and not self.pending.done:
            raise GuardError("internal error: a large blob was not read to its end")
        if not OBJECT_ID.fullmatch(sha.encode()):  # one request per line: anything else loses the stream
            raise GuardError("internal error: not an object id")
        self.stdin.write(sha.encode() + b"\n")
        self.stdin.flush()
        header = self.stdout.readline().split()
        if len(header) == 2 and header[1] == b"missing":
            raise GuardError(f"object {sha[:10]} is missing")
        if len(header) != 3 or not header[2].isdigit():
            raise GuardError("unexpected output from git cat-file")
        size = int(header[2])
        if header[1] != b"blob":
            self._discard(size + 1)  # the content must be consumed or the stream loses sync
            return None
        if size > WHOLE_BYTES:
            self.pending = Big(self.stdout, size)
            return self.pending
        data = self.stdout.read(size)
        if len(data) != size:
            raise GuardError("git cat-file ended early")
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
        self.scanned = 0  # contents looked at, so that a run that read nothing can be told from a clean one

    def carrier(self, where):
        self.carriers.append(where)

    def _add(self, where, line, owners):
        self.hits.setdefault((where, line), set()).update(f"{name} ({show(path)})" for name, path in owners)

    def stream(self, where, chunks):
        self.scanned += 1
        scan = Stream(self.needles)
        for chunk in chunks:  # always to the end: the source may be a shared stream
            scan.feed(chunk)
        for index, line in scan.found.items():
            self._add(where, line, self.needles[index][1])

    def content(self, where, data):
        if isinstance(data, Big):
            self.stream(where, data.chunks())
            return
        self.scanned += 1
        for line, owners in find_hits(data, self.needles):
            self._add(where, line, owners)

    def failed(self):
        return bool(self.carriers or self.hits)

    def print(self):
        if self.carriers:
            print("secret-guard: these files hold secrets by their name and must not be committed:", file=sys.stderr)
            for where in sorted(set(self.carriers))[:MAX_REPORT]:
                print(f"  {show(where)}", file=sys.stderr)
            print("  Git-ignore them, or commit an *.example template instead.", file=sys.stderr)
        if self.hits:
            print("secret-guard: this content holds the value of a secret from a .env file:", file=sys.stderr)
            for (where, line), owners in sorted(self.hits.items())[:MAX_REPORT]:
                print(f"  {show(where)}:{line}  -> {', '.join(sorted(owners))}", file=sys.stderr)
            if len(self.hits) > MAX_REPORT:
                print(f"  ... and {len(self.hits) - MAX_REPORT} more", file=sys.stderr)
            print("  Remove the value, or write 'gitleaks:allow' on a line that must keep it.", file=sys.stderr)


def staged_entries():
    """(new mode, new blob id, path) of every staged change except deletions. The output is pairs of
    `:<old mode> <new mode> <old id> <new id> <status>` and a path; anything else is an error."""
    raw = git("diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames", "--no-ext-diff", "--diff-filter=d")
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 2:
        raise GuardError("unexpected output from git diff")
    for meta, name in zip(parts[0::2], parts[1::2]):
        fields = meta[1:].split()
        if not meta.startswith(b":") or len(fields) != 5 or not OBJECT_ID.fullmatch(fields[3]):
            raise GuardError("unexpected output from git diff")
        yield fields[1].decode(), fields[3].decode(), name.decode("utf-8", "surrogateescape")


def scan_staged(report, blobs):
    for mode, sha, path in staged_entries():
        if carrier_name(path):
            report.carrier(path)
        if mode != "160000":
            data = blobs.get(sha)
            if data is not None:
                report.content(path, data)


def scan_tree(report):
    unreadable = []
    for raw in git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0"):
        path = raw.decode("utf-8", "surrogateescape")
        if not path:
            continue
        if carrier_name(path):
            report.carrier(path)
        try:
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                if os.path.getsize(path) <= WHOLE_BYTES:
                    report.content(path, handle.read())
                else:
                    report.stream(path, iter(lambda: handle.read(CHUNK_BYTES), b""))
        except OSError as exc:
            unreadable.append(f"{show(path)} ({exc.strerror})")
    refuse_unreadable(unreadable)


def list_objects(rev_args):
    """{object id: set of paths} of `git rev-list --objects REV_ARGS`. The output is one `<id> <path>` line
    per object (a commit has no path) in every git version. -z is not used: git 2.49 accepts it and ignores
    it, git 2.56 changes the format, and a parser that expects one reads the other as `nothing found`. A path
    that holds a newline is cut there by git, which only affects the name that is reported. Any line that is
    not an object id is an error, so a format this code does not know never reads as a clean result."""
    paths = {}
    for line in git("rev-list", "--objects", *rev_args).split(b"\n"):
        if not line:
            continue
        sha, _, path = line.partition(b" ")
        if not OBJECT_ID.fullmatch(sha):
            raise GuardError("unexpected output from git rev-list")
        names = paths.setdefault(sha.decode(), set())
        if path:
            names.add(path.decode("utf-8", "surrogateescape"))
    return paths


def scan_objects(report, blobs, rev_args, where):
    for sha, names in list_objects(rev_args).items():
        for path in names:
            if carrier_name(path):
                report.carrier(f"{path} ({where})")
        data = blobs.get(sha)
        if data is not None:
            report.content(f"{sorted(names)[0] if names else '?'} [blob {sha[:10]}]", data)


def scan_range(report, blobs, rev_args):
    for arg in rev_args:
        if not re.fullmatch(r"\^?[0-9a-fA-F]{40}|\^?[0-9a-fA-F]{64}|--not|--remotes=\S+", arg):
            raise GuardError(f"unexpected revision argument: {show(arg)}")
    scan_objects(report, blobs, rev_args, "in the commits to push")
    for entry in git("log", "-z", "--format=%h%n%B", *rev_args).split(b"\0"):
        head, _, body = entry.partition(b"\n")
        if head.strip():
            report.content(f"message of commit {head.decode('ascii', 'replace').strip()}", body)


def main(argv):
    mode, args = (argv[0] if argv else "--staged"), argv[1:]
    valid = {"--staged": not args, "--tree": not args, "--history": not args,
             "--message": len(args) == 1, "--range": bool(args)}
    if not valid.get(mode):
        print(__doc__, file=sys.stderr)
        return 2
    message = os.path.abspath(args[0]) if mode == "--message" else None
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
        elif mode == "--range":
            blobs = Blobs()
            scan_range(report, blobs, args)
        elif mode == "--tree":
            scan_tree(report)
        else:
            blobs = Blobs()
            scan_objects(report, blobs, ["--all"], "in history")
    finally:
        if blobs:
            blobs.close()
    report.print()
    if report.failed():
        if mode == "--history":
            print("  Find the commits with: git log --all --oneline --find-object=<blob id>", file=sys.stderr)
        return 1
    if mode in ("--tree", "--history"):
        print(f"secret-guard: {len(secrets)} secret value(s) checked in {report.scanned} file(s) ({mode[2:]}): no hit.")
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
