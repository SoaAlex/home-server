#!/bin/sh
# Restore test, run inside the backup container: compare restored folders with the live ones (sha256).
# usage: filecheck.sh SNAPSHOT_EPOCH DIR...   restored copy in /scratch/files/DIR, live copy in /live/DIR
# One summary line per folder; "MISMATCH <path>" for a restored file that differs from a live file the
# live side did not modify after the snapshot (files changed or deleted since then are expected).
snap="$1"; shift
for d in "$@"; do
  r="/scratch/files$d"; l="/live$d"
  if [ ! -d "$r" ]; then echo "dir=$d files=0 identical=0 changed_since=0 mismatch=0"; continue; fi
  (cd "$r" && find . -type f -exec sha256sum {} +) > /tmp/r.txt
  (cd "$l" && find . -type f -exec sha256sum {} +) > /tmp/l.txt
  awk 'NR == FNR { live[substr($0, 67)] = substr($0, 1, 64); next }
       { p = substr($0, 67); h = substr($0, 1, 64)
         if (!(p in live)) print "GONE " p; else if (live[p] == h) print "SAME " p; else print "DIFF " p }' \
      /tmp/l.txt /tmp/r.txt > /tmp/cmp.txt
  files=$(wc -l < /tmp/r.txt)
  same=$(grep -c '^SAME ' /tmp/cmp.txt)
  gone=$(grep -c '^GONE ' /tmp/cmp.txt)
  grep '^DIFF ' /tmp/cmp.txt | cut -c6- > /tmp/diff.txt
  changed=0; bad=0
  while IFS= read -r p; do
    m=$(stat -c %Y "$l/$p" 2>/dev/null || echo 0)
    if [ "$m" -gt "$snap" ]; then changed=$((changed + 1)); else bad=$((bad + 1)); echo "MISMATCH $d/${p#./}"; fi
  done < /tmp/diff.txt
  echo "dir=$d files=$files identical=$same changed_since=$((changed + gone)) mismatch=$bad"
done
