#!/usr/bin/env bash
# Refuse any Ansible Vault file that is not encrypted.
# A vault file = a *vault*.yml / .yaml under group_vars/ or host_vars/.
#
#   check-vault-encrypted.sh                 # staged versions (git index), default
#   check-vault-encrypted.sh --range A..B    # every version added in a commit range
#   check-vault-encrypted.sh --tree          # working tree
#
# Exit 1 and name the file when one does not start with $ANSIBLE_VAULT;
# Never prints file contents. Usable as .git/hooks/pre-commit.
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
cd "$repo"
mode=${1:---staged}
pattern='(^|/)(group_vars|host_vars)/(.*/)?[^/]*vault[^/]*\.ya?ml$'
header='$ANSIBLE_VAULT;'
bad=0 checked=0

check_blob() {  # $1 = label, stdin = content (redirect, not a pipe: keeps $bad)
  local first
  first=$(head -c ${#header}; cat >/dev/null)
  checked=$((checked + 1))
  if [[ "$first" != "$header" ]]; then
    echo "NOT ENCRYPTED: $1" >&2
    bad=1
  fi
}

case "$mode" in
  --staged)
    while IFS= read -r -d '' path; do
      [[ "$path" =~ $pattern ]] || continue
      git cat-file -e ":$path" 2>/dev/null || continue   # staged deletion
      check_blob "$path (staged)" < <(git show ":$path")
    done < <(git ls-files -z)   # the index: tracked + newly staged files
    ;;
  --range)
    range=${2:?usage: --range A..B}
    while read -r commit; do
      while IFS= read -r -d '' path; do
        [[ "$path" =~ $pattern ]] || continue
        check_blob "$path (commit ${commit:0:7})" < <(git show "$commit:$path")
      done < <(git diff-tree --no-commit-id --name-only -r -z --diff-filter=ACMR "$commit")
    done < <(git rev-list "$range")
    ;;
  --tree)
    while IFS= read -r -d '' path; do
      [[ "$path" =~ $pattern ]] || continue
      check_blob "$path" < "$path"
    done < <(find . -path ./.git -prune -o -type f -print0 | sed -z 's|^\./||')
    ;;
  *)
    echo "usage: $0 [--staged | --range A..B | --tree]" >&2
    exit 2
    ;;
esac

if (( bad )); then
  echo "Encrypt it first: ansible-vault encrypt <file> (or use ansible/scripts/vault-set.py)." >&2
  exit 1
fi
echo "vault files encrypted: $checked checked"
