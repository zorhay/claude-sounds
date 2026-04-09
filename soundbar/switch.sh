#!/bin/bash
# Soundbar — CLI control for Claude Code sound layers
# Usage: switch.sh <command> [value]

SOUNDBAR="$HOME/.claude/soundbar"
CFG="$SOUNDBAR/config.json"
[ ! -f "$CFG" ] && CFG="$SOUNDBAR/config.defaults.json"
[ ! -f "$CFG" ] && { echo "Soundbar not installed. Run install.sh first."; exit 1; }

EFFECTS_PROFILES="ambient attention chiptune construction default factory minimal organic paper sci-fi submarine silent"
VOICE_PROFILES="narration generals"

# Read a config value
cfg_get() { jq -r ".$1 // \"$2\"" "$CFG" 2>/dev/null || echo "$2"; }

# Write a config value (string or boolean)
cfg_set() {
  local key="$1" val="$2"
  local user_cfg="$SOUNDBAR/config.json"
  # Ensure user config exists
  if [ ! -f "$user_cfg" ]; then
    cp "$SOUNDBAR/config.defaults.json" "$user_cfg" 2>/dev/null || echo '{}' > "$user_cfg"
  fi
  # Build new JSON
  local tmp
  if [ "$val" = "true" ] || [ "$val" = "false" ]; then
    tmp=$(jq ".$key = $val" "$user_cfg")
  else
    tmp=$(jq ".$key = \"$val\"" "$user_cfg")
  fi
  # Validate before writing
  if echo "$tmp" | jq . > /dev/null 2>&1; then
    echo "$tmp" > "$user_cfg"
  else
    echo "Error: failed to update config" >&2
    return 1
  fi
}

CMD="$1"
VALUE="$2"

if [ -z "$CMD" ]; then
  EFX_ON=$(cfg_get effects_on true)
  EFX_PROF=$(cfg_get effects_profile default)
  VOX_ON=$(cfg_get voice_on false)
  VOX_PROF=$(cfg_get voice_profile narration)
  VOICE=$(cfg_get voice_main Tara)
  SUBVOICE=$(cfg_get voice_sub Aman)

  echo "=== Effects Layer ==="
  echo "  Enabled:  $EFX_ON"
  echo "  Profile:  $EFX_PROF"
  echo "  Profiles: $EFFECTS_PROFILES"
  echo ""
  echo "=== Voice Layer ==="
  echo "  Enabled:  $VOX_ON"
  echo "  Profile:  $VOX_PROF"
  echo "  Main:     $VOICE"
  echo "  Subagent: $SUBVOICE"
  echo "  Profiles: $VOICE_PROFILES"
  echo ""
  echo "Commands:"
  echo "  switch.sh effects [on|off]       Toggle effects layer"
  echo "  switch.sh effects-profile <name>  Switch effects profile"
  echo "  switch.sh voice [on|off]          Toggle voice layer"
  echo "  switch.sh voice-profile <name>    Switch voice profile"
  echo "  switch.sh main-voice <name>       Change main TTS voice"
  echo "  switch.sh sub-voice <name>        Change subagent TTS voice"
  exit 0
fi

case "$CMD" in
  effects)
    if [ -z "$VALUE" ]; then
      CURRENT=$(cfg_get effects_on true)
      [ "$CURRENT" = "true" ] && VALUE="false" || VALUE="true"
    else
      [ "$VALUE" = "on" ] && VALUE="true" || VALUE="false"
    fi
    cfg_set effects_on "$VALUE"
    echo "Effects: $VALUE"
    ;;

  effects-profile)
    if [ -z "$VALUE" ]; then
      echo "Current: $(cfg_get effects_profile default)"
      echo "Available: $EFFECTS_PROFILES"
      exit 0
    fi
    if ! echo "$EFFECTS_PROFILES" | grep -qw "$VALUE"; then
      echo "Unknown effects profile: $VALUE"
      echo "Available: $EFFECTS_PROFILES"
      exit 1
    fi
    cfg_set effects_profile "$VALUE"
    echo "Effects profile: $VALUE"
    ;;

  voice)
    if [ -z "$VALUE" ]; then
      CURRENT=$(cfg_get voice_on false)
      [ "$CURRENT" = "true" ] && VALUE="false" || VALUE="true"
    else
      [ "$VALUE" = "on" ] && VALUE="true" || VALUE="false"
    fi
    cfg_set voice_on "$VALUE"
    echo "Voice: $VALUE"
    if [ "$VALUE" = "true" ]; then
      V=$(cfg_get voice_main Tara)
      say -v "$V" -r 200 "Voice on" &
    fi
    ;;

  voice-profile)
    if [ -z "$VALUE" ]; then
      echo "Current: $(cfg_get voice_profile narration)"
      echo "Available: $VOICE_PROFILES"
      exit 0
    fi
    if ! echo "$VOICE_PROFILES" | grep -qw "$VALUE"; then
      echo "Unknown voice profile: $VALUE"
      echo "Available: $VOICE_PROFILES"
      exit 1
    fi
    cfg_set voice_profile "$VALUE"
    echo "Voice profile: $VALUE"
    ;;

  main-voice)
    if [ -z "$VALUE" ]; then
      echo "Current: $(cfg_get voice_main Tara)"
      exit 0
    fi
    cfg_set voice_main "$VALUE"
    say -v "$VALUE" -r 200 "Hello, I am $VALUE" &
    echo "Main voice: $VALUE"
    ;;

  sub-voice)
    if [ -z "$VALUE" ]; then
      echo "Current: $(cfg_get voice_sub Aman)"
      exit 0
    fi
    cfg_set voice_sub "$VALUE"
    say -v "$VALUE" -r 190 "Hello, I am $VALUE" &
    echo "Subagent voice: $VALUE"
    ;;

  *)
    # Backwards compat: bare profile name = switch effects profile
    if echo "$EFFECTS_PROFILES" | grep -qw "$CMD"; then
      cfg_set effects_profile "$CMD"
      echo "Effects profile: $CMD"
    else
      echo "Unknown command: $CMD"
      echo "Run without arguments for help."
      exit 1
    fi
    ;;
esac
