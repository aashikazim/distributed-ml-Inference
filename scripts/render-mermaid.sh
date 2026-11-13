#!/usr/bin/env bash
# Render every Mermaid block in README.md and docs/ with the real Mermaid CLI.
# Exits non-zero if any diagram fails to parse (GitHub would show "Unable to render").
# Needs Node.js (npx) and a Chromium that Puppeteer can launch.
set -euo pipefail

MERMAID_CLI_VERSION="${MERMAID_CLI_VERSION:-12.2.0}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

uv run python -m tools.check_docs --extract "$work/blocks"
printf '{"args":["--no-sandbox"]}\n' >"$work/puppeteer.json"

failed=0
for block in "$work"/blocks/*.mmd; do
  name="$(basename "$block" .mmd)"
  if npx --yes "@mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION}" --quiet \
      -p "$work/puppeteer.json" -i "$block" -o "$work/$name.svg" >"$work/$name.log" 2>&1; then
    echo "ok    $name"
  else
    echo "FAIL  $name"
    grep -m3 -iE 'error|expect' "$work/$name.log" | sed 's/^/      /'
    failed=1
  fi
done
exit "$failed"
