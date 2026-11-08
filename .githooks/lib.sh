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
