#!/bin/bash
# Soundbar — Uninstall Claude Code sound system
# Removes hooks from settings.json, deletes installed files.
# User config (config.json, phrases.json) kept unless --purge.
set -euo pipefail

DEST="$HOME/.claude/soundbar"
SETTINGS="$HOME/.claude/settings.json"
TAG="soundbar/play.sh"

green() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
yellow() { printf '  \033[33m⚠\033[0m %s\n' "$1"; }
red() { printf '  \033[31m✗\033[0m %s\n' "$1"; }
phase() { printf '\n\033[1m[%s] %s\033[0m\n' "$1" "$2"; }

PURGE=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --purge)  PURGE=1 ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done

if [ ! -d "$DEST" ] && [ ! -L "$DEST" ]; then
  echo "Soundbar is not installed ($DEST not found)."
  exit 0
fi

IS_DEV=0
if [ -L "$DEST" ]; then
  IS_DEV=1
fi

echo "Soundbar uninstaller"
echo "─────────────────────────────────────"

# ═══════════════════════════════════════════
# Build operations list
# ═══════════════════════════════════════════

OPS=()

# Phase 1: Stop running processes
OPS+=("kill_server|Stop panel server if running|$DEST/.server.pid|")
OPS+=("kill_kokoro|Stop kokoro daemon if running|$DEST/kokoro.sock|")

# Phase 2: Remove hooks from settings.json
OPS+=("remove_hooks|Remove soundbar hooks from settings.json|$SETTINGS|$TAG")

# Phase 3: Remove files
if [ "$PURGE" = "1" ]; then
  OPS+=("remove_all|Remove $DEST (including user config)|$DEST|")
else
  OPS+=("remove_installed|Remove installed files (keep user config)|$DEST|")
fi

# Phase 4: Clean up
OPS+=("remove_file|Remove settings.json backup|$SETTINGS.soundbar-backup|")

# ═══════════════════════════════════════════
# Executors
# ═══════════════════════════════════════════

run_op() {
  local action="$1" desc="$2" arg1="$3" arg2="$4"

  case "$action" in
    kill_server)
      if [ -f "$arg1" ]; then
        local pid
        pid=$(cat "$arg1")
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null || true
          green "$desc (pid $pid)"
        else
          green "$desc — not running"
        fi
        rm -f "$arg1"
      else
        green "$desc — not running"
      fi
      ;;

    kill_kokoro)
      if [ -S "$arg1" ]; then
        local pid
        pid=$(lsof -t "$arg1" 2>/dev/null || true)
        if [ -n "$pid" ]; then
          kill "$pid" 2>/dev/null || true
          green "$desc (pid $pid)"
        else
          green "$desc — socket exists but no process found"
        fi
        rm -f "$arg1"
      else
        green "$desc — not running"
      fi
      ;;

    remove_hooks)
      if [ ! -f "$arg1" ]; then
        green "$desc — no settings.json"
        return 0
      fi
      if ! jq -e '.. | strings | select(contains("'"$arg2"'"))' "$arg1" > /dev/null 2>&1; then
        green "$desc — no soundbar hooks found"
        return 0
      fi
      local cleaned
      cleaned=$(jq '
        .hooks = (
          (.hooks // {}) | to_entries | map(
            .value = [.value[] | select(
              ([.. | strings | select(contains("soundbar/play.sh"))] | length) == 0
            )]
          ) | map(select(.value | length > 0)) | from_entries
        ) | if .hooks == {} then del(.hooks) else . end
      ' "$arg1")
      if echo "$cleaned" | jq . > /dev/null 2>&1; then
        echo "$cleaned" | jq . > "$arg1"
        green "$desc"
      else
        red "$desc — produced invalid JSON, settings.json unchanged"
      fi
      ;;

    remove_all)
      if [ "$IS_DEV" = "1" ]; then
        rm "$arg1"  # remove symlink only, not target
        green "$desc (removed symlink)"
      else
        rm -rf "$arg1"
        green "$desc"
      fi
      ;;

    remove_installed)
      if [ "$IS_DEV" = "1" ]; then
        rm "$arg1"  # remove symlink only, never touch source repo
        green "$desc (removed symlink)"
      else
        # Remove everything except user config
        find "$arg1" \( -type f -o -type s \) \
          ! -name 'config.json' \
          ! -name 'phrases.json' \
          -delete 2>/dev/null || true
        # Remove empty directories
        find "$arg1" -type d -empty -delete 2>/dev/null || true
        # If only config files remain, tell the user
        if [ -d "$arg1" ]; then
          yellow "$desc — kept config.json and phrases.json"
          yellow "Use --purge to remove everything"
        else
          green "$desc"
        fi
      fi
      ;;

    remove_file)
      if [ -f "$arg1" ]; then
        rm -f "$arg1"
        green "$desc"
      fi
      ;;
  esac
}

preview_op() {
  local action="$1" desc="$2" arg1="$3" arg2="$4"
  echo "  ○ $desc"
}

# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

if [ "$DRY_RUN" = "1" ]; then
  echo ""
  echo "Preview (no changes will be made):"
  echo ""
  for op in "${OPS[@]}"; do
    IFS='|' read -r action desc arg1 arg2 <<< "$op"
    preview_op "$action" "$desc" "$arg1" "$arg2"
  done
  echo ""
  echo "Run without --dry-run to uninstall."
  exit 0
fi

phase "1/4" "Stop processes"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in kill_server|kill_kokoro) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "2/4" "Remove hooks"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in remove_hooks) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "3/4" "Remove files"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in remove_all|remove_installed) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

phase "4/4" "Clean up"
for op in "${OPS[@]}"; do
  IFS='|' read -r action desc arg1 arg2 <<< "$op"
  case "$action" in remove_file) run_op "$action" "$desc" "$arg1" "$arg2" ;; esac
done

echo ""
echo "─────────────────────────────────────"
printf '\033[32mSoundbar uninstalled.\033[0m\n'
echo ""
