#!/bin/bash
# Soundbar — Claude Code sound engine
# Two independent layers: effects + voice
# Usage: play.sh <event> [stdin: hook JSON]
# Events: stop, edit, bash, search, permission, error, subagent_start, subagent_stop, session_start, compact
#
# Sound mappings are read from sounds.json (shared with the web UI).
# Narration voice profile reads phrases.json (TTS-specific).
# Narrator voice profile pipes hook JSON to narrate.py (LLM-powered).

# Capture hook stdin and event before backgrounding (~1ms)
STDIN_DATA=$(cat)
EVENT="${1:-stop}"

# Background all work — script exits in ~2ms, Claude Code proceeds immediately
{

SND="$HOME/.claude/soundbar"
CFG="$SND/config.json"
[ ! -f "$CFG" ] && CFG="$SND/config.defaults.json"
[ ! -f "$CFG" ] && exit 0

# Read config (single jq call for speed)
IFS=$'\t' read -r EFFECTS_ON EFFECTS_PROFILE EFFECTS_VOL VOICE_ON VOICE_PROFILE VOICE_VOL VOICE SUBVOICE TTS_ENGINE KOKORO_VOICE PYTHON3 < <(
  jq -r '[
    (if .effects_on == true then "on" else "off" end),
    (.effects_profile // "default"),
    (.effects_volume // 100),
    (if .voice_on == true then "on" else "off" end),
    (.voice_profile // "senior"),
    (.voice_volume // 100),
    (.voice_main // "Tara"),
    (.voice_sub // "Aman"),
    (.tts_engine // "say"),
    (.kokoro_voice // "af_heart"),
    (.python3_path // "")
  ] | @tsv' "$CFG" 2>/dev/null || printf 'on\tdefault\t100\toff\tsenior\t100\tTara\tAman\tsay\taf_heart\t'
)

# Resolve python3: config path → which → fallback
if [ -z "$PYTHON3" ] || [ ! -x "$PYTHON3" ]; then
  PYTHON3="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
fi

# Convert volume 0-100 to afplay scale 0-1 (pure bash, locale-safe)
printf -v EFX_VOL '%d.%02d' "$((EFFECTS_VOL / 100))" "$((EFFECTS_VOL % 100))"
printf -v VOX_VOL '%d.%02d' "$((VOICE_VOL / 100))" "$((VOICE_VOL % 100))"

# Volume-controlled say (macOS say has no volume flag; render to temp file, play with afplay)
say_vol() {
  local tmp
  tmp=$(mktemp /tmp/soundbar_say.XXXXXX.aiff)
  say "$@" -o "$tmp" && afplay -v "$VOX_VOL" "$tmp"
  rm -f "$tmp"
}

MANIFEST="$SND/sounds.json"
PHRASES="$SND/phrases.json"
[ ! -f "$PHRASES" ] && PHRASES="$SND/phrases.defaults.json"

# Preview overrides (for manual testing: FORCE_LAYER=voice ./play.sh stop)
[ -n "$FORCE_EFFECTS_PROFILE" ] && EFFECTS_PROFILE="$FORCE_EFFECTS_PROFILE" && EFFECTS_ON="on"
[ -n "$FORCE_VOICE_PROFILE" ] && VOICE_PROFILE="$FORCE_VOICE_PROFILE" && VOICE_ON="on"
[ "$FORCE_LAYER" = "effects" ] && VOICE_ON="off"
[ "$FORCE_LAYER" = "voice" ] && EFFECTS_ON="off"

# ═══════════════════════════════════════════════════════
# play_sound — dispatch one layer via sounds.json
# ═══════════════════════════════════════════════════════

play_sound() {
  local layer="$1" profile="$2" event="$3" vol="$4"
  [ ! -f "$MANIFEST" ] && return

  # One jq call: type + primary value + dir + rate range (centis)
  local stype val dir rate_min rate_max
  IFS=$'\t' read -r stype val dir rate_min rate_max < <(
    jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" '
      .[$l][$p] as $prof |
      ($prof.dir // "") as $dir |
      (($prof.events // {})[$e] // {}) as $s |
      ($s.rate // [1, 1]) as $r |
      if   $s.file     then ["file",  $s.file, $dir, ($r[0] * 100 | floor), ($r[1] * 100 | floor)]
      elif $s.files    then ["files", ($s.files | length | tostring), $dir, ($r[0] * 100 | floor), ($r[1] * 100 | floor)]
      elif $s.sox      then ["sox",   $s.sox, "", 100, 100]
      elif $s.sequence then ["seq",   ($s.sequence | length | tostring), $dir, ($r[0] * 100 | floor), ($r[1] * 100 | floor)]
      else                  ["none",  "", "", 100, 100]
      end | @tsv
    ' "$MANIFEST" 2>/dev/null
  ) || return

  # Compute random afplay rate flag from rate range
  local rate_flag=""
  if [ "$rate_min" -ne "$rate_max" ] 2>/dev/null; then
    local range=$((rate_max - rate_min))
    local rc=$((rate_min + RANDOM % (range + 1)))
    local rv
    printf -v rv '%d.%02d' "$((rc / 100))" "$((rc % 100))"
    rate_flag="-r $rv"
  fi

  case "$stype" in
    file)
      [[ "$val" != /* ]] && [ -n "$dir" ] && val="$SND/$dir/$val"
      afplay -v "$vol" $rate_flag "$val" &
      ;;
    files)
      local idx=$((RANDOM % val))
      val=$(jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" --argjson i "$idx" \
        '.[$l][$p].events[$e].files[$i]' "$MANIFEST")
      [ -n "$dir" ] && val="$SND/$dir/$val"
      afplay -v "$vol" $rate_flag "$val" &
      ;;
    sox)
      play -qn $val vol "$vol" &
      ;;
    seq)
      local idx=$((RANDOM % val))
      # Get gap + all files in one jq call (newline-separated, variable length)
      local seq_data
      seq_data=$(jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" --argjson i "$idx" \
        '.[$l][$p].events[$e] as $s | ($s.gap // 0.15 | tostring), $s.sequence[$i][]' "$MANIFEST")
      local gap
      local -a seq_files=()
      { read -r gap; while IFS= read -r line; do seq_files+=("$line"); done; } <<< "$seq_data"
      (
        for ((j=0; j<${#seq_files[@]}; j++)); do
          [ $j -gt 0 ] && sleep "$gap"
          local f="${seq_files[$j]}"
          [[ "$f" != /* ]] && [ -n "$dir" ] && f="$SND/$dir/$f"
          afplay -v "$vol" $rate_flag "$f"
        done
      ) &
      ;;
  esac
}

# ═══════════════════════════════════════════════════════
# LAYER 1: VOICE
# ═══════════════════════════════════════════════════════

if [ "$VOICE_ON" = "on" ]; then
  if [ "$VOICE_PROFILE" = "narrator" ]; then
    # Narrator: LLM-powered commentary via narrate.py
    echo "$STDIN_DATA" | "$PYTHON3" "$SND/narrate.py" &
  elif [ "$VOICE_PROFILE" = "senior" ]; then
    # Narration reads phrases.json (TTS, not in manifest)
    if [ -f "$PHRASES" ] && command -v jq &>/dev/null; then
      COUNT=$(jq -r ".[\"$EVENT\"] | length // 0" "$PHRASES" 2>/dev/null)
      if [ "$COUNT" -gt 0 ] 2>/dev/null; then
        IDX=$((RANDOM % COUNT))
        if [ "$TTS_ENGINE" = "kokoro" ]; then
          # Kokoro TTS: extract phrase, speak via narrate.py --speak
          case "$EVENT" in
            subagent_start)
              MAIN_P=$(jq -r ".[\"$EVENT\"][$IDX][0]" "$PHRASES")
              SUB_P=$(jq -r ".[\"$EVENT\"][$IDX][1]" "$PHRASES")
              ("$PYTHON3" "$SND/narrate.py" --speak "$MAIN_P" && "$PYTHON3" "$SND/narrate.py" --speak "$SUB_P") &
              ;;
            subagent_stop)
              SUB_P=$(jq -r ".[\"$EVENT\"][$IDX][0]" "$PHRASES")
              MAIN_P=$(jq -r ".[\"$EVENT\"][$IDX][1]" "$PHRASES")
              ("$PYTHON3" "$SND/narrate.py" --speak "$SUB_P" && "$PYTHON3" "$SND/narrate.py" --speak "$MAIN_P") &
              ;;
            *)
              PHRASE=$(jq -r ".[\"$EVENT\"][$IDX]" "$PHRASES")
              "$PYTHON3" "$SND/narrate.py" --speak "$PHRASE" &
              ;;
          esac
        else
          case "$EVENT" in
            subagent_start)
              MAIN_P=$(jq -r ".[\"$EVENT\"][$IDX][0]" "$PHRASES")
              SUB_P=$(jq -r ".[\"$EVENT\"][$IDX][1]" "$PHRASES")
              (say_vol -v "$VOICE" -r 200 "$MAIN_P" && sleep 0.2 && say_vol -v "$SUBVOICE" -r 190 "$SUB_P") &
              ;;
            subagent_stop)
              SUB_P=$(jq -r ".[\"$EVENT\"][$IDX][0]" "$PHRASES")
              MAIN_P=$(jq -r ".[\"$EVENT\"][$IDX][1]" "$PHRASES")
              (say_vol -v "$SUBVOICE" -r 190 "$SUB_P" && sleep 0.2 && say_vol -v "$VOICE" -r 200 "$MAIN_P") &
              ;;
            *)
              PHRASE=$(jq -r ".[\"$EVENT\"][$IDX]" "$PHRASES")
              say_vol -v "$VOICE" -r 200 "$PHRASE" &
              ;;
          esac
        fi
      fi
    fi
  else
    play_sound "voice" "$VOICE_PROFILE" "$EVENT" "$VOX_VOL"
  fi
fi

# ═══════════════════════════════════════════════════════
# LAYER 2: EFFECTS
# ═══════════════════════════════════════════════════════

if [ "$EFFECTS_ON" = "on" ]; then
  play_sound "effects" "$EFFECTS_PROFILE" "$EVENT" "$EFX_VOL"
fi

} &
