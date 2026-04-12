#!/bin/bash
# Soundbar — Claude Code sound engine
# Two independent layers: effects + voice
# Usage: play.sh <event> [stdin: hook JSON]
# Events: stop, edit, bash, search, permission, error, subagent_start, subagent_stop, session_start, compact
#
# Sound mappings are read from sounds.json (shared with the web UI).
# Narration voice profile reads phrases.json (TTS-specific).

SND="$HOME/.claude/soundbar"
CFG="$SND/config.json"
[ ! -f "$CFG" ] && CFG="$SND/config.defaults.json"
[ ! -f "$CFG" ] && exit 0

# Read config (single jq call for speed)
IFS=$'\t' read -r EFFECTS_ON EFFECTS_PROFILE EFFECTS_VOL VOICE_ON VOICE_PROFILE VOICE_VOL VOICE SUBVOICE < <(
  jq -r '[
    (if .effects_on == true then "on" else "off" end),
    (.effects_profile // "default"),
    (.effects_volume // 100),
    (if .voice_on == true then "on" else "off" end),
    (.voice_profile // "narration"),
    (.voice_volume // 100),
    (.voice_main // "Tara"),
    (.voice_sub // "Aman")
  ] | @tsv' "$CFG" 2>/dev/null || printf 'on\tdefault\t100\toff\tnarration\t100\tTara\tAman'
)

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
EVENT="${1:-stop}"

# Preview overrides (for manual testing: FORCE_LAYER=voice ./play.sh stop)
[ -n "$FORCE_EFFECTS_PROFILE" ] && EFFECTS_PROFILE="$FORCE_EFFECTS_PROFILE" && EFFECTS_ON="on"
[ -n "$FORCE_VOICE_PROFILE" ] && VOICE_PROFILE="$FORCE_VOICE_PROFILE" && VOICE_ON="on"
[ "$FORCE_LAYER" = "effects" ] && VOICE_ON="off"
[ "$FORCE_LAYER" = "voice" ] && EFFECTS_ON="off"

# Drain hook stdin
cat > /dev/null

# ═══════════════════════════════════════════════════════
# play_sound — dispatch one layer via sounds.json
# ═══════════════════════════════════════════════════════

play_sound() {
  local layer="$1" profile="$2" event="$3" vol="$4"
  [ ! -f "$MANIFEST" ] && return

  # One jq call: type + primary value + dir
  local stype val dir
  IFS=$'\t' read -r stype val dir < <(
    jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" '
      .[$l][$p] as $prof |
      ($prof.dir // "") as $dir |
      (($prof.events // {})[$e] // {}) as $s |
      if   $s.file     then ["file",  $s.file, $dir]
      elif $s.files    then ["files", ($s.files | length | tostring), $dir]
      elif $s.sox      then ["sox",   $s.sox, ""]
      elif $s.sequence then ["seq",   ($s.sequence | length | tostring), $dir]
      else                  ["none",  "", ""]
      end | @tsv
    ' "$MANIFEST" 2>/dev/null
  ) || return

  case "$stype" in
    file)
      [[ "$val" != /* ]] && [ -n "$dir" ] && val="$SND/$dir/$val"
      afplay -v "$vol" "$val" &
      ;;
    files)
      local idx=$((RANDOM % val))
      val=$(jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" --argjson i "$idx" \
        '.[$l][$p].events[$e].files[$i]' "$MANIFEST")
      [ -n "$dir" ] && val="$SND/$dir/$val"
      afplay -v "$vol" "$val" &
      ;;
    sox)
      play -qn $val vol "$vol" &
      ;;
    seq)
      local idx=$((RANDOM % val)) gap f1 f2
      IFS=$'\t' read -r gap f1 f2 < <(
        jq -r --arg l "$layer" --arg p "$profile" --arg e "$event" --argjson i "$idx" \
          '.[$l][$p].events[$e] as $s |
           [($s.gap // 0.15 | tostring), $s.sequence[$i][0], $s.sequence[$i][1]] | @tsv' "$MANIFEST"
      )
      [ -n "$dir" ] && f1="$SND/$dir/$f1" && f2="$SND/$dir/$f2"
      (afplay -v "$vol" "$f1" && sleep "$gap" && afplay -v "$vol" "$f2") &
      ;;
  esac
}

# ═══════════════════════════════════════════════════════
# LAYER 1: VOICE
# ═══════════════════════════════════════════════════════

if [ "$VOICE_ON" = "on" ]; then
  if [ "$VOICE_PROFILE" = "narration" ]; then
    # Narration reads phrases.json (TTS, not in manifest)
    if [ -f "$PHRASES" ] && command -v jq &>/dev/null; then
      COUNT=$(jq -r ".[\"$EVENT\"] | length // 0" "$PHRASES" 2>/dev/null)
      if [ "$COUNT" -gt 0 ] 2>/dev/null; then
        IDX=$((RANDOM % COUNT))
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
