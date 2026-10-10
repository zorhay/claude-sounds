#!/bin/bash
# Code Gossip — Installation verification test
# Validates that an installed soundbar is complete and functional.
# Usage: test-install.sh [target-dir]
#   default target: ~/.code-gossip
set -uo pipefail

DEST="${1:-$HOME/.code-gossip}"

MANIFEST="$DEST/configs/sounds.json"
[ ! -f "$MANIFEST" ] && MANIFEST="$DEST/data/sounds.json"

pass=0
fail=0

check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  \033[32m✓\033[0m %s\n' "$desc"
    ((pass++))
  else
    printf '  \033[31m✗\033[0m %s\n' "$desc"
    ((fail++))
  fi
}

echo "Code Gossip installation test"
echo "─────────────────────────────────────"
echo "Target: $DEST"

# ── Files ──

printf '\n\033[1mFiles\033[0m\n'
for f in engine/play.sh engine/playback_gate.py soundbar/server.py engine/integrations.py engine/narrate.py engine/kokoro_server.py soundbar/ui.html soundbar/panel.sh switch.sh data/sounds.json installer.py paths.py hooks/codex.py hooks/cursor.py hooks/claude-hooks.json uninstall.sh \
         configs/config.defaults.json configs/phrases.defaults.json; do
  check "$f" test -f "$DEST/$f"
done
for f in configs/config.json configs/phrases.json; do
  check "$f (user config)" test -f "$DEST/$f"
done

# ── JSON validity ──

printf '\n\033[1mJSON validity\033[0m\n'
for f in configs/config.json configs/config.defaults.json configs/phrases.json configs/phrases.defaults.json data/sounds.json; do
  check "$f" jq empty "$DEST/$f"
done

# ── Manifest structure ──

printf '\n\033[1mManifest (data/sounds.json)\033[0m\n'
check "has effects profiles" jq -e '.effects | keys | length > 0' "$MANIFEST"
check "has voice profiles" jq -e '.voice | keys | length > 0' "$MANIFEST"

# Verify every effects profile has an events object
EPROFILES=$(jq -r '.effects | keys[]' "$MANIFEST" 2>/dev/null)
for p in $EPROFILES; do
  check "effects/$p has events" jq -e ".effects.\"$p\".events" "$MANIFEST"
done

# Verify every voice profile has an events object
VPROFILES=$(jq -r '.voice | keys[]' "$MANIFEST" 2>/dev/null)
for p in $VPROFILES; do
  check "voice/$p has events" jq -e ".voice.\"$p\".events" "$MANIFEST"
done

# ── Sound assets ──

printf '\n\033[1mSound assets\033[0m\n'
# Check dirs referenced by manifest
for layer in effects voice; do
  for p in $(jq -r ".${layer} | to_entries[] | select(.value.dir) | .key" "$MANIFEST" 2>/dev/null); do
    dir=$(jq -r ".${layer}.\"$p\".dir" "$MANIFEST")
    check "$dir/ exists" test -d "$DEST/data/$dir"
    # Spot-check: at least one referenced file exists
    first=$(jq -r ".${layer}.\"$p\".events | to_entries[0].value |
      if .file then .file elif .files then .files[0]
      elif .sequence then .sequence[0][0] else empty end" "$MANIFEST" 2>/dev/null)
    if [ -n "$first" ]; then
      check "$dir/$first" test -f "$DEST/data/$dir/$first"
    fi
  done
done

# ── Hooks ──

printf '\n\033[1mHooks\033[0m\n'
check "at least one agent has Code Gossip hooks" python3 -B -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import installer
connected = [agent for agent, path in installer.targets(Path.home()).items()
             if installer.is_connected(installer.read_document(path), agent)]
sys.exit(0 if connected else 1)
' "$DEST"

# ── Smoke tests ──

printf '\n\033[1mSmoke tests\033[0m\n'
check "engine/play.sh is executable" test -x "$DEST/engine/play.sh"
check "engine/play.sh reads config" \
  env FORCE_LAYER=effects FORCE_EFFECTS_PROFILE=silent bash "$DEST/engine/play.sh" stop < /dev/null
check "engine/play.sh reads data/sounds.json" \
  jq -e '.effects.default.events.stop.file' "$MANIFEST"
for module in engine.integrations soundbar.server engine.narrate; do
  check "$module imports cleanly" python3 -B -c '
import importlib, sys
sys.path.insert(0, sys.argv[1])
importlib.import_module(sys.argv[2])
' "$DEST" "$module"
done

# ── Results ──

echo ""
echo "─────────────────────────────────────"
printf "Results: \033[32m%d passed\033[0m" "$pass"
[ "$fail" -gt 0 ] && printf ", \033[31m%d failed\033[0m" "$fail"
echo ""
[ "$fail" -eq 0 ] || exit 1
