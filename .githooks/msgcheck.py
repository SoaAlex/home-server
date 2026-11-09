#!/usr/bin/env python3
"""The two halves of the commit-msg hook that need to understand a commit message.

    msgcheck.py extract FILE            print the text of the message that must be scanned for secrets
    msgcheck.py check FILE [--no-echo]  check the subject against Conventional Commits 1.0.0:
                                        exit 0 ok, 1 not a Conventional Commit

The scan text is what git keeps: the message as the file holds it, CRs removed, minus the diff that
`git commit -v` appends. Comment lines stay in, because `commit.cleanup=whitespace` and
`--cleanup=verbatim` keep them in the commit.

The diff is recognised by the lines git writes around it: a comment line `-------- >8 --------` (24
dashes on each side, exactly), then only comment lines, then `diff --git`. A line that merely looks like
the scissors line is the author's text, and git keeps what is below it, so it is scanned: a copy with
another prefix or with CRLF line ends, or one that is not followed by a diff. That cut is only looked for
when an editor ran, because git exports GIT_EDITOR=: to the hook otherwise, and then the whole file is
the message, whatever it contains.

The subject check ignores comment lines when an editor ran, as git does by default. With -m or -F the
message is kept as written, so "#123 fix login" really is a subject that is not a Conventional Commit.
Run it with `python3 -I`.
"""
import os
import re
import subprocess
import sys

TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")
SUBJECT = re.compile(r"(?:%s)(?:\([a-z0-9][a-z0-9._/,-]*\))?!?: \S" % "|".join(TYPES))
# Merge commits, git revert and its re-apply, and autosquash markers keep git's own wording.
EXEMPT = ("Merge ", 'Revert "', 'Reapply "', "fixup! ", "squash! ", "amend! ")
CUT = b"-" * 24 + b" >8 " + b"-" * 24
AUTO_CHARS = [c.encode() for c in "#;@!$%^&|:"]  # what core.commentChar=auto may pick
LONG_SUBJECT = 100


def show(text):
    return "".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in text)


def strip_cr(lines):
    return [line[:-1] if line.endswith(b"\r") else line for line in lines]


def editor_ran():
    return os.environ.get("GIT_EDITOR") != ":"


def comment_prefixes():
    """The comment prefixes git can be using: the configured one, '#' by default, any of AUTO_CHARS under auto."""
    for key in ("core.commentString", "core.commentChar"):
        try:
            value = subprocess.run(["git", "config", "--get", key], capture_output=True).stdout.rstrip(b"\n")
        except OSError:
            value = b""
        if value == b"auto":
            return AUTO_CHARS
        if value:
            return [value]
    return [b"#"]


def scissors_at(raw_lines):
    """(index, prefix) of the scissors line that git wrote above the diff of `git commit -v`, else None.
    `raw_lines` still carry their CR: git matches the line byte for byte, so does this."""
    for index, line in enumerate(raw_lines):
        for prefix in comment_prefixes():
            if line != prefix + b" " + CUT:
                continue
            after = index + 1
            while after < len(raw_lines) and raw_lines[after].startswith(prefix):
                after += 1
            if after < len(raw_lines) and raw_lines[after].startswith(b"diff --git "):
                return index, prefix
    return None


def scan_text(raw_lines):
    found = scissors_at(raw_lines) if editor_ran() else None
    kept = raw_lines if found is None else raw_lines[:found[0]]
    return b"\n".join(strip_cr(kept)) + b"\n"


def subject_lines(raw_lines):
    """The lines git keeps as the message: no comment lines when an editor ran, no leading blank line."""
    kept = strip_cr(raw_lines)
    if editor_ran():
        found = scissors_at(raw_lines)
        prefix = found[1] if found else comment_prefixes()[0]
        kept = strip_cr(raw_lines if found is None else raw_lines[:found[0]])
        kept = [line for line in kept if not line.startswith(prefix)]
    while kept and not kept[0].strip():
        kept = kept[1:]
    return [line.decode("utf-8", "surrogateescape") for line in kept]


def check(raw_lines, echo):
    kept = subject_lines(raw_lines)
    if not kept:
        return 0  # an empty message: git aborts the commit by itself
    subject, second = kept[0], kept[1] if len(kept) > 1 else ""
    if subject.startswith(EXEMPT):
        return 0
    if not SUBJECT.match(subject):
        shown = show(subject) if echo else "(not shown: the message was refused for another reason)"
        print("commit-msg: the subject is not a Conventional Commit:\n\n  %s\n\n"
              "Expected: <type>[(<scope>)][!]: <description>, with a lowercase type and scope\n"
              "  types:    %s\n"
              "  examples: feat(maison): add a surface filter\n"
              "            fix(pihole): raise the rate limit\n"
              "            chore: update .gitignore" % (shown, " ".join(TYPES)), file=sys.stderr)
        return 1
    if second.strip():
        print("commit-msg: leave a blank line between the subject and the body.", file=sys.stderr)
        return 1
    if len(subject) > LONG_SUBJECT:
        print(f"commit-msg: warning: the subject is {len(subject)} characters; keep it under {LONG_SUBJECT} "
              "and move details to the body.", file=sys.stderr)
    return 0


def main(argv):
    if len(argv) < 2 or argv[0] not in ("extract", "check") or (argv[0] == "extract" and len(argv) != 2) \
            or (argv[0] == "check" and argv[2:] not in ([], ["--no-echo"])):
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], "rb") as handle:
        raw_lines = handle.read().split(b"\n")
    if argv[0] == "extract":
        sys.stdout.buffer.write(scan_text(raw_lines))
        return 0
    return check(raw_lines, echo="--no-echo" not in argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as error:
        print(f"msgcheck: {error.strerror or error}", file=sys.stderr)
        sys.exit(2)
