#!/usr/bin/env python3
"""The two halves of the commit-msg hook that need to understand a commit message.

    msgcheck.py extract FILE            print the text of the message that must be scanned for secrets
    msgcheck.py check FILE [--no-echo]  check the subject against Conventional Commits 1.0.0:
                                        exit 0 ok, 1 not a Conventional Commit

The scan text is the message as git left it in the file, CRs removed, minus the diff that
`git commit -v` appends under git's scissors line (git shows it for editing, never keeps it; a line
that only looks like it does not count, because git keeps what is below). Comment
lines stay in: `commit.cleanup=whitespace` and `--cleanup=verbatim` keep them in the commit. The
scissors line is only cut when an editor ran, because git exports GIT_EDITOR=: to the hook otherwise,
and then the whole file is the message, whatever it contains.

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
# The line git writes above the diff of `git commit -v`, character for character (24 dashes on each side):
# git only cuts the message at that exact line, so a look-alike must not hide the text below it.
SCISSORS = re.compile(rb"(\S+) -{24} >8 -{24}")
LONG_SUBJECT = 100


def show(text):
    return "".join(c if c.isprintable() else "\\x%02x" % ord(c) for c in text)


def split_lines(data):
    return [line[:-1] if line.endswith(b"\r") else line for line in data.split(b"\n")]


def editor_ran():
    return os.environ.get("GIT_EDITOR") != ":"


def scissors_at(lines):
    for index, line in enumerate(lines):
        if SCISSORS.fullmatch(line):
            return index
    return None


def comment_prefix(lines, cut):
    """The comment prefix git used: read off the scissors line, else from the configuration."""
    if cut is not None:
        return SCISSORS.fullmatch(lines[cut]).group(1)
    for key in ("core.commentString", "core.commentChar"):
        try:
            value = subprocess.run(["git", "config", "--get", key], capture_output=True).stdout.strip()
        except OSError:
            value = b""
        if value and value != b"auto":
            return value
    return b"#"


def scan_text(lines):
    cut = scissors_at(lines) if editor_ran() else None
    kept = lines if cut is None else lines[:cut]
    return b"\n".join(kept) + b"\n"


def subject_lines(lines):
    """The lines git keeps as the message: no comment lines when an editor ran, no leading blank line."""
    kept = lines
    if editor_ran():
        cut = scissors_at(lines)
        prefix = comment_prefix(lines, cut)
        kept = [line for line in (lines if cut is None else lines[:cut]) if not line.startswith(prefix)]
    while kept and not kept[0].strip():
        kept = kept[1:]
    return [line.decode("utf-8", "surrogateescape") for line in kept]


def check(lines, echo):
    kept = subject_lines(lines)
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
        lines = split_lines(handle.read())
    if argv[0] == "extract":
        sys.stdout.buffer.write(scan_text(lines))
        return 0
    return check(lines, echo="--no-echo" not in argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except OSError as error:
        print(f"msgcheck: {error.strerror or error}", file=sys.stderr)
        sys.exit(2)
