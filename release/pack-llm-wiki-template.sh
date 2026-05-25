#!/usr/bin/env bash
# Pack llm-wiki skeleton (templates + empty raw dirs) for GitHub Release.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/release-dist/llm-wiki-template.zip}"
WIKI="$ROOT/backend/llm-wiki"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"

cd "$WIKI"
zip -r "$OUT" \
  AGENTS.md \
  llm-wiki.md \
  README.md \
  raw/pdf/.gitkeep \
  raw/markdown/.gitkeep \
  raw/rag/.gitkeep \
  wiki/README.md \
  wiki/_templates/paper.md \
  wiki/_synthesis/README.md \
  wiki/_synthesis/.gitkeep

echo "Created $OUT"
