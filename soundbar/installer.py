#!/usr/bin/env python3
"""Shared install/uninstall flow for Code Gossip's local agent integrations."""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

import codex
import cursor


AGENTS = ("codex", "cursor", "claude")
USER_FILES = ("config.json", "phrases.json", "narrator_styles.json")
TAGS = {
    "codex": ("soundbar/codex.py",),
    "cursor": ("soundbar/cursor.py",),
    "claude": ("soundbar/play.sh", "play-sound.sh"),
}


def targets(home):
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser().absolute()
    return {"codex": codex_home / "hooks.json",
            "cursor": home / ".cursor/hooks.json",
            "claude": home / ".claude/settings.json"}


def detected_agents(home, paths):
    commands = {"codex": ("codex",), "cursor": ("cursor", "cursor-agent"), "claude": ("claude",)}
    apps = {"codex": "Codex.app", "cursor": "Cursor.app", "claude": "Claude.app"}
    def has_config(agent):
        directory = paths[agent].parent
        if not directory.is_dir():
            return False
        # Every integration creates ~/.claude/soundbar. That alone is not
        # evidence of Claude Code, including after a config-preserving uninstall.
        if agent == "claude" and (directory / "soundbar").exists():
            return any(child.name != "soundbar" for child in directory.iterdir())
        return True

    return [agent for agent in AGENTS if has_config(agent)
            or any(shutil.which(cmd) for cmd in commands[agent])
            or any((root / apps[agent]).is_dir()
                   for root in (Path("/Applications"), home / "Applications"))]


def read_document(path):
    document = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(document, dict) or not isinstance(document.get("hooks", {}), dict):
        raise ValueError(f"{path}: expected a JSON object with a hooks object")
    for groups in document.get("hooks", {}).values():
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise ValueError(f"{path}: hook events must contain arrays of objects")
        for group in groups:
            if "hooks" in group and (not isinstance(group["hooks"], list)
                    or any(not isinstance(h, dict) for h in group["hooks"])):
                raise ValueError(f"{path}: invalid hook handlers")
    return document


def owned(handler, agent):
    command = handler.get("command", "")
    return isinstance(command, str) and any(tag in command for tag in TAGS[agent])


def merge_hooks(document, agent, new_hooks):
    """Remove only our commands, preserving unrelated handlers in shared groups."""
    updated = copy.deepcopy(document)
    merged = {}
    for event, groups in updated.get("hooks", {}).items():
        kept = []
        for group in groups:
            if agent == "cursor":
                if not owned(group, agent):
                    kept.append(group)
            else:
                handlers = group.get("hooks", [])
                remaining = [handler for handler in handlers if not owned(handler, agent)]
                if remaining or not handlers:
                    kept.append({**group, "hooks": remaining} if handlers else group)
        if kept:
            merged[event] = kept
    for event, groups in new_hooks.items():
        merged.setdefault(event, []).extend(groups)
    if merged:
        updated["hooks"] = merged
    else:
        updated.pop("hooks", None)
    if agent == "cursor" and new_hooks:
        updated.setdefault("version", 1)
    return updated


def is_connected(document, agent):
    return any(owned(handler, agent)
               for groups in document.get("hooks", {}).values()
               for group in groups
               for handler in ([group] if agent == "cursor" else group.get("hooks", [])))


def select_agents(selection, available):
    tokens = re.split(r"[\s,]+", selection.strip().lower())
    aliases = {"1": "all", "2": "codex", "3": "cursor", "4": "claude"}
    tokens = [aliases.get(token, token) for token in tokens]
    if not tokens or any(token not in (*AGENTS, "all") for token in tokens):
        raise ValueError("Select all, codex, cursor, or claude (names or numbers, separated by spaces or commas).")
    chosen = set(available if "all" in tokens else ()) | (set(tokens) - {"all"})
    return [agent for agent in AGENTS if agent in chosen]


def choose(args, available):
    if args.all:
        return list(AGENTS) if args.action == "uninstall" else list(available)
    if args.agents:
        return select_agents(" ".join(args.agents), AGENTS if args.action == "uninstall" else available)
    print(f"Detected {'integrations' if args.action == 'uninstall' else 'agents'}: {', '.join(available) or 'none'}")
    print("  1) all\n  2) codex\n  3) cursor\n  4) claude")
    print("Choose one or more (e.g. 2,4 or codex claude). Explicit names also work if detection missed an agent.")
    while True:
        try:
            selection = input(f"Agents to {args.action}: ")
        except EOFError:
            raise ValueError("No selection received. Use --agents codex,cursor,claude or --all for noninteractive use.") from None
        try:
            return select_agents(selection, AGENTS if args.action == "uninstall" else available)
        except ValueError as exc:
            print(exc)


def definitions(agent, source, dest):
    if agent == "codex":
        return codex.hook_definitions(dest / "codex.py")
    if agent == "cursor":
        return cursor.hook_definitions(dest / "cursor.py")
    hooks = json.loads((source / "claude-hooks.json").read_text())
    for groups in hooks.values():
        for group in groups:
            for handler in group["hooks"]:
                event = handler["command"].rsplit(" ", 1)[1]
                handler["command"] = f"/bin/bash {shlex.quote(str(dest / 'play.sh'))} {event}"
    return hooks


def write_hooks(path, original, updated):
    if original == updated:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".soundbar-backup"))
    fd, temporary = tempfile.mkstemp(prefix=".soundbar-hooks-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(updated, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_files(source, dest, dev):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dev:
        if dest.is_symlink():
            dest.unlink()
        dest.symlink_to(source, target_is_directory=True)
    else:
        # Never copy a developer's private settings, environments, or runtime state.
        ignored = shutil.ignore_patterns(*USER_FILES, ".venv", "__pycache__", ".pytest_cache",
            ".DS_Store", "*.log", "*.sock", ".server.pid", ".manifest", ".soundbar-window",
            "integrations.json", "session_context", "*.soundbar-backup")
        shutil.copytree(source, dest, dirs_exist_ok=True, ignore=ignored)
    for name in USER_FILES:
        target = source.parent / name if dev else dest / name
        if not target.exists():
            existing = source / name
            defaults = source / name.replace(".json", ".defaults.json")
            shutil.copy2(existing if dev and existing.is_file() else defaults, target)
        if dev:
            link = source / name
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(Path("..") / name)
    config_path = source.parent / "config.json" if dev else dest / "config.json"
    config = json.loads(config_path.read_text())
    config["python3_path"] = sys.executable
    if config.get("voice_profile") == "narration":
        config["voice_profile"] = "senior"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    for name in ("play.sh", "switch.sh", "panel.sh", "uninstall.sh"):
        script = dest / name
        script.chmod(script.stat().st_mode | 0o111)
    if shutil.which("say"):
        subprocess.run(["/bin/bash", str(dest / "sounds/generals/generate.sh")], check=True)
    else:
        print("Skipping Generals voice generation (macOS say is unavailable).")


def stop_process(pid, script):
    """A stale PID must never terminate an unrelated process that reused it."""
    if pid <= 1:
        return
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True)
    command = result.stdout.strip()
    if not any(command.endswith(" " + str(path)) for path in (script, script.resolve())):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def stop_processes(dest):
    pid_file = dest / ".server.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid = 0
        stop_process(pid, dest / "server.py")
        pid_file.unlink()
    sock = dest / "kokoro.sock"
    if sock.exists() and shutil.which("lsof"):
        result = subprocess.run(["lsof", "-t", str(sock)], capture_output=True, text=True)
        for value in result.stdout.split():
            pid = int(value)
            stop_process(pid, dest / "kokoro_server.py")
        sock.unlink(missing_ok=True)


def remove_files(dest, purge):
    stop_processes(dest)
    if dest.is_symlink():
        dest.unlink()  # Never remove a development checkout.
    elif dest.exists():
        for child in dest.iterdir():
            if not purge and child.name in USER_FILES:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        if not any(dest.iterdir()):
            dest.rmdir()


def run(args):
    home = Path.home()
    source = Path(__file__).resolve().parent
    dest = home / ".claude/soundbar"
    paths = targets(home)
    uninstall = args.action == "uninstall"
    # Uninstall must inspect every integration before deciding whether the shared
    # runtime is unused. Install only needs to validate the selected hook files.
    documents = {agent: read_document(path) for agent, path in paths.items()} if uninstall else {}
    installed = [agent for agent in documents if is_connected(documents[agent], agent)]
    available = installed if uninstall else detected_agents(home, paths)
    selected = choose(args, available)
    if not selected:
        raise ValueError("No agents detected or selected. Specify --agents codex, cursor, or claude.")
    if not uninstall:
        documents = {agent: read_document(paths[agent]) for agent in selected}
        if not shutil.which("jq"):
            raise ValueError("jq is required for sound playback. Install jq and try again.")
        if args.dev and dest.exists() and not dest.is_symlink():
            raise ValueError(f"{dest} is a regular installation; uninstall it before using --dev.")
        if not args.dev and dest.is_symlink():
            raise ValueError(f"{dest} is a dev symlink; use --dev or uninstall it first.")
        config_path = source.parent / "config.json" if args.dev else dest / "config.json"
        if config_path.exists() and not isinstance(json.loads(config_path.read_text()), dict):
            raise ValueError(f"{config_path}: expected a JSON object")
    updated = {agent: merge_hooks(documents[agent], agent,
               {} if uninstall else definitions(agent, source, dest)) for agent in selected}
    remaining = [agent for agent in installed if agent not in selected]
    print(f"{'Preview: would ' if args.dry_run else ''}{args.action.capitalize()} for {', '.join(selected)}")
    for agent in selected:
        print(f"  {'Remove' if uninstall else 'Merge'} Code Gossip hooks: {paths[agent]}")
    if uninstall and remaining:
        print(f"Keep shared soundbar files and processes for: {', '.join(remaining)}")
    elif uninstall:
        print(f"Remove shared installation: {dest}")
        if not args.purge:
            print("Keep config.json, phrases.json, and narrator_styles.json (use --purge to remove them).")
    else:
        print(f"{'Symlink' if args.dev else 'Copy'} shared soundbar files: {dest}")
    if args.dry_run:
        print("No changes made.")
        return
    if not uninstall:
        install_files(source, dest, args.dev)
    for agent in selected:
        write_hooks(paths[agent], documents[agent], updated[agent])
    if uninstall and not remaining:
        remove_files(dest, args.purge)
    print(f"Code Gossip {'uninstalled' if uninstall else 'installed'} for {', '.join(selected)}.")
    if not uninstall:
        print(f"Panel: {dest / 'panel.sh'}\nUninstall: {dest / 'uninstall.sh'}")
        if "codex" in selected:
            print("Open /hooks in Codex CLI to review and trust the commands, then start a new chat.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("install", "uninstall"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--agents", nargs="+", metavar="AGENT", help="agent names separated by spaces or commas; skips the prompt")
    selection.add_argument("--all", action="store_true", help="skip questions; install detected agents or uninstall every integration")
    parser.add_argument("--dry-run", action="store_true", help="preview without changing files or processes")
    parser.add_argument("--dev", action="store_true", help="install using a symlink to this checkout")
    parser.add_argument("--purge", action="store_true", help="delete user settings when the last integration is uninstalled")
    args = parser.parse_args()
    if args.dev and args.action != "install" or args.purge and args.action != "uninstall":
        parser.error("--dev applies only to install; --purge applies only to uninstall")
    try:
        run(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Code Gossip: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nCancelled.\n")


if __name__ == "__main__":
    main()
