#!/bin/bash
# Connect an existing Soundbar installation to Codex.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/soundbar/codex.py" --install "$@"
