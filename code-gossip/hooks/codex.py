#!/usr/bin/env python3
"""Connect Codex lifecycle hooks to the existing Code Gossip installation.

No hook output is emitted and no permission decisions are made. Hook trust is
managed by Codex; installation never modifies trust or config.toml.
"""

import copy
import json
import sys
from pathlib import Path


import re
import shlex
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT


EVENTS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt",
    "PermissionRequest": "permission",
    "PreCompact": "pre_compact",
    "PostCompact": "compact",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "SessionEnd": "session_end",
    "Interrupt": "interrupt",
}
SHELL_TOOLS = {"Bash", "exec_command", "shell", "shell_command"}
# Polling and coordination already have their own lifecycle or completion hooks.
QUIET_TOOLS = {"write_stdin", "wait", "wait_agent", "wait_threads", "spawn_agent", "Agent"}


def command_segments(command, depth=0):
    """Inspect shell syntax without executing it; unwrap common Codex commands."""
    if depth > 3:
        return []
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        segments, words = [], []
        for token in lexer:
            if token and all(c in ";&|()\n" for c in token):
                if words:
                    segments.append(words)
                    words = []
            else:
                words.append(token)
        if words:
            segments.append(words)
    except ValueError:
        return []
    result = []
    for words in segments:
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)
        if not words:
            continue
        if Path(words[0]).name == "env":
            words.pop(0)
            while words and (words[0].startswith("-") or "=" in words[0]):
                flag = words.pop(0)
                if flag in ("-u", "--unset", "-C", "--chdir") and words:
                    words.pop(0)
        if words and Path(words[0]).name in ("command", "exec", "sudo", "timeout"):
            wrapper = Path(words.pop(0)).name
            while words and words[0].startswith("-"):
                flag = words.pop(0)
                if flag in ("-u", "-g") and words:
                    words.pop(0)
            if wrapper == "timeout" and words:
                words.pop(0)
        if not words:
            continue
        executable = Path(words[0]).name
        if executable in ("bash", "sh", "zsh", "fish"):
            for index, arg in enumerate(words[1:], 1):
                if arg.startswith("-") and not arg.startswith("--") and "c" in arg and index + 1 < len(words):
                    result.extend(command_segments(words[index + 1], depth + 1))
                    break
            else:
                result.append(words)
        elif executable == "uv" and len(words) > 2 and words[1] == "run":
            args = words[2:]
            while args and args[0].startswith("-"):
                flag = args.pop(0)
                if flag in ("--project", "--directory", "--python", "--with") and args:
                    args.pop(0)
            if args:
                result.extend(command_segments(shlex.join(args), depth + 1))
        elif executable not in ("cd", "pwd", "echo", "printf", "export", "set", "source"):
            result.append(words)
    return result


def git_command_kind(args):
    """Find the subcommand after Git global options without executing Git."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--':
            index += 1
            break
        if not arg.startswith('-'):
            break
        if arg in ('--help', '-h', '--version', '-v'):
            return 'git'
        if arg in ('-C', '-c', '--git-dir', '--work-tree', '--namespace',
                   '--super-prefix', '--config-env', '--exec-path'):
            index += 2
        else:
            index += 1
    if index >= len(args):
        return 'git'
    subcommand = args[index]
    options = args[index + 1:]
    # Preview and help commands must never announce a saved commit or push.
    if any(arg in ('--dry-run', '--help', '-h') for arg in options):
        return 'git'
    if subcommand == 'push' and any(arg.startswith('-') and not arg.startswith('--') and 'n' in arg for arg in options):
        return 'git'  # -n means --dry-run for push, but --no-verify for commit.
    if subcommand == 'commit' and any(arg in ('--short', '--porcelain', '--long') for arg in options):
        return 'git'  # These status formats imply --dry-run.
    return {'status': 'git_status', 'log': 'git_history', 'show': 'git_history',
            'reflog': 'git_history', 'commit': 'git_commit', 'push': 'git_push'}.get(subcommand, 'git')


def classify_command(command):
    kinds = []
    for words in command_segments(command):
        executable = Path(words[0]).name
        args = words[1:]
        if executable in ("pytest", "unittest", "jest", "vitest", "mocha", "rspec", "bats"):
            kind = "test"
        elif executable.startswith("python") and "-m" in args and any(a in ("pytest", "unittest") for a in args):
            kind = "test"
        elif executable in ("npm", "pnpm", "yarn", "bun", "cargo", "go", "dotnet", "mvn", "gradle", "make") and any(a == "test" or a.startswith("test:") for a in args):
            kind = "test"
        elif executable in ("make", "cmake", "ninja", "tsc", "webpack", "vite") or executable in ("npm", "pnpm", "yarn", "bun", "cargo", "go", "dotnet", "mvn", "gradle") and any(a in ("build", "compile", "package", "install") or a.startswith("build:") for a in args):
            kind = "build"
        elif executable == "git":
            kind = git_command_kind(args)
        elif executable in ("rg", "grep", "egrep", "fgrep", "find", "fd"):
            kind = "search"
        elif executable in ("cat", "head", "tail", "sed", "awk", "less", "more", "ls", "tree", "wc", "stat", "file"):
            kind = "read"
        else:
            kind = "bash"
        kinds.append(kind)
    priority = {"bash": 0, "read": 1, "search": 2, "git_status": 3,
                "git_history": 4, "git": 5, "build": 6, "test": 7,
                "git_commit": 8, "git_push": 9}
    return max(kinds, key=priority.get) if kinds else "bash"


def response_info(response):
    """Normalize Codex shell strings, structured results, and MCP content."""
    if isinstance(response, dict):
        code = response.get("exitCode", response.get("exit_code"))
        output = response.get("stdout", response.get("output", ""))
        stderr = response.get("stderr", "")
        content = response.get("content", [])
        if not output and isinstance(content, list):
            output = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
        failed = response.get("isError") is True or response.get("is_error") is True or response.get("success") is False or bool(response.get("error"))
        if response.get("error"):
            stderr = str(response["error"])
        running = response.get("session_id") is not None and code is None
    else:
        code, output, stderr, failed, running = None, str(response or ""), "", False, False
    output, stderr = str(output or ""), str(stderr or "")
    if code is None:
        match = re.search(r"^(?:Process exited with code|Exit code:)\s*(-?\d+)\s*$", output, re.M)
        code = int(match.group(1)) if match else None
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    running = running and code is None or code is None and bool(re.search(r"^Process running with session ID", output, re.M))
    return {"exitCode": code, "stdout": output, "stderr": stderr}, failed, running


def tool_kind(tool):
    name = tool.split("__")[-1].lower()
    if name in {t.lower() for t in QUIET_TOOLS}:
        return None
    if name in ("update_plan", "plan", "request_user_input", "request_user_input_async"):
        return "plan"
    if name in ("apply_patch", "edit", "write") or name.startswith(("write_", "edit_", "update_file", "create_file")):
        return "edit"
    if name in ("grep", "glob", "search", "websearch") or "search" in name or name.startswith(("find_", "query_")):
        return "search"
    if name in ("read", "list", "get", "view_image") or name.startswith(("read_", "list_", "get_", "view_", "open_")):
        return "read"
    return "tool"


def normalize(data):
    """Return (sound event, narrator payload), or None for unhandled events."""
    if not isinstance(data, dict):
        return None
    data = copy.deepcopy(data)
    data["soundbar_agent"] = "codex"
    event = data.get("hook_event_name")
    if event in EVENTS:
        # Compaction has its own sound, so don't also play session_start.
        if event == "SessionStart" and data.get("source") == "compact":
            return None
        data["soundbar_event"] = EVENTS[event]
        return EVENTS[event], data

    tool = str(data.get("tool_name") or "")
    inp = data.get("tool_input")
    if not isinstance(inp, dict):
        inp = {"command": inp if isinstance(inp, str) else ""}
    data["tool_input"] = inp
    if event == "PreToolUse" and tool not in SHELL_TOOLS:
        kind = tool_kind(tool) if tool else None
        if kind is None:
            return None
        if tool == "apply_patch":
            patch = str(inp.get("command") or inp.get("patch") or "")
            paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch, re.M)
            data["tool_name"] = "Edit"
            data["tool_input"] = {"file_path": ", ".join(paths), "new_string": patch[:600]}
        data["soundbar_event"] = kind
        return kind, data

    if event != "PostToolUse" or tool in QUIET_TOOLS:
        return None
    response, failed, running = response_info(data.get("tool_response"))
    data["tool_response"] = response
    if running:
        return None
    code = response["exitCode"]
    if tool not in SHELL_TOOLS:
        if not failed and code in (None, 0):
            return None
        data["soundbar_event"] = "error"
        data["error_message"] = f"{tool} failed: {response['stderr'] or response['stdout']}"
        return "error", data
    data["tool_name"] = "Bash"
    command = inp.get("command", inp.get("cmd", ""))
    if isinstance(command, list):
        command = shlex.join(command)
    inp["command"] = command
    kind = classify_command(command)
    segments = command_segments(command)
    # grep/rg exit 1 means no matches, including `cd repo && rg ... | head`.
    no_matches = code == 1 and kind == "search" and bool(segments) and all(
        Path(words[0]).name in ("rg", "grep", "egrep", "fgrep", "head", "tail", "wc", "sort", "uniq")
        for words in segments
    )
    if failed or code not in (None, 0) and not no_matches:
        data["soundbar_event"] = "error"
        data["error_message"] = f"Command exited with {code}: {command}. {response['stderr'] or response['stdout']}"
        return "error", data
    if no_matches:
        data["soundbar_no_matches"] = True
    if code is None and kind in ('git_commit', 'git_push'):
        kind = 'git'  # No explicit success status: avoid claiming a mutation succeeded.
    data["soundbar_event"] = kind
    return kind, data


def dispatch(data, soundbar):
    result = normalize(data)
    if result is None:
        return
    event, payload = result
    if event == "permission":
        # Manual audio preference only; never change Codex's approval flow.
        # Missing settings in existing installs keep approval cues enabled.
        try:
            config = json.loads((soundbar / "configs/config.json").read_text())
        except (OSError, ValueError):
            config = {}
        if isinstance(config, dict) and config.get("codex_permission_sound_on") is False:
            return
    # Close captured output: background playback must not hold Codex's pipes
    # open or send narration/debug output back into the agent conversation.
    subprocess.run(
        ["/bin/bash", str(soundbar / "engine/play.sh"), event],
        input=json.dumps(payload), text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4,
    )


def hook_definitions(adapter):
    path = shlex.quote(str(adapter))
    command = f"if [ -f {path} ]; then {shlex.quote(sys.executable)} {path} || true; fi"
    hooks = {}
    for event in [*EVENTS, "PreToolUse", "PostToolUse"]:
        group = {"hooks": [{"type": "command", "command": command, "timeout": 3 if event == "Interrupt" else 5}]}
        if event == "SessionStart":
            group["matcher"] = "^(startup|resume|clear)$"
        hooks[event] = [group]
    return hooks


def main():
    try:
        dispatch(json.load(sys.stdin), ROOT)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        # Sound feedback must never break a tool call or block approval.
        pass


if __name__ == "__main__":
    main()
