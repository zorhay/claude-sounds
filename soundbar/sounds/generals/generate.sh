#!/bin/bash
# Generate C&C Generals-style voice lines using macOS TTS
# Run this to create the .aiff files locally. Requires macOS with `say` command.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

CMD_VOICE="Ralph"       # Commander voice
UNIT_VOICE="Reed"       # Unit voice
CMD_RATE=200
UNIT_RATE=190

gen() {
  local voice="$1" rate="$2" file="$3" text="$4"
  if [ ! -f "$DIR/$file" ]; then
    say -v "$voice" -r "$rate" -o "$DIR/$file" "$text"
    printf '  ✓ %s\n' "$file"
  else
    printf '  · %s (exists)\n' "$file"
  fi
}

echo "Generating generals voice lines..."
echo "  Commander: $CMD_VOICE  |  Unit: $UNIT_VOICE"
echo ""

# ── Single event lines ──

gen "$UNIT_VOICE"  "$UNIT_RATE"  "building.aiff"               "Building"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "yes_sir.aiff"                "Yes sir"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "scanning_area.aiff"          "Scanning area"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "awaiting_orders.aiff"        "Awaiting orders"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_lost.aiff"              "Unit lost"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "construction_complete.aiff"  "Construction complete"
gen "$CMD_VOICE"   "$CMD_RATE"   "command_center.aiff"         "Command center, online"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "upgrading.aiff"              "Upgrading"

# ── Commander lines (subagent start: commander delegates) ──

gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_moving_out.aiff"   "Moving out"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_move_out.aiff"     "Move out"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_deploy.aiff"       "Deploy"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_recon.aiff"        "Recon"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_need_intel.aiff"   "Need intel"

# ── Unit responses (subagent start: unit acknowledges) ──

gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_ready_action.aiff"    "Ready for action"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_on_my_way.aiff"       "On my way"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_consider_done.aiff"   "Consider it done"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_copy_that.aiff"       "Copy that"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_right_away.aiff"      "Right away"

# ── Unit reports (subagent stop: unit reports back) ──

gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_reporting.aiff"            "Reporting"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_mission_complete.aiff"     "Mission complete"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_target_neutralized.aiff"   "Target neutralized"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_area_clear.aiff"           "Area clear"
gen "$UNIT_VOICE"  "$UNIT_RATE"  "unit_all_done.aiff"             "All done"

# ── Commander acknowledgements (subagent stop: commander responds) ──

gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_good_work.aiff"     "Good work"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_excellent.aiff"      "Excellent"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_affirmative.aiff"    "Affirmative"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_acknowledged.aiff"   "Acknowledged"
gen "$CMD_VOICE"   "$CMD_RATE"   "cmd_carry_on.aiff"       "Carry on"

echo ""
echo "Done. $(ls "$DIR"/*.aiff 2>/dev/null | wc -l | tr -d ' ') files."
