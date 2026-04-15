#!/bin/bash
# Soundbar — Installation verification test
# Validates that an installed soundbar is complete and functional.
# Usage: test-install.sh [target-dir]
#   default target: ~/.claude/soundbar
set -uo pipefail

DEST="${1:-$HOME/.claude/soundbar}"
SETTINGS="$HOME/.claude/settings.json"

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

echo "Soundbar installation test"
echo "─────────────────────────────────────"
echo "Target: $DEST"

# ── Files ──

printf '\n\033[1mFiles\033[0m\n'
for f in play.sh server.py integrations.py narrate.py kokoro_server.py ui.html panel.sh switch.sh sounds.json \
         config.defaults.json phrases.defaults.json; do
  check "$f" test -f "$DEST/$f"
done
for f in config.json phrases.json; do
  check "$f (user config)" test -f "$DEST/$f"
done

# ── JSON validity ──

printf '\n\033[1mJSON validity\033[0m\n'
for f in config.json config.defaults.json phrases.json phrases.defaults.json sounds.json; do
  check "$f" jq empty "$DEST/$f"
done

# ── Manifest structure ──

printf '\n\033[1mManifest (sounds.json)\033[0m\n'
check "has effects profiles" jq -e '.effects | keys | length > 0' "$DEST/sounds.json"
check "has voice profiles" jq -e '.voice | keys | length > 0' "$DEST/sounds.json"

# Verify every effects profile has an events object
EPROFILES=$(jq -r '.effects | keys[]' "$DEST/sounds.json" 2>/dev/null)
for p in $EPROFILES; do
  check "effects/$p has events" jq -e ".effects.\"$p\".events" "$DEST/sounds.json"
done

# Verify every voice profile has an events object
VPROFILES=$(jq -r '.voice | keys[]' "$DEST/sounds.json" 2>/dev/null)
for p in $VPROFILES; do
  check "voice/$p has events" jq -e ".voice.\"$p\".events" "$DEST/sounds.json"
done

# ── Sound assets ──

printf '\n\033[1mSound assets\033[0m\n'
# Check dirs referenced by manifest
for layer in effects voice; do
  for p in $(jq -r ".${layer} | to_entries[] | select(.value.dir) | .key" "$DEST/sounds.json" 2>/dev/null); do
    dir=$(jq -r ".${layer}.\"$p\".dir" "$DEST/sounds.json")
    check "$dir/ exists" test -d "$DEST/$dir"
    # Spot-check: at least one referenced file exists
    first=$(jq -r ".${layer}.\"$p\".events | to_entries[0].value |
      if .file then .file elif .files then .files[0]
      elif .sequence then .sequence[0][0] else empty end" "$DEST/sounds.json" 2>/dev/null)
    if [ -n "$first" ]; then
      check "$dir/$first" test -f "$DEST/$dir/$first"
    fi
  done
done

# ── Hooks ──

printf '\n\033[1mHooks\033[0m\n'
check "settings.json exists" test -f "$SETTINGS"
check "hooks reference soundbar/play.sh" \
  jq -e '.. | strings | select(contains("soundbar/play.sh"))' "$SETTINGS"

# ── Smoke tests ──

printf '\n\033[1mSmoke tests\033[0m\n'
check "play.sh is executable" test -x "$DEST/play.sh"
check "play.sh reads config" \
  bash -c "FORCE_LAYER=effects FORCE_EFFECTS_PROFILE=silent $DEST/play.sh stop < /dev/null"
check "play.sh reads sounds.json" \
  jq -e '.effects.default.events.stop.file' "$DEST/sounds.json"
check "integrations.py imports cleanly" \
  python3 -c "import sys; sys.path.insert(0,'$DEST'); import integrations"
check "server.py imports cleanly" \
  python3 -c "import sys; sys.path.insert(0,'$DEST'); import server"
check "narrate.py imports cleanly" \
  python3 -c "import sys; sys.path.insert(0,'$DEST'); import narrate"

# ── Results ──

echo ""
echo "─────────────────────────────────────"
printf "Results: \033[32m%d passed\033[0m" "$pass"
[ "$fail" -gt 0 ] && printf ", \033[31m%d failed\033[0m" "$fail"
echo ""
[ "$fail" -eq 0 ] || exit 1
