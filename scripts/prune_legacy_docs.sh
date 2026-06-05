#!/usr/bin/env bash
# Remove flat legacy docs/ files after accidental re-export from private.
# Keeps: index.md, index.ru.md, REPO_LINKS.md, llms.txt, subdirs, assets/
set -euo pipefail
DOCS="$(cd "$(dirname "$0")/../docs" && pwd)"
for f in "$DOCS"/*.md; do
  [[ -f "$f" ]] || continue
  base="$(basename "$f")"
  case "$base" in
    index.md|index.ru.md|REPO_LINKS.md) continue ;;
    *) rm -f "$f" && echo "removed $base" ;;
  esac
done
rm -f "$DOCS"/*.txt 2>/dev/null || true
echo "OK — docs root pruned; structured folders kept"
