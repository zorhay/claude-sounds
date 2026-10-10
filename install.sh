#!/bin/bash
# Code Gossip — unified installer for Codex, Cursor, and Claude Code.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 -B "$SCRIPT_DIR/code-gossip/installer.py" install "$@"
