#!/usr/bin/env bash
# Fetch desktop specs via `codex exec` with a validated JSON schema.
# Writes the schema-matching object to $OUT_FILE; also prints to stdout.
set -euo pipefail

SCHEMA_PATH="$(dirname "$0")/desktop-specs.schema.json"
OUT_FILE="${OUT_FILE:-/tmp/codex-desktop-specs.json}"

timeout 5m codex --ask-for-approval never --sandbox danger-full-access exec \
  --cd "$HOME" \
  --skip-git-repo-check \
  --output-schema "$SCHEMA_PATH" \
  -o "$OUT_FILE" \
  "SSH into my desktop with 'ssh desktop' and gather its hardware/OS specs (CPU, RAM, GPU, disk, OS/kernel, hostname). Return structured JSON matching the schema."

echo
echo "--- Result written to $OUT_FILE ---"
cat "$OUT_FILE"
