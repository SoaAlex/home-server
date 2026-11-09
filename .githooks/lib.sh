# Helpers shared by the hooks. Sourced, never executed.

# Print the python3 to use, or fail.
find_python() {
  for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Print the gitleaks binary to use, or fail. GITLEAKS_BIN wins.
find_gitleaks() {
  for candidate in \
    "${GITLEAKS_BIN:-}" \
    "$(command -v gitleaks 2>/dev/null || true)" \
    "${HOME:-}/.local/bin/gitleaks" \
    /usr/local/bin/gitleaks \
    /opt/homebrew/bin/gitleaks \
    /home/linuxbrew/.linuxbrew/bin/gitleaks; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Print the gitleaks config: the repository's own, else the one beside the hooks
# directory. $1 = repository root, $2 = hooks directory.
find_gitleaks_config() {
  for candidate in "$1/.gitleaks.toml" "$2/../.gitleaks.toml"; do
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Whether the gitleaks in $gitleaks has a flag (read from `gitleaks dir --help`). $1 = the flag.
gitleaks_has_flag() {
  "$gitleaks" dir --help 2>&1 | grep -q -e "$1"
}

# Make a signal end the hook through its EXIT trap, which removes the temporary files: dash and busybox sh do not run
# an EXIT trap when a signal kills them. Call it after the trap on EXIT is set.
exit_on_signals() {
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

# Whether the gitleaks in $gitleaks reports a token on its standard input (uses $config when set). A gitleaks that
# scans nothing (a stub, a wrapper without access to the files) exits 0 for everything, and then "no leaks found"
# means nothing. The token is written in pieces so that no scan of this file flags it.
gitleaks_selftest() {
  set -- stdin --no-banner --no-color --log-level error
  if [ -n "${config:-}" ]; then
    set -- "$@" --config "$config"
  fi
  _gst_rc=0
  # The text begins with MZ, which gitleaks takes for a program and skips unless gitleaks_pad is in front of it:
  # a missing dd or tr, or a gitleaks that does not read the padded text, fails this test.
  printf 'MZ-1 token = ghp_%s%s\n' Q7xK2mV9bR4t Y8nL3cW6dF1hJ5sZ0pAe2uGv | gitleaks_pad | "$gitleaks" "$@" >/dev/null 2>&1 || _gst_rc=$?
  [ "$_gst_rc" -eq 1 ]
}

# gitleaks reads the text on its standard input as it reads a file: it takes the text for a program, a PDF or an archive,
# and skips all of it, when it begins like one (a message that starts with MZ-123, a ticket key) or holds "ustar" at
# byte 257. 40 000 spaces and a newline in front of the text keep it from looking at the text that way (no mark is
# found in spaces, and after the newline it stops looking). A line number in a finding is one more than in the text.
# Usage: gitleaks_pad <text | gitleaks stdin ...
gitleaks_pad() {
  dd if=/dev/zero bs=40000 count=1 2>/dev/null | tr '\0' ' '
  printf '\n'
  cat
}

# Whether secret-guard.py --export left files to scan in the directory $1.
export_has_files() {
  [ -d "$1/files" ] && [ -n "$(ls -A "$1/files")" ]
}

# Scan the copies that secret-guard.py --export made in the directory $1 with gitleaks, and print what it finds,
# redacted. When it finds something, the list of the copies (a finding in _scan/<id>.txt) follows. Needs
# $gitleaks; uses $config and $root when they are set. Returns gitleaks' exit status. It reads the files
# themselves: nothing here depends on how git prints a patch.
#
# --max-archive-depth lets gitleaks look into .zip, .tar, .gz, .bz2 and .xz files (by extension); without it
# it skips them. NEVER add --timeout: gitleaks then stops when the time is up and exits 0, "no leaks found",
# having read a tenth of the files.
gitleaks_scan_dir() {
  _gsd_dir=$1
  set -- dir --redact --verbose --no-banner --no-color --log-level warn
  if [ -n "${config:-}" ]; then
    set -- "$@" --config "$config"
  fi
  if [ -f "$root/.gitleaksignore" ]; then
    set -- "$@" --gitleaks-ignore-path "$root/.gitleaksignore"
  fi
  if gitleaks_has_flag --max-archive-depth; then
    set -- "$@" --max-archive-depth 3
  fi
  _gsd_rc=0
  (cd "$_gsd_dir/files" && "$gitleaks" "$@" .) || _gsd_rc=$?
  if [ "$_gsd_rc" -ne 0 ] && [ -s "$_gsd_dir/MAP" ]; then
    cat "$_gsd_dir/MAP" >&2
  fi
  return "$_gsd_rc"
}

# Run secret-guard.py. $1 = what to do (--staged, or --range followed by its revision arguments, which are
# the remaining arguments). Uses $python, $hooks_dir, $gitleaks, $config and $export_dir: when there is a
# gitleaks and an export directory the files are copied there for gitleaks_scan_dir.
run_secret_guard() {
  if [ -n "${export_dir:-}" ] && [ -n "${gitleaks:-}" ] && [ -n "${config:-}" ]; then
    "$python" -I "$hooks_dir/secret-guard.py" --export "$export_dir" --gitleaks "$gitleaks" \
      --gitleaks-config "$config" "$@"
  elif [ -n "${export_dir:-}" ] && [ -n "${gitleaks:-}" ]; then
    "$python" -I "$hooks_dir/secret-guard.py" --export "$export_dir" --gitleaks "$gitleaks" "$@"
  else
    "$python" -I "$hooks_dir/secret-guard.py" "$@"
  fi
}
