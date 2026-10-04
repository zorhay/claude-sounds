#!/usr/bin/env python3
"""Connect Codex lifecycle hooks to the existing Soundbar installation.

No hook output is emitted and no permission decisions are made. Hook trust is
managed by Codex; installation never modifies trust or config.toml.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


EVENTS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt",
    "PermissionRequest": "permission",
    "PostCompact": "compact",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
}
TAG = "soundbar/codex.py"


def normalize(data):
    """Return (sound event, narrator payload), or None for unhandled events."""
    if not isinstance(data, dict):
        return None
    data = copy.deepcopy(data)
    event = data.get("hook_event_name")
    if event in EVENTS:
        # Compaction has its own sound, so don't also play session_start.
        if event == "SessionStart" and data.get("source") == "compact":
            return None
        return EVENTS[event], data

    tool = data.get("tool_name")
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        inp = {"command": inp if isinstance(inp, str) else ""}
    data["tool_input"] = inp
    if event == "PreToolUse" and tool in ("apply_patch", "Edit", "Write"):
        if tool == "apply_patch":
            patch = inp.get("command", "")
            paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch, re.M)
            data["tool_name"] = "Edit"
            data["tool_input"] = {"file_path": ", ".join(paths), "new_string": patch[:600]}
        return "edit", data

    if event != "PostToolUse" or tool not in ("Bash", "exec_command", "shell", "shell_command"):
        return None
    data["tool_name"] = "Bash"
    command = inp.get("command", inp.get("cmd", ""))
    if isinstance(command, list):
        command = shlex.join(command)
    inp["command"] = command
    response = data.get("tool_response")
    if isinstance(response, dict):
        code = response.get("exitCode", response.get("exit_code"))
        output = response.get("stdout", response.get("output", ""))
        stderr = response.get("stderr", "")
    else:
        code, output, stderr = None, str(response or ""), ""
    if code is None:
        match = re.search(r"(?:Process exited with code|Exit code:)\s*(-?\d+)", output)
        code = int(match.group(1)) if match else None
    data["tool_response"] = {"exitCode": code, "stdout": output, "stderr": stderr}
    if code not in (None, 0, "0"):
        data["hook_event_name"] = "PostToolUseFailure"
        data["error_message"] = f"Command exited with {code}: {command}. {stderr or output}"
        return "error", data
    try:
        words = shlex.split(command)
    except ValueError:
        words = []
    if words and Path(words[0]).name in ("rg", "grep", "find", "fd"):
        return "search", data
    return "bash", data


def dispatch(data, soundbar):
    result = normalize(data)
    if result is None:
        return
    event, payload = result
    # Close captured output: background playback must not hold Codex's pipes
    # open or send narration/debug output back into the agent conversation.
    subprocess.run(
        ["/bin/bash", str(soundbar / "play.sh"), event],
        input=json.dumps(payload), text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4,
    )


def hook_definitions(adapter):
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter))}"
    hooks = {}
    for event in [*EVENTS, "PreToolUse", "PostToolUse"]:
        group = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        if event == "SessionStart":
            group["matcher"] = "^(startup|resume|clear)$"
        elif event == "PreToolUse":
            group["matcher"] = "^(apply_patch|Edit|Write)$"
        elif event == "PostToolUse":
            group["matcher"] = "^(Bash|exec_command|shell|shell_command)$"
        hooks[event] = [group]
    return hooks


def merge_hooks(document, new_hooks):
    """Replace only our handlers, including those inside shared groups."""
    document = copy.deepcopy(document)
    existing = document.get("hooks", {})
    if not isinstance(document, dict) or not isinstance(existing, dict):
        raise ValueError("Expected a JSON object with a hooks object")
    merged = {}
    for event, groups in existing.items():
        kept = []
        for group in groups:
            handlers = group.get("hooks", [])
            remaining = [h for h in handlers if TAG not in h.get("command", "")]
            if remaining or not handlers:
                kept.append({**group, "hooks": remaining})
        if kept:
            merged[event] = kept
    for event, groups in new_hooks.items():
        merged.setdefault(event, []).extend(groups)
    if merged:
        document["hooks"] = merged
    else:
        document.pop("hooks", None)
    return document


def install(codex_home, soundbar, dry_run=False, uninstall=False):
    target = codex_home / "hooks.json"
    adapter = soundbar / "codex.py"
    if not uninstall and not (soundbar / "play.sh").is_file():
        raise ValueError("Install Soundbar first with ./install.sh (or ./install.sh --dev)")
    original = target.read_text() if target.exists() else "{}"
    document = json.loads(original)
    updated = merge_hooks(document, {} if uninstall else hook_definitions(adapter))
    rendered = json.dumps(updated, indent=2) + "\n"
    if dry_run:
        print(f"Would {'remove Soundbar hooks from' if uninstall else 'merge Soundbar hooks into'} {target}")
        print(rendered, end="")
        return
    if not uninstall and Path(__file__).resolve() != adapter.resolve():
        shutil.copy2(__file__, adapter)
    if updated != document:
        codex_home.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_name("hooks.json.soundbar-backup"))
        fd, temporary = tempfile.mkstemp(prefix=".soundbar-hooks-", dir=codex_home)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(rendered)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print(f"Soundbar hooks {'removed from' if uninstall else 'installed in'} {target}")
    if not uninstall:
        print("Open /hooks in Codex CLI to review and trust the Soundbar commands, then start a new chat.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    soundbar = Path.home() / ".claude" / "soundbar"
    if args.install or args.uninstall:
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        try:
            install(codex_home, soundbar, args.dry_run, args.uninstall)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            parser.exit(1, f"Soundbar: {exc}\n")
    else:
        try:
            dispatch(json.load(sys.stdin), soundbar)
        except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
            # Sound feedback must never break a tool call or block approval.
            pass


if __name__ == "__main__":
    main()
