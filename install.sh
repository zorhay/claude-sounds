#!/bin/bash
# Soundbar — Install Claude Code sound system
# Copies soundbar/ → ~/.claude/soundbar/, injects hooks into settings.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$SCRIPT_DIR/soundbar"
DEST="$HOME/.claude/soundbar"
SETTINGS="$HOME/.claude/settings.json"
BACKUP="$SETTINGS.soundbar-backup"
TAG="soundbar/play.sh"

green() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
yellow() { printf '  \033[33m⚠\033[0m %s\n' "$1"; }
red() { printf '  \033[31m✗\033[0m %s\n' "$1"; }
phase() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }

# ═══════════════════════════════════════════
# Parse flags
# ═══════════════════════════════════════════

DRY_RUN=0
DEV=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --dev)     DEV=1 ;;
  esac
done

# ═══════════════════════════════════════════
# Build operations list (same list for preview and execute)
# ═══════════════════════════════════════════

OPS=()
# op format: "action|description|arg1|arg2"

# Phase 1: Check requirements
OPS+=("check_cmd|Check jq (required)|jq|required")
OPS+=("check_cmd|Check python3 (required for panel)|python3|required")
OPS+=("check_cmd|Check sox (optional, for generated profiles)|sox|optional")
OPS+=("check_dir|Check source directory|$SRC|")

# Phase 2: Install files
if [ "$DEV" = "1" ]; then
  OPS+=("symlink|Symlink $DEST → $SRC|$SRC|$DEST")
else
  OPS+=("copy_dir|Copy soundbar/ → $DEST|$SRC|$DEST")
  OPS+=("chmod|Make scripts executable|$DEST/play.sh $DEST/switch.sh $DEST/panel.sh $DEST/uninstall.sh|")
fi

# Phase 3: Generate voice assets (macOS only)
OPS+=("generate_voices|Generate generals voice lines (macOS TTS)|$DEST/sounds/generals/generate.sh|")

# Phase 4: Create user config
if [ "$DEV" = "1" ]; then
  OPS+=("dev_config|Create config.json at repo root + symlink|$SCRIPT_DIR/config.json|$SRC/config.defaults.json")
  OPS+=("dev_config|Create phrases.json at repo root + symlink|$SCRIPT_DIR/phrases.json|$SRC/phrases.defaults.json")
else
  OPS+=("create_config|Create config.json from defaults|$DEST/config.defaults.json|$DEST/config.json")
  OPS+=("create_config|Create phrases.json from defaults|$DEST/phrases.defaults.json|$DEST/phrases.json")
fi

# Phase 4b: Migrate renamed config values
if [ "$DEV" = "1" ]; then
  OPS+=("migrate_config|Migrate voice_profile narration → senior|$SCRIPT_DIR/config.json|voice_profile")
else
  OPS+=("migrate_config|Migrate voice_profile narration → senior|$DEST/config.json|voice_profile")
fi

# Phase 5: Migrate old hooks, then inject current hooks
OPS+=("migrate_hooks|Remove deprecated/outdated soundbar hooks|$SETTINGS|$BACKUP")
OPS+=("inject_hooks|Inject hooks into settings.json|$SETTINGS|$BACKUP")

# Phase 6: Verify
OPS+=("verify|Verify installation|$DEST|")

# ═══════════════════════════════════════════
# Hook definitions
# ═══════════════════════════════════════════

HOOKS_JSON=$(cat <<'EOF'
{
  "Stop": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh stop", "timeout": 5}]}],
  "StopFailure": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh error", "timeout": 5}]}],
  "PermissionRequest": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh permission", "timeout": 5}]}],
  "SessionStart": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh session_start", "timeout": 5}]}],
  "PostCompact": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh compact", "timeout": 5}]}],
  "SubagentStart": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh subagent_start", "timeout": 5}]}],
  "SubagentStop": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh subagent_stop", "timeout": 5}]}],
  "PostToolUseFailure": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh error", "timeout": 5}]}],
  "PreToolUse": [
    {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh edit", "timeout": 5}]},
    {"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh bash", "timeout": 5}]},
    {"matcher": "Grep|Glob", "hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh search", "timeout": 5}]}
  ]
}
EOF
)

# ═══════════════════════════════════════════
# Operation executors
# ═══════════════════════════════════════════

run_op() {
  local action="$1" desc="$2" arg1="$3" arg2="$4"

  case "$action" in
    check_cmd)
      if command -v "$arg1" &>/dev/null; then
        green "$desc"
      elif [ "$arg2" = "required" ]; then
        red "$desc — not found"; return 1
      else
        yellow "$desc — not found (some profiles won't work)"
      fi
      ;;

    check_dir)
      if [ -d "$arg1" ]; then
        green "$desc"
      else
        red "$desc — not found"; return 1
      fi
      ;;

    symlink)
      if [ -L "$arg2" ]; then
        # Replace existing symlink
        rm "$arg2"
      elif [ -d "$arg2" ]; then
        red "$desc — $arg2 is a real directory (run uninstall.sh first)"
        return 1
      fi
      ln -s "$arg1" "$arg2"
      green "$desc"
      ;;

    copy_dir)
      mkdir -p "$arg2"
      rsync -a --exclude='.server.pid' --exclude='.manifest' "$arg1/" "$arg2/"
      green "$desc"
      ;;

    chmod)
      for f in $arg1; do
        [ -f "$f" ] && chmod +x "$f"
      done
      green "$desc"
      ;;

    generate_voices)
      if command -v say &>/dev/null; then
        bash "$arg1" 2>/dev/null
        green "$desc"
      else
        yellow "$desc — skipped (say not available, not macOS?)"
      fi
      ;;

    dev_config)
      # arg1 = repo root file, arg2 = defaults source
      local fname
      fname=$(basename "$arg1")
      [ ! -f "$arg1" ] && cp "$arg2" "$arg1"
      # Symlink inside soundbar/ → repo root
      local link="$SRC/$fname"
      if [ ! -L "$link" ]; then
        ln -sf "../$fname" "$link"
      fi
      # Stamp python3_path into config.json (detects actual interpreter)
      if [ -f "$arg1" ] && [[ "$fname" == "config.json" ]]; then
        local py3_path
        py3_path="$(command -v python3 2>/dev/null || echo "/usr/bin/python3")"
        if [ -x "$py3_path" ]; then
          local tmp
          tmp=$(jq --arg p "$py3_path" '.python3_path = $p' "$arg1")
          echo "$tmp" > "$arg1"
          green "Detected python3: $py3_path"
        fi
      fi
      green "$desc"
      ;;

    create_config)
      if [ -f "$arg2" ]; then
        green "$desc — already exists, kept"
      else
        cp "$arg1" "$arg2"
        green "$desc"
      fi
      # Stamp python3_path into config.json (detects actual interpreter)
      if [ -f "$arg2" ] && [[ "$(basename "$arg2")" == "config.json" ]]; then
        local py3_path
        py3_path="$(command -v python3 2>/dev/null || echo "/usr/bin/python3")"
        if [ -x "$py3_path" ]; then
          local tmp
          tmp=$(jq --arg p "$py3_path" '.python3_path = $p' "$arg2")
          echo "$tmp" > "$arg2"
          green "Detected python3: $py3_path"
        fi
      fi
      ;;

    migrate_config)
      if [ -f "$arg1" ] && jq -e '.voice_profile == "narration"' "$arg1" > /dev/null 2>&1; then
        local tmp
        tmp=$(jq '.voice_profile = "senior"' "$arg1")
        echo "$tmp" > "$arg1"
        green "$desc"
      else
        green "$desc — not needed"
      fi
      ;;

    migrate_hooks)
      if [ ! -f "$arg1" ]; then
        green "$desc — no settings.json yet"
        return 0
      fi
      if ! jq . "$arg1" > /dev/null 2>&1; then
        red "$desc — settings.json is invalid JSON, skipping"; return 1
      fi
      # Check for deprecated play-sound.sh hooks OR existing soundbar/play.sh hooks
      local has_deprecated has_current
      if jq -e '.. | strings | select(contains("play-sound.sh"))' "$arg1" > /dev/null 2>&1; then
        has_deprecated=1
      else
        has_deprecated=0
      fi
      if jq -e '.. | strings | select(contains("soundbar/play.sh"))' "$arg1" > /dev/null 2>&1; then
        has_current=1
      else
        has_current=0
      fi
      if [ "$has_deprecated" = "0" ] && [ "$has_current" = "0" ]; then
        green "$desc — no old hooks to migrate"
        return 0
      fi
      cp "$arg1" "$arg2"
      green "Backed up settings.json → $(basename "$arg2")"
      # Remove any hook entries containing play-sound.sh or soundbar/play.sh
      local cleaned
      cleaned=$(jq '
        .hooks = (
          (.hooks // {}) | to_entries | map(
            .value = [.value[] | select(
              ([.. | strings | select(contains("play-sound.sh") or contains("soundbar/play.sh"))] | length) == 0
            )]
          ) | map(select(.value | length > 0)) | from_entries
        ) | if .hooks == {} then del(.hooks) else . end
      ' "$arg1")
      if echo "$cleaned" | jq . > /dev/null 2>&1; then
        echo "$cleaned" | jq . > "$arg1"
        if [ "$has_deprecated" != "0" ]; then
          green "$desc — removed deprecated play-sound.sh hooks"
        else
          green "$desc — cleared old hooks for re-injection"
        fi
      else
        red "$desc — produced invalid JSON, restoring backup"
        cp "$arg2" "$arg1"
        return 1
      fi
      ;;

    inject_hooks)
      if [ -f "$arg1" ]; then
        if ! jq . "$arg1" > /dev/null 2>&1; then
          red "$desc — settings.json is invalid JSON, skipping"; return 1
        fi
        if jq -e '.. | strings | select(contains("soundbar/play.sh"))' "$arg1" > /dev/null 2>&1; then
          green "$desc — hooks already present"
          return 0
        fi
        if [ ! -f "$arg2" ]; then
          # Only backup if migrate_hooks didn't already
          cp "$arg1" "$arg2"
          green "Backed up settings.json → $(basename "$arg2")"
        fi
      else
        echo '{}' > "$arg1"
      fi
      local merged
      merged=$(jq --argjson new "$HOOKS_JSON" '
        .hooks = (.hooks // {}) |
        reduce ($new | to_entries[]) as $entry (.; .hooks[$entry.key] = ((.hooks[$entry.key] // []) + $entry.value))
      ' "$arg1")
      if echo "$merged" | jq . > /dev/null 2>&1; then
        echo "$merged" | jq . > "$arg1"
        green "$desc"
      else
        red "$desc — merge failed, restoring backup"
        [ -f "$arg2" ] && cp "$arg2" "$arg1"
        return 1
      fi
      ;;

    verify)
      local ok=1
      for f in play.sh switch.sh panel.sh server.py integrations.py narrate.py kokoro_server.py ui.html config.json phrases.json sounds.json; do
        [ ! -f "$arg1/$f" ] && { red "Missing: $f"; ok=0; }
      done
      [ "$ok" = "1" ] && green "$desc"
      ;;
  esac
}

preview_op() {
  local action="$1" desc="$2" arg1="$3" arg2="$4"

  case "$action" in
    check_cmd)    echo "  ○ $desc" ;;
    check_dir)    echo "  ○ $desc" ;;
    symlink)      echo "  ○ $desc" ;;
    copy_dir)     echo "  ○ $desc" ;;
    chmod)        echo "  ○ $desc" ;;
    dev_config)   echo "  ○ $desc" ;;
    generate_voices) echo "  ○ $desc" ;;
    create_config)
      if [ -f "$arg2" ]; then
        echo "  ○ $desc — already exists, will keep"
      else
        echo "  ○ $desc"
      fi
      ;;
    migrate_config)
      if [ -f "$arg1" ] && jq -e '.voice_profile == "narration"' "$arg1" > /dev/null 2>&1; then
        echo "  ○ $desc"
      else
        echo "  ○ $desc — not needed"
      fi
      ;;
    migrate_hooks)
      if [ -f "$arg1" ]; then
        local has_deprecated=0 has_current=0
        jq -e '.. | strings | select(contains("play-sound.sh"))' "$arg1" > /dev/null 2>&1 && has_deprecated=1
        jq -e '.. | strings | select(contains("soundbar/play.sh"))' "$arg1" > /dev/null 2>&1 && has_current=1
        if [ "$has_deprecated" != "0" ]; then
          echo "  ○ $desc — will remove deprecated play-sound.sh hooks"
        elif [ "$has_current" != "0" ]; then
          echo "  ○ $desc — will update existing hooks to current format"
        else
          echo "  ○ $desc — not needed"
        fi
      else
        echo "  ○ $desc — not needed"
      fi
      ;;
    inject_hooks) echo "  ○ $desc" ;;
    verify)       echo "  ○ $desc" ;;
  esac
}

# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

if [ "$DEV" = "1" ]; then
  echo "Soundbar installer (dev mode)"
else
  echo "Soundbar installer"
fi
echo "─────────────────────────────────────"

if [ "$DRY_RUN" = "1" ]; then
  echo ""
  echo "Preview (no changes will be made):"
  echo ""
  phase "1/6" "Requirements"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in check_cmd|check_dir) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  phase "2/6" "Install files"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in symlink|copy_dir|chmod) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  phase "3/6" "Generate voice assets"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in generate_voices) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  phase "4/6" "User configuration"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in create_config|dev_config|migrate_config) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  phase "5/6" "Hook injection"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in migrate_hooks|inject_hooks) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  phase "6/6" "Verify"
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    case "$action" in verify) preview_op "$action" "$desc" "$arg1" "$arg2" ;; esac
  done
  echo ""
  echo "Run without --dry-run to install."
  exit 0
fi

phase "1/6" "Requirements"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in check_cmd|check_dir) run_op "$action" "$desc" "$arg1" "$arg2" || exit 1 ;; esac
done

phase "2/6" "Install files"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in symlink|copy_dir|chmod) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "3/6" "Generate voice assets"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in generate_voices) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "4/6" "User configuration"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in create_config|dev_config|migrate_config) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "5/6" "Hook injection"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in migrate_hooks|inject_hooks) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "6/6" "Verify"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in verify) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

echo ""
echo "─────────────────────────────────────"
printf '\033[32mSoundbar installed.\033[0m\n'
echo ""
echo "  Panel:      ~/.claude/soundbar/panel.sh"
echo "  CLI:        ~/.claude/soundbar/switch.sh"
echo "  Uninstall:  ~/.claude/soundbar/uninstall.sh"
echo ""
