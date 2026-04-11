#!/bin/bash
# Soundbar — Claude Code sound engine
# Two independent layers: effects + voice
# Usage: play.sh <event> [stdin: hook JSON]
# Events: stop, edit, bash, search, permission, error, subagent_start, subagent_stop, session_start, compact

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

PHRASES="$SND/phrases.json"
[ ! -f "$PHRASES" ] && PHRASES="$SND/phrases.defaults.json"
EVENT="${1:-stop}"
H=$(date +%H)

# Preview overrides: play a single layer+profile (used by panel UI)
[ -n "$FORCE_EFFECTS_PROFILE" ] && EFFECTS_PROFILE="$FORCE_EFFECTS_PROFILE" && EFFECTS_ON="on"
[ -n "$FORCE_VOICE_PROFILE" ] && VOICE_PROFILE="$FORCE_VOICE_PROFILE" && VOICE_ON="on"
[ "$FORCE_LAYER" = "effects" ] && VOICE_ON="off"
[ "$FORCE_LAYER" = "voice" ] && EFFECTS_ON="off"

# Drain hook stdin
cat > /dev/null

# ═══════════════════════════════════════════════════════
# LAYER 1: VOICE — spoken lines (narration or generals)
# ═══════════════════════════════════════════════════════

if [ "$VOICE_ON" = "on" ]; then
  case "$VOICE_PROFILE" in

    narration)
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
      ;;

    generals)
      S="$SND/sounds/generals"
      case "$EVENT" in
        stop)            afplay -v "$VOX_VOL" "$S/construction_complete.aiff" & ;;
        edit)            afplay -v "$VOX_VOL" "$S/building.aiff" & ;;
        bash)            afplay -v "$VOX_VOL" "$S/yes_sir.aiff" & ;;
        search)          afplay -v "$VOX_VOL" "$S/scanning_area.aiff" & ;;
        permission)      afplay -v "$VOX_VOL" "$S/awaiting_orders.aiff" & ;;
        error)           afplay -v "$VOX_VOL" "$S/unit_lost.aiff" & ;;
        subagent_start)
          case $((RANDOM % 5)) in
            0) (afplay -v "$VOX_VOL" "$S/cmd_moving_out.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/unit_ready_action.aiff") & ;;
            1) (afplay -v "$VOX_VOL" "$S/cmd_move_out.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/unit_on_my_way.aiff") & ;;
            2) (afplay -v "$VOX_VOL" "$S/cmd_deploy.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/unit_consider_done.aiff") & ;;
            3) (afplay -v "$VOX_VOL" "$S/cmd_recon.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/unit_copy_that.aiff") & ;;
            4) (afplay -v "$VOX_VOL" "$S/cmd_need_intel.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/unit_right_away.aiff") & ;;
          esac
          ;;
        subagent_stop)
          case $((RANDOM % 5)) in
            0) (afplay -v "$VOX_VOL" "$S/unit_reporting.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/cmd_good_work.aiff") & ;;
            1) (afplay -v "$VOX_VOL" "$S/unit_mission_complete.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/cmd_excellent.aiff") & ;;
            2) (afplay -v "$VOX_VOL" "$S/unit_target_neutralized.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/cmd_affirmative.aiff") & ;;
            3) (afplay -v "$VOX_VOL" "$S/unit_area_clear.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/cmd_acknowledged.aiff") & ;;
            4) (afplay -v "$VOX_VOL" "$S/unit_all_done.aiff" && sleep 0.15 && afplay -v "$VOX_VOL" "$S/cmd_carry_on.aiff") & ;;
          esac
          ;;
        session_start)   afplay -v "$VOX_VOL" "$S/command_center.aiff" & ;;
        compact)         afplay -v "$VOX_VOL" "$S/upgrading.aiff" & ;;
      esac
      ;;

  esac
fi

# ═══════════════════════════════════════════════════════
# LAYER 2: EFFECTS — audio sounds (profiles)
# ═══════════════════════════════════════════════════════

if [ "$EFFECTS_ON" = "on" ]; then
  case "$EFFECTS_PROFILE" in

    ambient)
      case "$EVENT" in
        stop)
          if [ "$H" -lt 12 ]; then
            play -v "$EFX_VOL" -qn synth 0.6 pluck 440 synth 0.6 pluck 554 synth 0.6 pluck 659 reverb 40 fade 0.01 0.6 0.3
          else
            play -v "$EFX_VOL" -qn synth 0.8 sine 220:330 reverb 60 fade 0.05 0.8 0.4
          fi
          ;;
        edit)       play -v "$EFX_VOL" -qn synth 0.25 pluck $((400 + RANDOM % 200)) reverb 30 fade 0.01 0.25 0.15 ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.3 sine $((250 + RANDOM % 100)) fade 0.05 0.3 0.2 reverb 40 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.15 triangle $((600 + RANDOM % 200)) reverb 25 fade 0.01 0.15 0.1 ;;
        permission) play -v "$EFX_VOL" -qn synth 0.3 pluck 440:660 reverb 50 fade 0.01 0.3 0.2 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.4 sine 400:200 reverb 60 fade 0.02 0.4 0.3 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.3 sine 300:600 reverb 50 fade 0.01 0.3 0.15 tremolo 4 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.25 sine 600:350 reverb 45 fade 0.01 0.25 0.15 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 0.8 pluck 330 synth 0.8 pluck 440 synth 0.8 pluck 550 reverb 60 fade 0.05 0.8 0.5 ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.5 pinknoise vol 0.2 fade 0.05 0.5 0.4 reverb 50 ;;
      esac
      ;;

    attention)
      case "$EVENT" in
        permission) play -v "$EFX_VOL" -qn synth 0.8 sine 396 vol 0.18 fade 0.15 0.8 0.5 reverb 60 ;;
        stop)       play -v "$EFX_VOL" -qn synth 0.9 sine 290:260 vol 0.13 fade 0.2 0.9 0.6 reverb 70 ;;
      esac
      ;;

    chiptune)
      case "$EVENT" in
        stop)       play -v "$EFX_VOL" -qn synth 0.08 square 523 : synth 0.08 square 659 : synth 0.08 square 784 : synth 0.15 square 1047 ;;
        edit)       play -v "$EFX_VOL" -qn synth 0.06 square $((800 + RANDOM % 400)) : synth 0.06 square $((900 + RANDOM % 500)) ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.1 square 200 : synth 0.05 square 250 : synth 0.05 square 300 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.05 square $((1000 + RANDOM % 500)) ;;
        permission) play -v "$EFX_VOL" -qn synth 0.08 square 600 : synth 0.08 square 800 : synth 0.12 square 600 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.1 square 200 : synth 0.1 square 150 : synth 0.15 square 100 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.05 square 400 : synth 0.05 square 600 : synth 0.05 square 800 : synth 0.05 square 1000 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.05 square 1000 : synth 0.05 square 800 : synth 0.05 square 600 : synth 0.05 square 400 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 0.1 square 523 : synth 0.1 square 659 : synth 0.1 square 784 : synth 0.2 square 1047 : synth 0.1 square 784 : synth 0.15 square 1047 ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.08 square 800 : synth 0.08 square 400 : synth 0.08 square 200 ;;
      esac
      ;;

    construction)
      S="$SND/sounds/construction"
      HAMMERS=("$S/hammer.mp3" "$S/hammering_single.mp3" "$S/hammering_few.mp3")
      case "$EVENT" in
        stop)            afplay -v "$EFX_VOL" "${HAMMERS[$((RANDOM % ${#HAMMERS[@]}))]}" ;;
        edit)            afplay -v "$EFX_VOL" "$S/saw.mp3" ;;
        bash)            afplay -v "$EFX_VOL" "$S/stapler_wood.mp3" ;;
        search)          afplay -v "$EFX_VOL" "$S/ratchet.mp3" ;;
        permission)      afplay -v "$EFX_VOL" "$S/whistle.mp3" ;;
        error)           afplay -v "$EFX_VOL" "$S/steel_drop.mp3" ;;
        subagent_start)  afplay -v "$EFX_VOL" "$S/walkie_start.mp3" ;;
        subagent_stop)   afplay -v "$EFX_VOL" "$S/walkie_tones.mp3" ;;
        session_start)   afplay -v "$EFX_VOL" "$S/jackhammer.mp3" ;;
        compact)         afplay -v "$EFX_VOL" "$S/metal_barrier.mp3" ;;
      esac
      ;;

    default)
      case "$EVENT" in
        stop)            afplay -v "$EFX_VOL" /System/Library/Sounds/Hero.aiff ;;
        edit)            afplay -v "$EFX_VOL" /System/Library/Sounds/Frog.aiff ;;
        bash)            afplay -v "$EFX_VOL" /System/Library/Sounds/Submarine.aiff ;;
        search)          afplay -v "$EFX_VOL" /System/Library/Sounds/Pop.aiff ;;
        permission)      afplay -v "$EFX_VOL" /System/Library/Sounds/Funk.aiff ;;
        error)           afplay -v "$EFX_VOL" /System/Library/Sounds/Basso.aiff ;;
        subagent_start)  afplay -v "$EFX_VOL" /System/Library/Sounds/Bottle.aiff ;;
        subagent_stop)   afplay -v "$EFX_VOL" /System/Library/Sounds/Ping.aiff ;;
        session_start)   afplay -v "$EFX_VOL" /System/Library/Sounds/Glass.aiff ;;
        compact)         afplay -v "$EFX_VOL" /System/Library/Sounds/Purr.aiff ;;
      esac
      ;;

    factory)
      case "$EVENT" in
        stop)       play -v "$EFX_VOL" -qn synth 0.15 noise vol 0.4 fade 0.01 0.15 0.05 : synth 0.4 square 180:160 tremolo 25 vol 0.3 fade 0.01 0.4 0.2 ;;
        edit)
          FREQ=$((100 + RANDOM % 80))
          play -v "$EFX_VOL" -qn synth 0.04 square $FREQ synth 0.04 noise vol 0.5 fade 0 0.04 0.03 : synth 0.15 sine $((FREQ * 3)) vol 0.15 fade 0 0.15 0.12
          ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.2 brownnoise vol 0.4 fade 0.01 0.2 0.1 : synth 0.1 sawtooth 120:60 vol 0.3 fade 0.01 0.1 0.05 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.08 sine $((1400 + RANDOM % 200)) vol 0.3 fade 0.005 0.08 0.06 repeat 1 reverb 50 ;;
        permission) play -v "$EFX_VOL" -qn synth 0.1 square 300 vol 0.35 : synth 0.05 noise vol 0.2 : synth 0.1 square 350 vol 0.35 fade 0.01 0.25 0.05 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.2 sawtooth 200:80 vol 0.4 : synth 0.1 noise vol 0.3 fade 0.01 0.3 0.1 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.25 sawtooth 80:200 tremolo 15 vol 0.3 fade 0.01 0.25 0.1 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.2 sawtooth 200:60 tremolo 10 vol 0.25 fade 0.01 0.2 0.12 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 0.6 sawtooth 60:180 tremolo 8 vol 0.35 fade 0.02 0.6 0.3 : synth 0.2 noise vol 0.15 fade 0.01 0.2 0.15 ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.15 brownnoise vol 0.3 : synth 0.1 square 100:50 vol 0.25 fade 0.01 0.25 0.1 ;;
      esac
      ;;

    minimal)
      case "$EVENT" in
        stop)       play -v "$EFX_VOL" -qn synth 0.2 sine 660 vol 0.3 fade 0.02 0.2 0.15 ;;
        edit)       play -v "$EFX_VOL" -qn synth 0.08 sine $((700 + RANDOM % 100)) vol 0.2 fade 0.01 0.08 0.05 ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.1 sine $((400 + RANDOM % 50)) vol 0.25 fade 0.01 0.1 0.06 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.06 sine $((900 + RANDOM % 100)) vol 0.15 fade 0.01 0.06 0.03 ;;
        permission) play -v "$EFX_VOL" -qn synth 0.06 sine 700 vol 0.25 fade 0.01 0.06 0.04 : synth 0.08 sine 750 vol 0.2 fade 0.01 0.08 0.05 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.15 sine 350:250 vol 0.2 fade 0.01 0.15 0.1 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.1 sine 500:700 vol 0.2 fade 0.01 0.1 0.06 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.1 sine 700:450 vol 0.15 fade 0.01 0.1 0.07 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 0.3 sine 440 vol 0.25 fade 0.03 0.3 0.2 ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.15 sine 600:400 vol 0.15 fade 0.02 0.15 0.1 ;;
      esac
      ;;

    organic)
      case "$EVENT" in
        stop)
          N1=$((400 + RANDOM % 100)); N2=$((500 + RANDOM % 100)); N3=$((600 + RANDOM % 100))
          play -v "$EFX_VOL" -qn synth 0.5 pluck $N1 synth 0.5 pluck $N2 synth 0.5 pluck $N3 chorus 0.6 0.9 50 0.4 0.25 2 reverb 30
          ;;
        edit)       play -v "$EFX_VOL" -qn synth 0.2 pluck $((500 + RANDOM % 300)) chorus 0.5 0.9 40 0.3 0.2 2 fade 0.01 0.2 0.1 ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.25 pluck $((300 + RANDOM % 150)) reverb 20 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.12 pluck $((700 + RANDOM % 300)) fade 0.01 0.12 0.05 ;;
        permission) play -v "$EFX_VOL" -qn synth 0.2 pluck 700 : synth 0.2 pluck 550 chorus 0.6 0.9 50 0.4 0.25 2 reverb 25 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.15 pluck 200 synth 0.15 noise vol 0.3 fade 0 0.15 0.1 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.15 pluck 400 : synth 0.15 pluck 550 : synth 0.15 pluck 700 reverb 20 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.2 pluck 650 : synth 0.2 pluck 430 chorus 0.5 0.8 40 0.3 0.2 2 reverb 20 ;;
        session_start)
          N1=$((350 + RANDOM % 50))
          play -v "$EFX_VOL" -qn synth 0.6 pluck $N1 synth 0.6 pluck $((N1 + 110)) synth 0.6 pluck $((N1 + 220)) chorus 0.7 0.9 55 0.4 0.3 2 reverb 40
          ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.3 pluck 300:200 vol 0.2 reverb 35 fade 0.02 0.3 0.25 ;;
      esac
      ;;

    paper)
      S="$SND/sounds/paper"
      FLIPS=("$S/page_flip.mp3" "$S/page_flip2.mp3" "$S/page_flip3.mp3")
      RIPS=("$S/paper_rip_short.mp3" "$S/paper_rip2_short.mp3")
      case "$EVENT" in
        stop)            afplay -v "$EFX_VOL" "$S/book_close.mp3" ;;
        edit)            afplay -v "$EFX_VOL" "$S/pencil_short.mp3" ;;
        bash)            afplay -v "$EFX_VOL" "$S/typewriter_short.mp3" ;;
        search)          afplay -v "$EFX_VOL" "${FLIPS[$((RANDOM % ${#FLIPS[@]}))]}" ;;
        permission)      afplay -v "$EFX_VOL" "$S/bell.mp3" ;;
        error)           afplay -v "$EFX_VOL" "$S/crumple_short.mp3" ;;
        subagent_start)  afplay -v "$EFX_VOL" "${RIPS[$((RANDOM % ${#RIPS[@]}))]}" ;;
        subagent_stop)   afplay -v "$EFX_VOL" "$S/page_turn_quick.mp3" ;;
        session_start)   afplay -v "$EFX_VOL" "$S/book_open.mp3" ;;
        compact)         afplay -v "$EFX_VOL" "$S/paper_rustle2.mp3" ;;
      esac
      ;;

    sci-fi)
      case "$EVENT" in
        stop)       play -v "$EFX_VOL" -qn synth 0.5 sine 300:900 fade 0.01 0.5 0.2 tremolo 6 reverb 40 ;;
        edit)       play -v "$EFX_VOL" -qn synth 0.15 sine $((800 + RANDOM % 600)):$((400 + RANDOM % 300)) fade 0.01 0.15 0.08 ;;
        bash)       play -v "$EFX_VOL" -qn synth 0.2 sawtooth 150:300 tremolo 10 fade 0.01 0.2 0.1 ;;
        search)     play -v "$EFX_VOL" -qn synth 0.1 sine $((1200 + RANDOM % 400)):$((800 + RANDOM % 200)) fade 0.01 0.1 0.05 ;;
        permission) play -v "$EFX_VOL" -qn synth 0.1 sine 800:1200 : synth 0.1 sine 1200:800 tremolo 8 fade 0.01 0.2 0.08 ;;
        error)      play -v "$EFX_VOL" -qn synth 0.3 sawtooth 600:100 tremolo 20 vol 0.4 fade 0.01 0.3 0.1 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.2 sine 200:1000 tremolo 6 fade 0.01 0.2 0.08 reverb 30 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.2 sine 1000:300 fade 0.01 0.2 0.1 reverb 25 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 0.6 sine 100:800 tremolo 3 reverb 40 fade 0.02 0.6 0.3 ;;
        compact)    play -v "$EFX_VOL" -qn synth 0.3 sine 800:200 tremolo 12 vol 0.3 fade 0.01 0.3 0.15 ;;
      esac
      ;;

    submarine)
      case "$EVENT" in
        stop)            play -v "$EFX_VOL" -qn synth 0.9 sine 290:260 vol 0.13 fade 0.2 0.9 0.6 reverb 70 ;;
        edit)            play -v "$EFX_VOL" -qn synth 0.5 sine 420:400 vol 0.10 fade 0.12 0.5 0.35 reverb 65 ;;
        bash)            play -v "$EFX_VOL" -qn synth 0.6 sine 180:160 vol 0.14 fade 0.15 0.6 0.4 reverb 60 ;;
        search)          play -v "$EFX_VOL" -qn synth 0.4 sine 530 vol 0.09 fade 0.1 0.4 0.28 reverb 75 ;;
        permission)      play -v "$EFX_VOL" -qn synth 0.8 sine 396 vol 0.18 fade 0.15 0.8 0.5 reverb 60 ;;
        error)           play -v "$EFX_VOL" -qn synth 1.0 sine 220:150 vol 0.12 fade 0.2 1.0 0.7 reverb 75 ;;
        subagent_start)  play -v "$EFX_VOL" -qn synth 0.6 sine 340:440 vol 0.11 fade 0.15 0.6 0.4 reverb 65 ;;
        subagent_stop)   play -v "$EFX_VOL" -qn synth 0.6 sine 440:340 vol 0.11 fade 0.15 0.6 0.4 reverb 65 ;;
        session_start)   play -v "$EFX_VOL" -qn synth 1.2 sine 260:330 vol 0.15 fade 0.3 1.2 0.8 reverb 80 ;;
        compact)         play -v "$EFX_VOL" -qn synth 0.7 sine 200 vol 0.08 fade 0.2 0.7 0.5 reverb 70 ;;
      esac
      ;;

    silent)
      # No sounds — intentional
      ;;

  esac
fi
