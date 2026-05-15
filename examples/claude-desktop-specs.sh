#!/usr/bin/env bash
# Fetch desktop specs via `claude -p` with a validated JSON schema.
# Prints the full wrapper JSON (with metadata: session_id, cost, usage, etc.).
# Pipe through `jq .structured_output` to get just the spec payload.
set -euo pipefail

SCHEMA_PATH="$(dirname "$0")/desktop-specs.schema.json"
SCHEMA="$(cat "$SCHEMA_PATH")"

timeout 5m claude -p \
  "SSH into my desktop with 'ssh desktop' and gather its hardware/OS specs (CPU, RAM, GPU, disk, OS/kernel, hostname). Return structured JSON matching the schema." \
  --dangerously-skip-permissions \
  --output-format json \
  --json-schema "$SCHEMA" \
  --max-turns 30
