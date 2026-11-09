#!/usr/bin/env python3
"""Refuse files and text headed for git that carry a secret known to this machine.

Two checks. Neither parses git's patch output, which hides added lines behind quoted names,
custom diff prefixes, external diff drivers and lines that look like headers.

  names   the path is a secret carrier whatever it holds: .env files and their backups
          (.env.local, .env-old, app.env.bak, .envrc; templates such as .env.example pass),
          Terraform state and variables, vault password files, key stores, SSH private keys
  values  the content holds the value of a secret from a .env file (UTF-8, UTF-16 and UTF-32,
          binary files and files of any size included; a value written with spaces is also
          looked for without them). This catches secrets that have no recognizable shape, which
          gitleaks cannot. The .env files read are the ones of the work tree (tracked or not,
          ignored or not), ~/.hermes/.env, and the files of SECRET_GUARD_ENV_FILES (':' separated).

    secret-guard.py [--staged]       files of the staged change (pre-commit hook)
    secret-guard.py --message FILE   a commit message, or any text file (commit-msg hook)
    secret-guard.py --range REV...   what a push would send: new blobs, their paths and the commit
                                     messages; REV... are `git rev-list` arguments (pre-push hook)
    secret-guard.py --tree           tracked and untracked-but-not-ignored files of the work tree
    secret-guard.py --history        every blob reachable from any ref, and every path in them

--staged and --range can also copy the files they read into a directory for gitleaks to scan, which
reads files, not patches (git diff and git log -p change with colors, attributes, diff drivers and the
binary/text guess, and then hand gitleaks nothing). Give the options before the mode:

    secret-guard.py --export DIR --gitleaks BIN [--gitleaks-config FILE] --staged
    secret-guard.py --export DIR --gitleaks BIN [--gitleaks-config FILE] --range REV...

DIR/files holds the copies for gitleaks to scan. gitleaks skips some files by name (lock files,
images, vendored directories, anything called gitleaks.toml...) and cannot read UTF-16: a text file of
that kind is copied a second time, as DIR/files/_scan/<n>.txt (in UTF-8), and DIR/MAP says which file
each copy is. The names it skips are found by asking it (see Export).

It prints paths, line numbers and variable names, never a value. Write `gitleaks:allow` (or
`secret-guard:allow`) on a line to accept a value that is meant to be there; files over 32 MB are
read in chunks and do not honor it. Run it with `python3 -I`.
Exit status: 0 clean, 1 something found, 2 the check itself failed (treat it as a failure).
SECRET_GUARD_ALLOW_UNREADABLE=1 turns an unreadable .env file from a failure into a warning.
"""
import codecs
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

MIN_LENGTH = 8
MAX_REPORT = 40
# Files up to WHOLE_BYTES are read at once, larger ones in CHUNK_BYTES pieces (the tests shrink both).
WHOLE_BYTES = int(os.environ.get("SECRET_GUARD_WHOLE_BYTES") or 32 * 1024 * 1024)
CHUNK_BYTES = max(1, int(os.environ.get("SECRET_GUARD_CHUNK_BYTES") or 8 * 1024 * 1024))
# Files copied for gitleaks (--export) up to this size; the .env values are checked at any size.
EXPORT_MAX_BYTES = int(os.environ.get("SECRET_GUARD_EXPORT_MAX_BYTES") or 64 * 1024 * 1024)
# What the probe puts in a file to see whether gitleaks reads a file of that name: a line shaped like a GitHub
# token, written in pieces so that no scan of this very file flags it.
PROBE_LINE = ("token = ghp_" + "Q7xK2mV9bR4t" + "Y8nL3cW6dF1hJ5sZ0pAe2uGv" + "\n").encode()
TWIN_DIR = "_scan"

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
SECRET_WORDS = {"PASS", "PASSWORD", "PASSWD", "PASSPHRASE", "PASSCODE", "PWD", "PSK", "SECRET", "TOKEN", "KEY",
                "APIKEY", "WEBHOOK", "CRED", "CREDS", "CREDENTIAL", "CREDENTIALS", "SALT", "DSN", "BEARER", "COOKIE",
                "SEED", "PAT", "JWT", "MNEMONIC", "TOPIC"}
SECRET_SUFFIXES = ("PASSWORD", "PASSWD", "PASSPHRASE", "SECRET", "TOKEN", "APIKEY")
URL_PASSWORD = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*:([^@/\s]+)@")
URL_TAIL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/?#\s]*([/?#].*)", re.S)
URL_PART_SPLIT = re.compile(r"[/?&=#;,]+")
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
    r"|x{4,}|\*{3,}|\.{3,}|<[^>]*>|.*\$\{.*|.*(?:\.{3}|\*{3}|\u2026)",  # sk-... and xoxb-*** are templates
    re.I | re.S,
)
# A value that points to another variable instead of being a secret: ${VAR}, ${VAR:-default}, $UPPER_SNAKE, and
# $name when `name` is defined in the same file (see is_reference).
BRACED_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::?[-+?=][^}]*)?\}")
BARE_REF = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
SNAKE_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
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


def entropy(text):
    """Shannon entropy of the characters of TEXT, in bits per character."""
    counts = {}
    for c in text:
        counts[c] = counts.get(c, 0) + 1
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def random_looking(part):
    """True for a piece of a URL that is a token and not a word: 16 or more of A-Z a-z 0-9 _ -, with letters and
    digits mixed (a hash, a UUID, a webhook token), or 20 or more with both cases and no digit."""
    if len(part) < 16 or not re.fullmatch(r"[A-Za-z0-9_-]+", part):
        return False
    letters, digits = re.search(r"[A-Za-z]", part), re.search(r"\d", part)
    if letters and digits:
        return entropy(part) >= 3.3
    return len(part) >= 20 and part.lower() != part and part.upper() != part and entropy(part) >= 3.9


def url_secrets(value):
    """The pieces of a URL that work as a password (a Slack or Discord webhook, a private calendar feed, a
    health-check ping URL): its path segments and query values that look random. The variable can have any
    name."""
    match = URL_TAIL.match(value)
    return [part for part in URL_PART_SPLIT.split(match.group(1)) if random_looking(part)] if match else []


def is_reference(value, defined, literal):
    """True for a value that points to another variable instead of being a secret of its own. In single
    quotes (literal) a `$` is a plain character, so nothing is a reference there. Outside them: ${VAR} and
    ${VAR:-default}; $UPPER_SNAKE (the usual shape of an environment variable); and $name when `name` is
    defined in the same file. Any other `$word` (a mixed-case run of letters and digits) can be a real
    password, so it is kept."""
    if literal:
        return False
    if BRACED_REF.fullmatch(value):
        return True
    bare = BARE_REF.fullmatch(value)
    return bool(bare) and (bare.group(1) in defined or SNAKE_NAME.fullmatch(bare.group(1)) is not None)


def usable(value, defined=frozenset(), literal=False):
    if len(value) < MIN_LENGTH or len(set(value)) <= 2 or value.lower() in PLACEHOLDERS:
        return False
    return not (is_reference(value, defined, literal) or NUMBER_SHAPES.fullmatch(value)
                or PLACEHOLDER_SHAPES.fullmatch(value))


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
    """(name, value, commented out, literal) tuples of a .env file: quotes with escapes, inline comments,
    `export`, quoted values over several lines, assignments that were commented out. `literal` is true for
    a value in single quotes. Every line is looked at on its own, so a bad quote never hides the lines
    below it."""
    lines = LINE_BREAK.split(text)
    found = []
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
        found.extend((name, v, commented, value[:1] == "'") for v in values)
    return found


def plausible(sample, codec):
    """True when SAMPLE decodes as CODEC without an error into text that is mostly printable."""
    try:
        text = sample.decode(codec)
    except UnicodeDecodeError as exc:
        if exc.start < len(sample) - 4:  # an error in the middle is not this encoding; one at the end is a cut
            return False
        text = sample[:exc.start].decode(codec)
    return bool(text) and sum(c.isprintable() or c in "\r\n\t" for c in text) >= 0.9 * len(text)


def text_codec(data):
    """The codec of text that is UTF-16 or UTF-32, found by its byte order mark or, without one, by the NUL
    bytes of ASCII text (a NUL in every other byte for UTF-16, in three of four for UTF-32) and a decoding
    that gives readable text; None for anything else (a binary file has many NULs and does not decode so)."""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    sample = data[:4096]
    if len(sample) < 8 or b"\x00" not in sample:
        return None
    zeros = [sample[k::4].count(0) for k in range(4)]
    quarter = len(sample) // 4
    guesses = []
    if min(zeros[1:]) > quarter // 2 and zeros[0] < quarter // 2:
        guesses.append("utf-32-le")
    if min(zeros[:3]) > quarter // 2 and zeros[3] < quarter // 2:
        guesses.append("utf-32-be")
    even, odd = sample[0::2].count(0), sample[1::2].count(0)
    if odd > even and odd > len(sample) // 8:
        guesses.append("utf-16-le")
    if even > odd and even > len(sample) // 8:
        guesses.append("utf-16-be")
    for codec in guesses:
        width = 4 if codec.startswith("utf-32") else 2
        if plausible(sample[:len(sample) // width * width], codec):
            return codec
    return None


def decode_text(data):
    """UTF-8, UTF-16 or UTF-32 text as str."""
    return data.decode(text_codec(data) or "utf-8-sig", "replace")


def utf8_view(data):
    """What a text scanner has to read: UTF-16 and UTF-32 text as UTF-8, anything else as it is."""
    codec = text_codec(data)
    return data if codec is None else data.decode(codec, "replace").encode("utf-8")


def refuse_unreadable(problems):
    if not problems:
        return
    if os.environ.get("SECRET_GUARD_ALLOW_UNREADABLE") == "1":
        warn("cannot read " + ", ".join(problems) + "; skipped (SECRET_GUARD_ALLOW_UNREADABLE=1)")
        return
    raise GuardError("cannot read " + ", ".join(problems) + ": its values would go unchecked. "
                     "Fix the permissions, or set SECRET_GUARD_ALLOW_UNREADABLE=1 to skip it.")


def extra_env_files():
    """.env files outside the work tree whose values must never be committed: the Hermes one (GitHub,
    Discord and dashboard credentials) and the ones listed in SECRET_GUARD_ENV_FILES, separated by ':'."""
    paths = [os.path.expanduser("~/.hermes/.env")]
    paths += [os.path.expanduser(p) for p in os.environ.get("SECRET_GUARD_ENV_FILES", "").split(os.pathsep) if p]
    return paths


def collect_secrets(secrets, text, label):
    """Add the secret values of one .env file (its text) to {value: [(variable, label), ...]}."""
    parsed = parse_env(text)
    defined = frozenset(name for name, _, commented, _ in parsed if not commented)
    for name, value, commented, literal in parsed:
        found = []
        url = URL_PASSWORD.match(value)
        if url:
            found.append(url.group(1))
        found.extend(url_secrets(value))
        if secret_name(name):
            found.append(value)
        for secret in found:
            if usable(secret, defined, literal):
                secrets.setdefault(secret, []).append((name + (" (commented out)" if commented else ""), label))


def load_secrets():
    """{value: [(variable, env file), ...]} from every .env file of the work tree that is not a template:
    tracked or not, ignored or not, inside ignored directories too; then the files of extra_env_files()
    that exist."""
    secrets, unreadable = {}, []
    sources = []
    for raw in git("ls-files", "-z", "--cached", "--others").split(b"\0"):
        path = raw.decode("utf-8", "surrogateescape")
        if path and env_file_name(path) and os.path.isfile(path):
            sources.append((path, path))
    sources += [(p, p.replace(os.path.expanduser("~"), "~", 1)) for p in extra_env_files() if os.path.isfile(p)]
    for path, label in sources:
        try:
            with open(path, "rb") as handle:
                text = decode_text(handle.read())
        except OSError as exc:
            unreadable.append(f"{show(label)} ({exc.strerror})")
            continue
        collect_secrets(secrets, text, label)
    refuse_unreadable(unreadable)
    return secrets


def variants(value):
    """The spellings of a secret that must be found: as written; without whitespace (an app password
    written 'abcd efgh ijkl mnop' is pasted as 'abcdefghijklmnop'); and, for hexadecimal values, in lower
    and upper case."""
    out = {value}
    squeezed = "".join(value.split())
    if squeezed != value and len(squeezed) >= MIN_LENGTH:
        out.add(squeezed)
    if re.fullmatch(r"[0-9A-Fa-f]{16,}", value):
        out.update((value.lower(), value.upper()))
    return out


def needles_for(secrets):
    """[(bytes to look for, owners)]: every spelling of every secret in UTF-8, UTF-16 and UTF-32."""
    merged = {}
    for value, owners in secrets.items():
        for spelling in variants(value):
            for codec in CODECS:
                merged.setdefault(spelling.encode(codec, "replace"), []).extend(owners)
    return list(merged.items())


BREAKS = re.compile(rb"\r\n|\n|\r")
CODECS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
# The markers in every encoding, because the file is searched as stored: a UTF-16 file is searched with
# UTF-16 needles, so its marker has to be UTF-16 as well. Bytes that merely look like a UTF-16 file are
# then still searched as they are; nothing here depends on a guess about the encoding.
ALLOW_MARKERS = tuple(m.encode(codec) for m in ("secret-guard:allow", "gitleaks:allow") for codec in CODECS)


def find_hits(data, needles):
    """[(line number, owners)] for each needle found on a line that carries no allow marker. A line ends at
    LF, CR or CRLF, so that in a file with old Mac line ends one marker covers its own line only."""
    hits = []
    for needle, owners in needles:
        start = 0
        while True:
            at = data.find(needle, start)
            if at < 0:
                break
            begin = max(data.rfind(b"\n", 0, at), data.rfind(b"\r", 0, at)) + 1
            ends = [e for e in (data.find(b"\n", at), data.find(b"\r", at)) if e >= 0]
            line = data[begin:min(ends) if ends else len(data)]
            if not any(marker in line for marker in ALLOW_MARKERS):
                hits.append((len(BREAKS.findall(data, 0, at)) + 1, owners))
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
    chunks() must be run to its end, or the stream loses sync. Every chunk is also written to each of
    `sinks` (binary file objects, closed at the end)."""

    def __init__(self, source, size):
        self.source, self.size, self.done, self.sinks = source, size, False, []

    def chunks(self):
        left = self.size
        while left > 0:
            chunk = self.source.read(min(left, CHUNK_BYTES))
            if not chunk:
                raise GuardError("git cat-file ended early")
            left -= len(chunk)
            for sink in self.sinks:
                sink.write(chunk)
            yield chunk
        self.source.read(1)  # the newline git writes after every object
        for sink in self.sinks:
            sink.close()
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


class Export:
    """Copies of the scanned files, in a directory the hook made, for gitleaks to read.

    gitleaks decides by the NAME of a file whether to look inside it: lock files, images, fonts, vendored
    directories, everything called gitleaks.toml and more are skipped without a word, and UTF-16 text is bytes
    that match nothing. A scan of the files themselves would let a secret through in those. So a file
    that gitleaks would not read also gets a copy under a name it does read, <twin dir>/<n>.txt (a name made
    of the file's own, even with its dots changed, can still match what gitleaks skips: its patterns use `.`
    as a wildcard), and UTF-16 or UTF-32 text a copy in UTF-8.

    The names gitleaks skips are not listed here, because its list changes with its version: probe() asks it,
    with a file of each name that holds a token, and checks that the names of the copies are read. When the
    answer cannot be read, every file gets a copy. A file under a name gitleaks reads is copied once, as it is
    (gitleaks opens archives by their extension, and reads binary files too).

    The directory holds `files/`, which gitleaks scans, and `MAP`, which says what each copy is."""

    def __init__(self, root, gitleaks, config):
        if not os.path.isdir(root):
            raise GuardError(f"export directory not found: {show(root)}")
        self.base, self.gitleaks, self.config = root, gitleaks, config
        self.tree, self.listing = os.path.join(root, "files"), os.path.join(root, "MAP")
        try:
            os.mkdir(self.tree)
        except OSError as exc:
            raise GuardError(f"cannot prepare {show(self.tree)} for gitleaks: {exc.strerror}")
        self.blind = set()       # names that gitleaks does not read
        self.everything = False  # the probe failed: treat every name as one that gitleaks does not read
        self.twin_dir, self.counter = TWIN_DIR, 0
        self.twins, self.skipped = [], []

    def probe(self, names):
        """Find out which of NAMES (the relative paths of the files about to be copied) gitleaks skips."""
        names = sorted(set(names))
        if not names:
            return
        tops = {n.split("/")[0] for n in names}
        while self.twin_dir in tops:  # a file of the change is called _scan: use another directory
            self.twin_dir += "_"
        try:
            asked = self._ask(names)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            asked = None
        if asked is None:
            self.everything = True
            warn("could not ask gitleaks which files it skips: every text file is also scanned as a copy.")
        else:
            self.blind = asked

    def _ask(self, names):
        """The names that gitleaks reported nothing for, or None when it could not be asked. A file of each
        name holds PROBE_LINE; a file that is read is reported, one that is skipped is not. A control file
        under a name that is always read proves that the report means something, and a file under the name
        of the first copy proves that the copies are read."""
        control = "0-control.txt"
        while control in names:
            control = "_" + control
        copy = self._copy_name(0)
        work = tempfile.mkdtemp(prefix=".probe.", dir=self.base)
        try:
            tree, report = os.path.join(work, "tree"), os.path.join(work, "report.json")
            for name in [control, copy, *names]:
                full = os.path.join(tree, *name.split("/"))
                try:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as handle:
                        handle.write(PROBE_LINE)
                except OSError:
                    if name in (control, copy):
                        return None  # a name that cannot be created is simply not reported, so it gets a copy
            cmd = [self.gitleaks, "dir", "--no-banner", "--no-color", "--log-level", "error", "--exit-code", "2",
                   "--report-format", "json", "--report-path", report]
            if self.config:
                cmd += ["--config", self.config]
            done = subprocess.run(cmd + ["."], cwd=tree, stdin=subprocess.DEVNULL, capture_output=True)
            if done.returncode != 2 or not os.path.isfile(report):
                return None
            with open(report, "rb") as handle:
                seen = {str(finding["File"]).replace(os.sep, "/") for finding in json.load(handle)}
            if control not in seen:
                return None
            if copy not in seen:
                raise GuardError(f"gitleaks does not read a file called {copy}: its copies would not be scanned")
            return {name for name in names if name not in seen}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _copy_name(self, number):
        return f"{self.twin_dir}/{number}.txt"

    def _target(self, name):
        parts = name.split("/")
        if name.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise GuardError(f"cannot copy a file called {show(name)} for gitleaks")
        full = os.path.join(self.tree, *parts)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
        except OSError as exc:
            raise GuardError(f"cannot copy {show(name)} for gitleaks: {exc.strerror}")
        taken, number = full, 1
        while os.path.lexists(taken):  # two names that differ only by case, on a file system that folds case
            taken, number = f"{full}.{number}", number + 1
        return taken

    def _twin(self, names, content, why):
        copy = self._copy_name(self.counter)
        self.counter += 1
        with open(self._target(copy), "wb") as handle:
            handle.write(content)
        self.twins.append((copy, names, why))

    def take(self, names, data):
        """Copy a file under each of NAMES (and, when it needs one, as a twin). The data comes back for the
        other checks; a Big is copied while it is read."""
        big = isinstance(data, Big)
        size = data.size if big else len(data)
        if size > EXPORT_MAX_BYTES:
            self.skipped.append(names[0])
            return data
        unread = self.everything or any(n in self.blind for n in names)
        try:
            targets = [self._target(n) for n in names]
            if big:
                data.sinks = [open(t, "wb") for t in targets]
                if unread:  # too big to hold, so no second copy: it is announced instead
                    self.skipped.append(names[0])
                return data
            for target in targets:
                with open(target, "wb") as handle:
                    handle.write(data)
            view = utf8_view(data)
            if view is not data:
                self._twin(names, view, "UTF-16 or UTF-32 text, as UTF-8")
            elif unread:
                self._twin(names, data, "gitleaks skips the name")
        except OSError as exc:
            raise GuardError(f"cannot copy {show(names[0])} for gitleaks: {exc.strerror}")
        return data

    def note(self):
        """Warn about what was not copied, and write MAP: the hook prints it when gitleaks finds something,
        because a finding in a copy is named _scan/<n>.txt."""
        if self.skipped:
            warn(f"{len(self.skipped)} file(s) were not given to gitleaks, being too large to copy (over "
                 f"{EXPORT_MAX_BYTES >> 20} MB, or over {WHOLE_BYTES >> 20} MB under a name it skips); the .env values "
                 "were still checked: " + ", ".join(show(p) for p in self.skipped[:5]))
        if not self.twins:
            return
        lines = ["secret-guard: gitleaks does not read some files as they are, so it scanned copies. A finding in a",
                 "file called _scan/<n>.txt is in:"]
        for copy, names, why in self.twins[:200]:
            more = f" (and {len(names) - 1} other name(s))" if len(names) > 1 else ""
            lines.append(f"  {copy} = {show(names[0])}{more}: {why}")
        if len(self.twins) > 200:
            lines.append(f"  ... and {len(self.twins) - 200} more")
        try:
            with open(self.listing, "w") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            warn(f"cannot write the list of copies: {exc.strerror}")


def scan_staged(report, blobs, export=None):
    entries = list(staged_entries())
    if export is not None:
        export.probe([path for mode, _, path in entries if mode not in ("160000", "120000")])
    for mode, sha, path in entries:
        if carrier_name(path):
            report.carrier(path)
        if mode == "160000":  # a submodule: no content here
            continue
        data = blobs.get(sha)
        if data is None:
            continue
        if export is not None and mode != "120000":  # a symlink holds its target, not a file
            data = export.take([path], data)
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


def changed_files(rev_args):
    """(path, blob id, mode) of every file that a commit of REV_ARGS adds or changes, merges included (-m), read
    from `git log --raw -z`. `rev-list --objects` names a blob only once, by the first path it met, so a second
    file with the same content under another name (a copy called terraform.tfstate) would escape the name
    check, and gitleaks would not see the blob under the name it skips."""
    raw = git("log", "--raw", "-z", "--no-abbrev", "--no-renames", "--root", "-m", "--format=", *rev_args)
    tokens = [t for t in raw.split(b"\0") if t]
    if len(tokens) % 2:
        raise GuardError("unexpected output from git log")
    for meta, name in zip(tokens[0::2], tokens[1::2]):
        fields = meta[1:].split()
        if not meta.startswith(b":") or len(fields) != 5 or not OBJECT_ID.fullmatch(fields[3]):
            raise GuardError("unexpected output from git log")
        if not fields[4].startswith(b"D"):  # a deletion removes a secret carrier, it does not publish one
            yield name.decode("utf-8", "surrogateescape"), fields[3].decode(), fields[1].decode()


def object_kinds(ids):
    """{object id: type} for the object ids, read with `git cat-file --batch-check`; anything else is an error."""
    ids = sorted(ids)
    try:
        done = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                              input="".join(f"{i}\n" for i in ids).encode(), capture_output=True)
    except OSError as exc:
        raise GuardError(f"cannot run git: {exc}")
    kinds = {}
    for line in done.stdout.split(b"\n"):
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not OBJECT_ID.fullmatch(parts[0]):
            raise GuardError("unexpected output from git cat-file")
        kinds[parts[0].decode()] = parts[1].decode()
    if done.returncode != 0 or set(kinds) != set(ids):
        raise GuardError("unexpected output from git cat-file")
    return kinds


def export_name(blob, path):
    """Where a blob of the pushed commits goes in the export: under its own id, so that two versions of a
    file do not meet and the report names the blob."""
    return f"{blob[:12]}/{path}"


def scan_objects(report, blobs, rev_args, where, export=None):
    entries = {}  # blob id -> every path it has in these commits
    for sha, names in list_objects(rev_args).items():
        entries[sha] = set(names)
    for path, sha, mode in changed_files(rev_args):
        if mode != "160000":  # a submodule is a commit of another repository
            entries.setdefault(sha, set()).add(path)
    for names in entries.values():
        for path in names:
            if carrier_name(path):
                report.carrier(f"{path} ({where})")
    if export is not None:
        kinds = object_kinds(entries)
        export.probe([export_name(sha, path) for sha, names in entries.items() if kinds[sha] == "blob"
                      for path in (names or {"blob"})])
    for sha, names in entries.items():
        data = blobs.get(sha)
        if data is None:
            continue
        if export is not None:
            data = export.take([export_name(sha, path) for path in sorted(names or {"blob"})], data)
        report.content(f"{sorted(names)[0] if names else '?'} [blob {sha[:10]}]", data)


def scan_range(report, blobs, rev_args, export=None):
    for arg in rev_args:
        if not re.fullmatch(r"\^?[0-9a-fA-F]{40}|\^?[0-9a-fA-F]{64}|--not|--remotes=\S+", arg):
            raise GuardError(f"unexpected revision argument: {show(arg)}")
    scan_objects(report, blobs, rev_args, "in the commits to push", export)
    for entry in git("log", "-z", "--format=%h%n%B", *rev_args).split(b"\0"):
        head, _, body = entry.partition(b"\n")
        if head.strip():
            report.content(f"message of commit {head.decode('ascii', 'replace').strip()}", body)


EXPORT_OPTIONS = ("--export", "--gitleaks", "--gitleaks-config")


def main(argv):
    argv, options = list(argv), {}
    while len(argv) >= 2 and argv[0] in EXPORT_OPTIONS:
        options[argv[0]] = argv[1]
        argv = argv[2:]
    mode, args = (argv[0] if argv else "--staged"), argv[1:]
    valid = {"--staged": not args, "--tree": not args, "--history": not args,
             "--message": len(args) == 1, "--range": bool(args)}
    export_ok = not options or (mode in ("--staged", "--range") and "--export" in options and "--gitleaks" in options)
    if not valid.get(mode) or not export_ok:
        print(__doc__, file=sys.stderr)
        return 2
    message = os.path.abspath(args[0]) if mode == "--message" else None
    export_dir = os.path.abspath(options["--export"]) if "--export" in options else None
    os.chdir(git("rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip())

    secrets = load_secrets()
    if not secrets and mode in ("--tree", "--history"):
        print("secret-guard: no .env values found on this machine: only file names are checked.")
    report = Report(needles_for(secrets))
    export = None
    if export_dir:
        export = Export(export_dir, os.path.abspath(options["--gitleaks"]), options.get("--gitleaks-config"))
    blobs = None
    try:
        if mode == "--staged":
            blobs = Blobs()
            scan_staged(report, blobs, export)
        elif mode == "--message":
            assert message is not None
            with open(message, "rb") as handle:
                report.content("commit message", handle.read())
        elif mode == "--range":
            blobs = Blobs()
            scan_range(report, blobs, args, export)
        elif mode == "--tree":
            scan_tree(report)
        else:
            blobs = Blobs()
            scan_objects(report, blobs, ["--all"], "in history")
    finally:
        if blobs:
            blobs.close()
        if export is not None:
            export.note()
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
