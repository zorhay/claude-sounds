#!/bin/bash
# Code Gossip — selective uninstall, or --all for removal without questions.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# This script is also symlinked at the repository root.
if [ -f "$SCRIPT_DIR/soundbar/installer.py" ]; then
  SCRIPT_DIR="$SCRIPT_DIR/soundbar"
fi
exec python3 -B "$SCRIPT_DIR/installer.py" uninstall "$@"
