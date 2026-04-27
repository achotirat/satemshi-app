#!/usr/bin/env bash
# Pre-commit guard: block commits that contain personal data patterns.
#
# Patterns are matched against the contents of the files passed in.
# Edit the BLOCKLIST and SAFE_PATTERNS arrays below to extend.
#
# Uses POSIX ERE (grep -E) so it runs on both BSD (macOS) and GNU grep.

set -euo pipefail

# Each entry is a POSIX ERE. Files matching any pattern fail.
BLOCKLIST=(
  # Personal email addresses. example.com/org/net are filtered as safe below.
  '[A-Za-z0-9._%+-]+@(gmail|yahoo|hotmail|outlook|icloud|protonmail|proton)\.[a-z]+'

  # Personal absolute paths. Generic placeholders are filtered as safe below.
  '/Users/[A-Za-z][A-Za-z0-9._-]+/'
  '/home/[A-Za-z][A-Za-z0-9._-]+/'

  # Tailscale CGNAT range — hints at a specific tailnet.
  '100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'

  # Specific vault path used by the maintainer.
  '/srv/satemshi/'
)

# Hits matching any of these patterns are treated as documentation
# placeholders, not real personal data.
SAFE_PATTERNS=(
  '@example\.(com|org|net)'
  '/Users/(user|youruser|placeholder|<[^>]+>)/'
  '/home/(user|youruser|placeholder|<[^>]+>)/'
  '/path/to/'
)

EXIT=0

# Files that legitimately contain blocklist patterns as data (the hook
# definition itself, its tests, and the spec docs that document them).
SELF_SKIP_RE='(scripts/check-no-personal-data\.sh|tests/check-no-personal-data\.bats|docs/.*personal-data.*\.md)$'

for file in "$@"; do
  [ -f "$file" ] || continue
  case "$file" in
    *.png|*.jpg|*.jpeg|*.gif|*.pdf|*.zip|*.gz|*.ico) continue ;;
  esac
  if printf '%s' "$file" | grep -Eq -- "$SELF_SKIP_RE"; then
    continue
  fi

  for pattern in "${BLOCKLIST[@]}"; do
    raw_hits=$(grep -En -- "$pattern" "$file" 2>/dev/null || true)
    [ -z "$raw_hits" ] && continue

    # Filter out lines that match any safe pattern.
    hits="$raw_hits"
    for safe in "${SAFE_PATTERNS[@]}"; do
      hits=$(printf '%s\n' "$hits" | grep -Ev -- "$safe" || true)
    done

    if [ -n "$hits" ]; then
      echo "::error file=$file:: blocked by no-personal-data hook (pattern: $pattern)"
      printf '%s\n' "$hits" | sed 's/^/    /'
      EXIT=1
    fi
  done
done

if [ "$EXIT" -ne 0 ]; then
  echo
  echo "Commit blocked: this is a public repo. Replace the hits above with"
  echo "generic placeholders (e.g. you@example.com, /path/to/your/vault),"
  echo "or extend SAFE_PATTERNS in $0 if a hit is a false positive."
fi

exit "$EXIT"
