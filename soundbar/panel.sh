#!/bin/bash
# Soundbar — Control panel
# Runs the server in the foreground. Opens the browser. Ctrl+C stops everything.

PORT=8111
SND="$(cd "$(dirname "$0")" && pwd)"
SERVER="$SND/server.py"

# Resolve python3: config → which → fallback
CFG="$SND/config.json"
[ ! -f "$CFG" ] && CFG="$SND/config.defaults.json"
PYTHON3=""
if [ -f "$CFG" ] && command -v jq &>/dev/null; then
  PYTHON3=$(jq -r '.python3_path // ""' "$CFG" 2>/dev/null)
fi
if [ -z "$PYTHON3" ] || [ ! -x "$PYTHON3" ]; then
  PYTHON3="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
fi

# Already running?
if curl -s "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
  echo "Soundbar is already running on port $PORT."
  echo "Open http://localhost:$PORT in your browser, or Ctrl+C the other terminal."
  exit 1
fi

echo "Soundbar: http://localhost:$PORT"
echo "Press Ctrl+C to stop."
echo ""

# Wait for server to be ready, then open browser (background)
(
  for i in $(seq 1 30); do
    curl -s "http://localhost:$PORT/api/status" >/dev/null 2>&1 && break
    sleep 0.1
  done
  if command -v open &>/dev/null; then open "http://localhost:$PORT"
  elif command -v xdg-open &>/dev/null; then xdg-open "http://localhost:$PORT"
  fi
) &

# Run server in foreground — Ctrl+C kills it
exec "$PYTHON3" "$SERVER"
