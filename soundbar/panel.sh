#!/bin/bash
# Soundbar — Control panel
# Runs the server in the foreground. Opens the browser. Ctrl+C stops everything.

PORT=8111
SND="$(cd "$(dirname "$0")" && pwd)"
SERVER="$SND/server.py"

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
exec python3 "$SERVER"
