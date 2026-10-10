#!/usr/bin/env python3
"""Translate Cursor's user hooks into Code Gossip playback events.

Only observational hooks are registered; this adapter never makes permission
decisions or asks Cursor to continue a conversation.
"""

import json
import sys
from pathlib import Path


import shlex
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ENGINE


EVENTS = {
    "sessionStart": "session_start",
    "sessionEnd": "session_end",
    "afterFileEdit": "edit",
    "afterShellExecution": "bash",
    "afterMCPExecution": "tool",
    "postToolUseFailure": "error",
    "subagentStop": "subagent_stop",
    "preCompact": "pre_compact",
    "stop": "stop",
}


def hook_definitions(adapter):
    path = shlex.quote(str(adapter))
    command = f"{shlex.quote(sys.executable)} {path}"
    return {event: [{"command": f"if [ -f {path} ]; then {command} {event} || true; fi", "timeout": 5}]
            for event in EVENTS}


def normalize(event, data):
    if event not in EVENTS or not isinstance(data, dict):
        return None
    sound = EVENTS[event]
    if event == "stop":
        sound = {"error": "error", "aborted": "interrupt"}.get(data.get("status"), "stop")
    payload = {**data, "soundbar_agent": "cursor", "soundbar_event": sound}
    payload.setdefault("session_id", data.get("conversation_id", ""))
    if event == "subagentStop":
        payload["hook_event_name"] = "SubagentStop"
    if sound == "error":
        payload["error_message"] = str(data.get("error_message") or data.get("error") or "Agent turn failed")
    if event == "afterShellExecution":
        payload["tool_name"] = "Bash"
        payload["tool_input"] = {"command": data.get("command", "")}
        payload["tool_response"] = {"stdout": data.get("output", "")}
    elif event == "afterFileEdit":
        payload["tool_name"] = "Edit"
        payload["tool_input"] = {"file_path": data.get("file_path", "")}
    return sound, payload


def main():
    try:
        result = normalize(sys.argv[1], json.load(sys.stdin))
        if result:
            event, payload = result
            subprocess.run(
                ["/bin/bash", str(ENGINE / "play.sh"), event],
                input=json.dumps(payload), text=True, timeout=4,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except (IndexError, OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass  # Audio must never break an agent operation.


if __name__ == "__main__":
    main()
