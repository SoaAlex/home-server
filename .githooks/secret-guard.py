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

    secret-guard.py [--staged]       files of the staged change and their names (pre-commit hook)
    secret-guard.py --message FILE [LABEL]
                                     a commit message, or any text file; LABEL names it in the report
                                     (commit-msg hook)
    secret-guard.py --range REV...   what a push would send: new blobs, their paths, the commit
                                     messages and the names of the authors and committers; REV... are
                                     `git rev-list` arguments (pre-push hook)
    secret-guard.py --tree           tracked and untracked-but-not-ignored files of the work tree
    secret-guard.py --history        every blob reachable from any ref, and every path in them

--staged and --range can also copy the files they read into a directory for gitleaks to scan, which
reads files, not patches (git diff and git log -p change with colors, attributes, diff drivers and the
binary/text guess, and then hand gitleaks nothing). Give the options before the mode:

    secret-guard.py --export DIR --gitleaks BIN [--gitleaks-config FILE] --staged
    secret-guard.py --export DIR --gitleaks BIN [--gitleaks-config FILE] --range REV...

DIR/files holds the copies for gitleaks to scan. gitleaks skips some files without a word: by name (lock
files, images, vendored directories, anything called gitleaks.toml...), by what they begin with or hold at
a fixed place (SQLite databases, executables, archives, PDFs, Office documents, a text file that starts with
"MZ" or has "ustar" at byte 257...), when the first line is over 100 KB, and it cannot read UTF-16. Such a file is
copied a second time, with 40 000 spaces and a newline in front, as DIR/files/_scan/<blob id>/<name> (the name
kept when gitleaks reads it, for the rules that depend on it; one copy for each different name of the file, at most
1000 names and 512 MB of copies in all: a file with more is refused),
_scan/<blob id>.txt or _scan/deps/<blob id>.txt (a lock file), in UTF-8, and DIR/MAP says which file each copy is.
The path of every file the change adds or modifies is also a file, DIR/files/_scan/names/<id>.txt, because a path can
hold a secret; a path has no comment, so the text gitleaks:allow or secret-guard:allow in it accepts nothing. What
gitleaks skips is found by asking it (see
Export): if it cannot answer, or does not report a token it is given, this exits 2.

It prints paths, line numbers and variable names, never a value; in a path it hides the value of a .env variable
(<secret>) and any run of 20 letters, digits and underscores that has both letters and digits (<hidden>). Write `gitleaks:allow` (or
`secret-guard:allow`) on a line to accept a value that is meant to be there; files over 32 MB are
read in chunks and do not honor it. Files over 64 MB are not copied for gitleaks (announced). Run it with `python3 -I`.
Exit status: 0 clean, 1 something found, 2 the check itself failed (treat it as a failure).
SECRET_GUARD_ALLOW_UNREADABLE=1 turns an unreadable .env file from a failure into a warning.
"""
import codecs
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

MIN_LENGTH = 8
MAX_REPORT = 40
# The test suite shrinks the limits below through the environment (with SECRET_GUARD_TEST=1); a stray variable in a
# real shell must not be able to switch a check off.
TESTING = os.environ.get("SECRET_GUARD_TEST") == "1"


def limit(name, default):
    return max(1, int(os.environ.get(name) or default)) if TESTING else default


# Files up to WHOLE_BYTES are read at once, larger ones in CHUNK_BYTES pieces.
WHOLE_BYTES = limit("SECRET_GUARD_WHOLE_BYTES", 32 * 1024 * 1024)
CHUNK_BYTES = limit("SECRET_GUARD_CHUNK_BYTES", 8 * 1024 * 1024)
# Files copied for gitleaks (--export) up to this size; the .env values are checked at any size.
EXPORT_MAX_BYTES = limit("SECRET_GUARD_EXPORT_MAX_BYTES", 64 * 1024 * 1024)
# What the probe puts in a file to see whether gitleaks reads it: a line shaped like a GitHub token, written in
# pieces so that no scan of this very file flags it.
PROBE_TOKEN = "".join(("ghp_", "Q7xK2mV9bR4t", "Y8nL3cW6dF1hJ5sZ0pAe2uGv"))  # join: the compiler does not fold it
PROBE_LINE = ("token = " + PROBE_TOKEN + "\n").encode()
TWIN_DIR = "_scan"
# gitleaks takes a file for a format it skips from the first bytes it reads: marks at byte 0 and, for a few formats, at a
# fixed place (DICOM at 128, tar at 257, a disc image at 32769). It checks the first piece of 100 KB, then the next
# pieces too until it has read a newline. HEAD_BYTES is the start of a file that is shown to gitleaks to find out, a little
# further than the furthest mark it looks for.
HEAD_BYTES = 33 * 1024
LONG_LINE = 100 * 1000
HEAD_READ = LONG_LINE + 1
# One file that has several names gets a copy per different name, because the rules that depend on the path (.yaml, .tf,
# nuget.config) fire only on the copy that has the right name, and all of them look at the END of the path. A file with
# more names than this, or whose copies would add up to more than MAX_TWIN_BYTES, is refused: nothing is left out silently.
# The name of a copy is cut to its last TWIN_NAME_BYTES.
MAX_TWINS = limit("SECRET_GUARD_MAX_TWINS", 1000)
MAX_TWIN_BYTES = limit("SECRET_GUARD_MAX_TWIN_BYTES", 512 * 1024 * 1024)
TWIN_NAME_BYTES = 100
# In front of every copy made for gitleaks: no mark is found in spaces, whatever the format; the newline after them
# makes gitleaks stop checking the file type once it has read the first piece; and the content starts after byte
# 40 000, further than any mark it looks for. A line number in a copy is one more than in the file.
FILLER = b" " * 40000 + b"\n"
# Lock files and checksum lists: their hashes look like keys. Their copies go to _scan/deps/, where .gitleaks.toml
# switches off the one rule that fires on hashes (the same names, by regular expression, are in .gitleaks.toml).
LOCK_NAMES = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "deno.lock", "cargo.lock",
    "gemfile.lock", "podfile.lock", "composer.lock", "poetry.lock", "pipfile.lock", "pdm.lock", "uv.lock", "flake.lock",
    "mix.lock", "pubspec.lock", "conan.lock", "renv.lock", "paket.lock", "berksfile.lock", "packages.lock.json",
    "package.resolved", "cartfile.resolved", "go.sum", "go.work.sum", "verification-metadata.xml", ".terraform.lock.hcl",
)
DEPENDENCY_LIST = re.compile("|".join(re.escape(n) for n in LOCK_NAMES) + r"|.*\.lockfile", re.I | re.S)

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
                "SEED", "PAT", "JWT", "MNEMONIC"}
SECRET_SUFFIXES = ("PASSWORD", "PASSWD", "PASSPHRASE", "SECRET", "TOKEN", "APIKEY")
# A topic is a secret when it is the name of a public channel that anyone who knows it can read (ntfy.sh): a
# single word. An MQTT topic is a path with slashes (frigate/events), written all over the configuration.
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


HIDE = []  # the spellings of the .env values, longest first (set by main): a path that holds one is shown without it
LONG_RUN = re.compile(r"[A-Za-z0-9_]{20,}")


def _hide_run(match):
    run = match.group()
    return "<hidden>" if any(c.isdigit() for c in run) and any(c.isalpha() for c in run) else run


def show(text, mask=True):
    """Printable form of a path: control characters and undecodable bytes become \\x escapes. A path can hold a
    secret (a cache called after a URL), so the value of a .env variable in it becomes <secret> and a run of 20
    letters, digits and underscores that has both letters and digits (a long identifier or a token) becomes
    <hidden>; a token with hyphens, or made of letters only, is not recognised. That is for what this program prints;
    gitleaks prints the name of a file it flags on its own."""
    if mask:  # before the escapes: a secret can hold a tab, or another character that is escaped
        for spelling in HIDE:
            text = text.replace(spelling, "<secret>")
        text = LONG_RUN.sub(_hide_run, text)
    return "".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in text)


def twin_base(name):
    """The file name of NAME, cut to its last TWIN_NAME_BYTES bytes (not in the middle of a character): the rules
    that depend on the path look at the end of it."""
    raw = name.rsplit("/", 1)[-1].encode("utf-8", "surrogateescape")
    if len(raw) > TWIN_NAME_BYTES:
        raw = raw[-TWIN_NAME_BYTES:]
        while raw and 0x80 <= raw[0] < 0xC0:
            raw = raw[1:]
    return raw.decode("utf-8", "surrogateescape")


SURROGATE = re.compile("[\udc80-\udcff]")


def reported(path):
    """PATH as gitleaks writes it in its JSON report: each byte that is not UTF-8 (a surrogate escape here) as U+FFFD.
    The names of the test files and the names in the report are compared in that form."""
    return SURROGATE.sub("\ufffd", path)


NAME_MARKER = re.compile(rb"(gitleaks|secret-guard):allow", re.I)


def plain_name(path):
    """The bytes of PATH for a check of the name. In a file, `gitleaks:allow` on a line accepts what is on it; a name has no
    comment, so that text in a name is turned into text that accepts nothing, and a secret next to it is still found."""
    return NAME_MARKER.sub(rb"\1 allow", path.encode("utf-8", "surrogateescape"))


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


def topic_name(name):
    words = name_words(name)
    return bool(words) and words[-1] != "FILE" and "TOPIC" in words


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


CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def other_reason(raw, size):
    """Why gitleaks may stop reading a file that looks like plain text, which the probe of its head cannot show: its first
    line is so long that gitleaks checks the file type again at the start of its next piece, and any mark there (a text
    that begins with MZ, say) ends the reading."""
    if size > LONG_LINE and b"\n" not in raw[:LONG_LINE]:
        return "its first line is over 100 KB, and gitleaks checks the file type of each piece of such a line"
    return None


def classify(head):
    """(kind, codec) of a file from its first bytes: 'wide' for UTF-16 or UTF-32 text (and its codec), 'text' for
    UTF-8 text without control characters, 'binary' for anything else."""
    codec = text_codec(head)
    if codec:
        return "wide", codec
    if CONTROL_BYTES.search(head):
        return "binary", None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 3:  # a character cut by the end of HEAD_BYTES is not an error
            return "binary", None
    return "text", None


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
        if secret_name(name) or (topic_name(name) and "/" not in value):
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
    written 'abcd efgh ijkl mnop' is pasted as 'abcdefghijklmnop'); for hexadecimal values, in lower and upper
    case; and as a file format or a URL writes it: docker compose doubles every $, a URL percent-encodes, JSON and
    YAML escape quotes and backslashes, a shell script escapes $ " ` and \\ in double quotes and writes ' as '\\'' in
    single quotes, XML writes entities. Two of these at once are covered (docker compose doubling the $ of a value
    that a YAML string then escapes), three are not. Only a value with a special character has more than one spelling.
    Not looked for: base64, hexadecimal or reversed forms of a value, one cut in pieces, or the escapes of a language
    that is not listed here (Python repr, regular expressions...)."""
    out = {value}
    squeezed = "".join(value.split())
    if squeezed != value and len(squeezed) >= MIN_LENGTH:
        out.add(squeezed)
    if re.fullmatch(r"[0-9A-Fa-f]{16,}", value):
        out.update((value.lower(), value.upper()))
    for _ in range(2):  # twice: one format inside another (compose's $$ in a YAML string, a URL in a JSON string)
        for spelling in list(out):
            out.update((spelling.replace("$", "$$"), urllib.parse.quote(spelling, safe="", errors="replace"),
                        urllib.parse.quote_plus(spelling, errors="replace"), json.dumps(spelling)[1:-1],
                        json.dumps(spelling, ensure_ascii=False)[1:-1], json.dumps(spelling)[1:-1].replace("/", "\\/"),
                        spelling.replace("'", "''"), re.sub(r'([$"`\\])', r"\\\1", spelling), spelling.replace("'", "'\\''"),
                        html.escape(spelling, quote=True)))
    return {value} | {v for v in out if len(v) >= MIN_LENGTH}


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

    gitleaks decides by itself whether to look inside a file, and it does not say when it does not: it skips
    lock files, images, fonts, vendored directories and everything called gitleaks.toml by NAME; it skips a file
    that BEGINS like an executable, a SQLite database, an archive, a PDF, an Office document or a font, whatever
    its name (a text file that starts with "MZ" too), or that holds the mark of a tar archive, a DICOM image or a disc image
    at a fixed place (byte 257, 128, 32769); it stops reading a file whose first line is over 100 KB when the next piece
    of it starts with such a mark; and UTF-16 text is bytes that match nothing. A scan of the files themselves would let a
    secret through in all of those. So a file that gitleaks would not read gets a second copy that it does: FILLER (40 000
    spaces and a newline, which no mark can be found in, and after which gitleaks stops looking for one), then the file in
    UTF-8. It is called <twin dir>/<blob id>/<name> when gitleaks reads that name (some rules apply to a path: .yaml, .tf),
    once for each different name of the file (at most MAX_TWINS names and MAX_TWIN_BYTES of copies in all: a file with
    more is refused, and a long name keeps its end), else <twin dir>/<blob id>.txt, or <twin dir>/deps/<blob id>.txt for
    a lock file. The paths themselves are written by _write_names. The twin is named after the content, so that the
    Fingerprint gitleaks prints for a finding in it is the same next time and for that content only.

    What gitleaks skips is not listed here, because its list changes with its version: finish() asks it. It puts
    a token in a file of every name, and in a file of every distinct head (the first 33 KB) of a text file, and sees
    which ones gitleaks reports. A test file that must be read, and copies of the kind that must be read, prove that the
    answer means something; when they are not reported, gitleaks cannot be trusted and this raises an error
    rather than guess. A file that does not look like text gets a twin without asking: gitleaks has no
    recognisable rule for it.

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
        self.twin_dir = TWIN_DIR
        self.copies = []   # [blob id, names, relative paths of the copies, path of the first copy, size]
        self.twins = []    # (relative path of the twin, names, why)
        self.skipped = []
        self.names = {}    # {first 12 characters of the SHA-1 of a path: the path}: a path can hold a secret too

    def _target(self, name):
        """(path, path relative to the tree) of a free place for a copy called NAME. A name that is taken (two
        names that differ only by case, on a file system that folds case) or that cannot exist beside the others
        (a file `pkg` and a file `pkg/__init__.py` that share a blob in one push) gets its first directory renamed."""
        parts = name.split("/")
        if name.startswith("/") or any(p in ("", ".", "..") for p in parts):
            raise GuardError(f"cannot copy a file called {show(name)} for gitleaks")
        for attempt in range(1000):
            top = parts[0] if attempt == 0 else f"{parts[0]}~{attempt}"
            full = os.path.join(self.tree, top, *parts[1:])
            try:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                os.close(os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))  # the place is ours from now on
            except (FileExistsError, NotADirectoryError, IsADirectoryError):
                continue  # taken, or one of the directories on the way is a file
            except OSError as exc:
                raise GuardError(f"cannot copy {show(name)} for gitleaks: {exc.strerror}")
            return full, "/".join([top, *parts[1:]])
        raise GuardError(f"cannot find a place for a copy of {show(name)}")

    def take(self, blob, names, data):
        """Copy a file under each of NAMES. The data comes back for the other checks; a Big is copied while it
        is read. The second copy, if the file needs one, is made by finish()."""
        big = isinstance(data, Big)
        size = data.size if big else len(data)
        if size > EXPORT_MAX_BYTES:
            self.skipped.append(names[0])
            return data
        failing = names[0]
        try:
            targets = []
            for failing in names:
                targets.append(self._target(failing))
            if big:
                data.sinks = [open(path, "wb") for path, _ in targets]
            else:
                for path, _ in targets:
                    with open(path, "wb") as handle:
                        handle.write(data)
        except OSError as exc:
            raise GuardError(f"cannot copy {show(failing)} for gitleaks: {exc.strerror}")
        self.copies.append([blob, list(names), [rel for _, rel in targets], targets[0][0], size])
        return data

    def _sample(self, tree, rel, content):
        full = os.path.join(tree, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(content)

    def _ask(self, rels, keys, twin_names):
        """(the relative paths gitleaks skips, the keys it skips, the TWIN_NAMES it reads). RELS are the paths of the
        copies, KEYS {hash: path of a copy} the distinct heads of text files, TWIN_NAMES paths a twin could have. A file
        of each holds PROBE_LINE (a key: the head of the file as it is, then PROBE_LINE): one that is read is reported, one
        that is skipped is not. Seven more files have the form of a twin or of a name (FILLER, then a start that gitleaks skips, then
        PROBE_LINE): they prove that the twins and the names are read. Raises GuardError when the report cannot be trusted."""
        tops = {r.split("/")[0] for r in rels}
        control = "0-control.txt"
        while control in rels:
            control = "_" + control
        key_dir = "_k"
        while key_dir in tops or key_dir == self.twin_dir:
            key_dir += "_"
        shapes = {  # a copy of each kind that must be read, and why it matters that it is
            f"{self.twin_dir}/000000000000.txt": (FILLER + PROBE_LINE, "a copy"),
            f"{self.twin_dir}/deps/000000000000.txt": (FILLER + PROBE_LINE, "a copy of a lock file"),
            f"{self.twin_dir}/canary-1.txt": (FILLER + b"SQLite format 3\x00" + b"\x00" * 90 + b"\n" + PROBE_LINE, "a copy of a database"),
            f"{self.twin_dir}/canary-2.txt": (FILLER + b"\x7fELF\x02\x01\x01\x00" + bytes(range(1, 256)) * 3 + b"\n" + PROBE_LINE, "a copy of an executable"),
            f"{self.twin_dir}/canary-3.txt": (FILLER + b"MZ_NOTE=1\n" + PROBE_LINE, "a copy of a text that starts with MZ"),
            f"{self.twin_dir}/names/000000000000.txt": (b"\nMZ-1/" + PROBE_LINE, "the name of a file"),
            f"{self.twin_dir}/names/000000000001.txt": (FILLER + b"%PDF-1.4/" + PROBE_LINE, "the long name of a file"),
        }
        key_files = {key: f"{key_dir}/{n}.txt" for n, key in enumerate(sorted(keys))}
        work = tempfile.mkdtemp(prefix=".probe.", dir=self.base)
        try:
            tree, report = os.path.join(work, "tree"), os.path.join(work, "report.json")
            try:
                self._sample(tree, control, PROBE_LINE)
                for rel, (content, _) in shapes.items():
                    self._sample(tree, rel, content)
                for key, rel in key_files.items():
                    with open(keys[key], "rb") as handle:
                        head = handle.read(HEAD_BYTES)
                    self._sample(tree, rel, head + b"\n" + PROBE_LINE)
            except OSError as exc:
                raise GuardError(f"cannot prepare the test files for gitleaks: {exc.strerror}")
            for rel in rels:
                try:
                    self._sample(tree, rel, PROBE_LINE)
                except OSError:
                    pass  # a name that cannot be created is not reported, so its file gets a twin
            for rel in twin_names:
                try:
                    self._sample(tree, rel, PROBE_LINE)  # only the name matters here
                except OSError as exc:  # no answer about this name would mean no path rule for it: refuse
                    raise GuardError(f"cannot make a test file called {show(rel)} to ask gitleaks about that name: {exc.strerror}")
            cmd = [self.gitleaks, "dir", "--no-banner", "--no-color", "--log-level", "error", "--exit-code", "2",
                   "--report-format", "json", "--report-path", report]
            if self.config:
                cmd += ["--config", self.config]
            try:
                done = subprocess.run(cmd + ["."], cwd=tree, stdin=subprocess.DEVNULL, capture_output=True)
            except OSError as exc:
                raise GuardError(f"cannot run gitleaks: {exc.strerror or exc}")
            last = (done.stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1][:160]

            def untrusted(what):
                return GuardError("gitleaks cannot be trusted: given files that hold a token, it " + what + ". "
                                  "Is GITLEAKS_BIN a wrapper that cannot see the files, or a stub?"
                                  + (" (" + last + ")" if last else ""))

            if done.returncode != 2 or not os.path.isfile(report):
                raise untrusted(f"exited {done.returncode} and reported nothing")
            try:
                with open(report, "rb") as handle:
                    findings = json.load(handle)
                seen = {str(f["File"]).replace(os.sep, "/") for f in findings
                        if PROBE_TOKEN in str(f.get("Secret", "")) + str(f.get("Match", ""))}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                raise untrusted("wrote a report that cannot be read")
            if control not in seen:
                raise untrusted("did not report it in a plain text file (does .gitleaks.toml allow it?)")
            for rel, (_, what) in shapes.items():
                if rel not in seen:
                    raise GuardError(f"gitleaks cannot be trusted: it did not report a token in {what}, so "
                                     "the copies that this check relies on would go unscanned.")
            return ({rel for rel in rels if reported(rel) not in seen}, {key for key, rel in key_files.items() if rel not in seen},
                    {rel for rel in twin_names if reported(rel) in seen})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _write_twin(self, rel, source, codec):
        """The twin: FILLER, then the file, as UTF-8 when it is UTF-16 or UTF-32 (decoded in pieces)."""
        path, rel = self._target(rel)
        decoder = codecs.getincrementaldecoder(codec)("replace") if codec else None
        try:
            with open(source, "rb") as src, open(path, "wb") as dst:
                dst.write(FILLER)
                for chunk in iter(lambda: src.read(CHUNK_BYTES), b""):
                    dst.write(decoder.decode(chunk).encode("utf-8") if decoder else chunk)
                if decoder:
                    dst.write(decoder.decode(b"", True).encode("utf-8"))
        except OSError as exc:
            raise GuardError(f"cannot make a copy of {show(source)} for gitleaks: {exc.strerror}")
        return rel

    def add_name(self, name):
        """A path that the change adds or modifies: gitleaks reads it as a file of its own (see _write_names)."""
        self.names.setdefault(hashlib.sha1(name.encode("utf-8", "surrogateescape")).hexdigest()[:12], name)

    def _clone_twin(self, first, rel):
        """Another name for a twin that is written: a hard link when the file system allows it, else a copy."""
        source = os.path.join(self.tree, *first.split("/"))
        path, rel = self._target(rel)
        try:
            try:
                os.unlink(path)
                os.link(source, path)
            except OSError:
                shutil.copyfile(source, path)
        except OSError as exc:
            raise GuardError(f"cannot make a copy of {show(source)} for gitleaks: {exc.strerror}")
        return rel

    def _twin_targets(self, blob, why, names, candidates, kept_names, size):
        """The places of the twins of one blob: one in deps/ for a lock file, else one per different readable name,
        so that the rules that depend on the path fire for each of them (more than MAX_TWINS, or copies of more than
        MAX_TWIN_BYTES in all, is an error, not a shortcut), or _scan/<blob id>.txt when gitleaks reads none of the names."""
        if why == ["gitleaks skips the name"] and all(DEPENDENCY_LIST.fullmatch(basename(n)) for n in names):
            return [f"{self.twin_dir}/deps/{blob[:12]}.txt"]
        readable = sorted({c for c in candidates.get(blob, []) if c in kept_names}, key=lambda c: (c.lower(), c))
        if len(readable) > MAX_TWINS or len(readable) * size > MAX_TWIN_BYTES:
            raise GuardError(f"the file [blob {blob[:10]}] has {len(readable)} different names, and gitleaks cannot be "
                             f"asked about more than {MAX_TWINS} of them, or about copies that add up to more than "
                             f"{MAX_TWIN_BYTES >> 20} MB: commit it under fewer names.")
        return readable or [f"{self.twin_dir}/{blob[:12]}.txt"]

    def _write_names(self):
        """Every path that the change adds or modifies, as a file of its own: gitleaks finds a token in a file name
        (a cache called after a URL) as it finds one in a file. A newline in front keeps it from taking a name that
        starts with MZ or %PDF for a program; a long name gets FILLER, which a mark at byte 128 or 257 cannot reach.
        The file is called after the name (the start of its SHA-1), so that a Fingerprint accepted for it means that
        name only."""
        for ident, name in sorted(self.names.items()):
            text = plain_name(name).replace(b"\r", b" ").replace(b"\n", b" ")
            path, _ = self._target(f"{self.twin_dir}/names/{ident}.txt")
            try:
                with open(path, "wb") as handle:
                    handle.write((b"\n" if len(text) < 100 else FILLER) + text + b"\n")
            except OSError as exc:
                raise GuardError(f"cannot write the name of a file for gitleaks: {exc.strerror}")

    def finish(self):
        """Call once every file is copied (and, for a Big, read to its end): ask gitleaks what it would not read of
        the copies, give each such file its twin, and write the paths as files."""
        merged = {}
        for blob, names, rels, path, size in self.copies:
            if size == 0:
                continue  # nothing in it
            if blob in merged:
                merged[blob][0].extend(names)
                merged[blob][1].extend(rels)
            else:
                merged[blob] = [list(names), list(rels), path, size]
        tops = {n.split("/")[0] for _, names, _, _, _ in self.copies for n in names}
        while self.twin_dir in tops:  # a file of the change is called _scan: use another directory
            self.twin_dir += "_"
        if not merged and not self.names:
            return
        shapes, key_paths = {}, {}
        for blob, (names, rels, path, size) in merged.items():
            try:
                with open(path, "rb") as handle:
                    raw = handle.read(HEAD_READ)
            except OSError as exc:
                raise GuardError(f"cannot read the copy of {show(names[0])}: {exc.strerror}")
            kind, codec = classify(raw[:HEAD_BYTES])
            key = None
            if kind == "text":
                key = hashlib.sha1(raw[:HEAD_BYTES]).digest()
                key_paths.setdefault(key, path)
            shapes[blob] = (kind, codec, key, other_reason(raw, size) if kind == "text" else None)
        # A twin keeps the name of its file when that name is read by gitleaks: some rules apply to the path (.yaml, .tf).
        candidates = {}
        for blob, (names, rels, _, _) in merged.items():
            for name in names:
                base = twin_base(name)
                if base and base not in (".", ".."):
                    candidates.setdefault(blob, []).append(f"{self.twin_dir}/{blob[:12]}/{base}")
        blind_rels, blind_keys, kept_names = self._ask(
            [r for _, rels, _, _ in merged.values() for r in rels], key_paths, [c for cs in candidates.values() for c in cs])
        for blob, (names, rels, path, size) in merged.items():
            kind, codec, key, other = shapes[blob]
            why = []
            if any(r in blind_rels for r in rels):  # the rules of THAT name (.yaml...) need a copy called after it
                why.append("gitleaks skips the name")
            if kind == "wide":
                why.append("UTF-16 or UTF-32 text, as UTF-8")
            elif kind == "binary":
                why.append("it starts with binary data, and gitleaks skips some formats by that")
            elif key in blind_keys:
                why.append("gitleaks skips a file that starts like this")
            if other:
                why.append(other)
            if not why:
                continue
            first = None
            for target in self._twin_targets(blob, why, names, candidates, kept_names, size):
                rel = self._write_twin(target, path, codec) if first is None else self._clone_twin(first, target)
                first = first or rel
                self.twins.append((rel, names, "; ".join(why)))
        self._write_names()

    def note(self):
        """Warn about what was not copied, and write MAP: the hook prints it when gitleaks finds something,
        because a finding in a copy is named _scan/<blob id>.txt."""
        if self.skipped:
            warn(f"{len(self.skipped)} file(s) were not given to gitleaks, being too large to copy (over "
                 f"{EXPORT_MAX_BYTES >> 20} MB); the .env values were still checked: "
                 + ", ".join(show(p) for p in self.skipped[:5]))
        if not self.twins and not self.names:
            return
        lines = []
        if self.names:
            lines += [f"secret-guard: a finding in a file called {self.twin_dir}/names/<id>.txt is in the NAME of a file of the change",
                      "(<id> is the start of the SHA-1 of the path. The names that had something hidden in them are listed here;",
                      "README.md, \"Secret scan\", gives a command that lists the ids of all of them)."]
            hidden = [(ident, show(name)) for ident, name in sorted(self.names.items()) if show(name) != show(name, mask=False)]
            lines += [f"  {self.twin_dir}/names/{ident}.txt = {shown}" for ident, shown in hidden[:200]]
            if len(hidden) > 200:
                lines.append(f"  ... and {len(hidden) - 200} more")
        if self.twins:
            lines += ["secret-guard: gitleaks does not read some files as they are, so it scanned copies (40 000 spaces and a",
                      "newline were put in front: a line number in a copy is one more than in the file). A finding in a",
                      f"file called {self.twin_dir}/<id>.txt, {self.twin_dir}/<id>/<name> or {self.twin_dir}/deps/<id>.txt is in:"]
        for copy, names, why in self.twins[:200]:
            more = f" (and {len(names) - 1} other name(s))" if len(names) > 1 else ""
            lines.append(f"  {show(copy)} = {show(names[0])}{more}: {why}")
        if len(self.twins) > 200:
            lines.append(f"  ... and {len(self.twins) - 200} more")
        if self.twins:
            lines.append("The Fingerprint of such a finding names the copy by its content: it stays valid for that content only.")
            lines.append("<id> is the start of the object id of the file: `git ls-files -s | grep <id>` (after a push check: "
                         "`git log --all --oneline --find-object=<id>`) finds it. A name that holds a secret is shown without it.")
        try:
            with open(self.listing, "w") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            warn(f"cannot write the list of copies: {exc.strerror}")


def scan_name(report, export, sha, path, kind="blob"):
    """The path of a file can hold a secret (a cache called after a URL). It is looked at as text, and given to
    gitleaks as a file of its own; the report does not print it. A submodule has a path and no content here."""
    if export is not None:
        export.add_name(path)
    scanned = report.scanned
    report.content(f"the name of a file [{kind} {sha[:10]}]", plain_name(path))
    report.scanned = scanned  # the summary counts files, and a name is not one


def scan_staged(report, blobs, export=None):
    for mode, sha, path in list(staged_entries()):
        if carrier_name(path):
            report.carrier(path)
        scan_name(report, export, sha, path, "submodule" if mode == "160000" else "blob")
        if mode == "160000":  # a submodule: no content here
            continue
        data = blobs.get(sha)
        if data is None:
            continue
        if export is not None:  # a symlink holds its target: that text is what gets copied
            data = export.take(sha, [path], data)
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


def export_name(blob, path):
    """Where a blob of the pushed commits goes in the export: under its own id, so that two versions of a
    file do not meet and the report names the blob."""
    return f"{blob[:12]}/{path}"


def scan_objects(report, blobs, rev_args, where, export=None):
    entries = {}  # blob id -> every path it has in these commits
    for sha, names in list_objects(rev_args).items():
        entries[sha] = set(names)
    submodules = {}  # path -> commit id: a commit of another repository, so no content here, but a public path
    for path, sha, mode in changed_files(rev_args):
        if mode == "160000":
            submodules[path] = sha
        else:
            entries.setdefault(sha, set()).add(path)
    for names in entries.values():
        for path in names:
            if carrier_name(path):
                report.carrier(f"{path} ({where})")
    for path, sha in sorted(submodules.items()):
        if carrier_name(path):
            report.carrier(f"{path} ({where})")
        scan_name(report, export, sha, path, "submodule")
    for sha, names in entries.items():
        for path in sorted(names):
            scan_name(report, export, sha, path)
        data = blobs.get(sha)
        if data is None:
            continue
        if export is not None:
            data = export.take(sha, [export_name(sha, path) for path in sorted(names or {"blob"})], data)
        report.content(f"{sorted(names)[0] if names else '?'} [blob {sha[:10]}]", data)


def scan_range(report, blobs, rev_args, export=None):
    for arg in rev_args:
        if not re.fullmatch(r"\^?[0-9a-fA-F]{40}|\^?[0-9a-fA-F]{64}|--not|--remotes=.+", arg, re.S):  # a URL may hold spaces
            raise GuardError(f"unexpected revision argument: {show(arg, mask=False)}")
    scan_objects(report, blobs, rev_args, "in the commits to push", export)
    for entry in git("log", "-z", "--format=%h%n%B", *rev_args).split(b"\0"):
        head, _, body = entry.partition(b"\n")
        if head.strip():
            report.content(f"message of commit {head.decode('ascii', 'replace').strip()}", body)
    # the names and addresses of the author and the committer are public too, and no commit hook sees them
    for entry in git("log", "-z", "--format=%h%n%an%n%ae%n%cn%n%ce", *rev_args).split(b"\0"):
        head, _, who = entry.partition(b"\n")
        if head.strip():
            report.content(f"author or committer of commit {head.decode('ascii', 'replace').strip()}", who)


EXPORT_OPTIONS = ("--export", "--gitleaks", "--gitleaks-config")


def main(argv):
    argv, options = list(argv), {}
    while len(argv) >= 2 and argv[0] in EXPORT_OPTIONS:
        options[argv[0]] = argv[1]
        argv = argv[2:]
    mode, args = (argv[0] if argv else "--staged"), argv[1:]
    valid = {"--staged": not args, "--tree": not args, "--history": not args,
             "--message": len(args) in (1, 2), "--range": bool(args)}
    export_ok = not options or (mode in ("--staged", "--range") and "--export" in options and "--gitleaks" in options)
    if not valid.get(mode) or not export_ok:
        print(__doc__, file=sys.stderr)
        return 2
    message = os.path.abspath(args[0]) if mode == "--message" else None
    export_dir = os.path.abspath(options["--export"]) if "--export" in options else None
    os.chdir(git("rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").strip())

    secrets = load_secrets()
    HIDE[:] = sorted({spelling for value in secrets for spelling in variants(value)}, key=len, reverse=True)
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
                report.content(args[1] if len(args) == 2 else "commit message", handle.read())
        elif mode == "--range":
            blobs = Blobs()
            scan_range(report, blobs, args, export)
        elif mode == "--tree":
            scan_tree(report)
        else:
            blobs = Blobs()
            scan_objects(report, blobs, ["--all"], "in history")
        if export is not None:
            export.finish()
    except GuardError:
        report.print()  # what was found before the check itself failed is still said
        raise
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
