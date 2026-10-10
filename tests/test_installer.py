"""Exercise the public scripts against isolated homes, without audio or live hooks."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

import cursor
import installer


@pytest.fixture
def sandbox(tmp_path, repo_root):
    home = tmp_path / "home with spaces"
    home.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # A controlled PATH prevents detection of real apps' CLIs and macOS TTS.
    for name, executable in (("python3", sys.executable), ("dirname", shutil.which("dirname")), ("jq", shutil.which("jq"))):
        (binaries / name).symlink_to(executable)
    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex"),
           "PATH": str(binaries), "PYTHONDONTWRITEBYTECODE": "1"}

    def run(script, *args, selection=None, success=True):
        path = Path(script)
        if not path.is_absolute():
            path = repo_root / path
        result = subprocess.run(["/bin/bash", str(path), *args], env=env,
                                input=selection or "", text=True, capture_output=True)
        if success:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0, result.stdout
        return result

    return home, env, run


def document(home, agent):
    path = home / {"claude": ".claude/settings.json", "codex": ".codex/hooks.json", "cursor": ".cursor/hooks.json"}[agent]
    return json.loads(path.read_text()) if path.exists() else {}


def snapshot(home):
    return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_interactive_multi_selection_and_partial_uninstall(sandbox):
    home, _, run = sandbox
    for agent in installer.AGENTS:
        (home / f".{agent}").mkdir()
    result = run("install.sh", selection="2,3\n")
    assert "Detected agents: codex, cursor, claude" in result.stdout
    assert not document(home, "claude")
    assert installer.is_connected(document(home, "codex"), "codex")
    assert document(home, "cursor")["version"] == 1
    dest = home / ".claude/soundbar"
    # Partial uninstall must neither remove files nor stop the shared server.
    (dest / ".server.pid").write_text(str(os.getpid()))
    result = run(dest / "uninstall.sh", "--purge", selection="codex\n")
    assert "Keep shared soundbar files and processes for: cursor" in result.stdout
    assert (dest / ".server.pid").exists()
    (dest / ".server.pid").unlink()
    assert not installer.is_connected(document(home, "codex"), "codex")
    assert (dest / "play.sh").exists()
    run(dest / "uninstall.sh", "--all")
    assert not (dest / "play.sh").exists()
    assert set(p.name for p in dest.iterdir()) == set(installer.USER_FILES)
    run("uninstall.sh", "--all", "--purge")
    assert not dest.exists()


def test_all_install_reinstall_and_uninstall_preserve_unrelated_hooks(sandbox):
    home, _, run = sandbox
    for agent in installer.AGENTS:
        (home / f".{agent}").mkdir()
    run("install.sh", "--all")
    paths = {agent: home / relative for agent, relative in {
        "codex": ".codex/hooks.json", "cursor": ".cursor/hooks.json", "claude": ".claude/settings.json"}.items()}
    for agent, path in paths.items():
        doc = json.loads(path.read_text())
        first = next(iter(doc["hooks"].values()))[0]
        if agent == "cursor":
            next(iter(doc["hooks"].values())).append({"command": "echo keep"})
        else:
            first["hooks"].append({"type": "command", "command": "echo keep"})
        doc["keep"] = True
        path.write_text(json.dumps(doc))
    dest = home / ".claude/soundbar"
    config = dest / "config.json"
    config.write_text('{"voice_profile":"narration", "volume":17}')
    run("install.sh", "--agents", "all")
    assert json.loads(config.read_text())["volume"] == 17
    assert json.loads(config.read_text())["voice_profile"] == "senior"
    before = {agent: path.read_bytes() for agent, path in paths.items()}
    run("install.sh", "--all")
    assert before == {agent: path.read_bytes() for agent, path in paths.items()}
    run(dest / "uninstall.sh", "--all", "--purge")
    assert not dest.exists()
    for agent, path in paths.items():
        doc = json.loads(path.read_text())
        assert doc["keep"] is True
        assert "echo keep" in path.read_text()
        assert not installer.is_connected(doc, agent)


def test_dry_runs_do_not_write_or_stop_anything(sandbox):
    home, _, run = sandbox
    before = snapshot(home)
    run("install.sh", "--agents", "codex,cursor,claude", "--dry-run")
    assert snapshot(home) == before
    run("install.sh", "--agents", "codex")
    before = snapshot(home)
    run("uninstall.sh", "--all", "--purge", "--dry-run")
    assert snapshot(home) == before


def test_custom_codex_home(sandbox):
    home, env, run = sandbox
    custom = home / "custom codex"
    env["CODEX_HOME"] = str(custom)
    run("install.sh", "--agents", "codex")
    assert (custom / "hooks.json").exists()
    assert not (home / ".codex").exists()
    assert not (home / ".claude/settings.json").exists()
    run("uninstall.sh", "--all", "--purge")
    assert not installer.is_connected(json.loads((custom / "hooks.json").read_text()), "codex")


@pytest.mark.parametrize("invalid", ['broken', '[]', '{"hooks":[]}', '{"hooks":{"Stop":[{"hooks":null}]}}'])
def test_invalid_settings_abort_before_any_changes(sandbox, invalid):
    home, _, run = sandbox
    path = home / ".cursor/hooks.json"
    path.parent.mkdir()
    path.write_text(invalid)
    before = snapshot(home)
    run("install.sh", "--agents", "codex,cursor,claude", success=False)
    assert snapshot(home) == before
    run("uninstall.sh", "--all", success=False)
    assert snapshot(home) == before


def test_orphan_hooks_removed_without_shared_files(sandbox):
    home, _, run = sandbox
    path = home / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"command": "old/play-sound.sh"}, {"command": "echo keep"}]}]}}))
    run("uninstall.sh", "--all")
    assert "play-sound.sh" not in path.read_text()
    assert "echo keep" in path.read_text()


def test_missing_selection_and_unknown_flags_fail_without_changes(sandbox):
    home, _, run = sandbox
    before = snapshot(home)
    result = run("install.sh", success=False)
    assert "No selection received" in result.stderr
    run("uninstall.sh", "--al", success=False)
    run("install.sh", "--agents", "typo", success=False)
    assert snapshot(home) == before


def test_dev_uninstall_never_deletes_checkout(sandbox, tmp_path, repo_root):
    home, _, run = sandbox
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy2(repo_root / "install.sh", checkout)
    shutil.copytree(repo_root / "soundbar", checkout / "soundbar",
                    ignore=shutil.ignore_patterns(*installer.USER_FILES, ".venv", "__pycache__",
                                                 "*.sock", "*.log", "session_context"))
    (checkout / "uninstall.sh").symlink_to("soundbar/uninstall.sh")
    run(checkout / "install.sh", "--dev", "--agents", "cursor,claude")
    dest = home / ".claude/soundbar"
    assert dest.is_symlink()
    run(checkout / "install.sh", "--agents", "codex", success=False)
    run(dest / "uninstall.sh", "--all", "--purge")
    assert not dest.is_symlink()
    assert (checkout / "soundbar/play.sh").exists()
    assert (checkout / "config.json").exists()


def test_detection_and_all_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None)
    paths = {agent: tmp_path / agent / "hooks.json" for agent in installer.AGENTS}
    # Hide actual host applications while retaining the test's directories.
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: False if str(path).startswith("/Applications/") else real_is_dir(path))
    assert installer.detected_agents(tmp_path, paths) == ["codex"]
    assert installer.select_agents("all", ["codex"]) == ["codex"]
    paths["cursor"].parent.mkdir()
    (tmp_path / "Applications/Claude.app").mkdir(parents=True)
    assert installer.detected_agents(tmp_path, paths) == list(installer.AGENTS)
    assert installer.select_agents("2,4 codex", []) == ["codex", "claude"]


def test_shared_runtime_does_not_count_as_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    paths = {agent: tmp_path / f".{agent}" / "hooks.json" for agent in installer.AGENTS}
    (tmp_path / ".claude/soundbar").mkdir(parents=True)
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda path: False if str(path).startswith("/Applications/") else real_is_dir(path))
    assert installer.detected_agents(tmp_path, paths) == []
    (tmp_path / ".claude/settings.json").write_text("{}")
    assert installer.detected_agents(tmp_path, paths) == ["claude"]


def test_unselected_invalid_settings_do_not_block_install(sandbox):
    home, _, run = sandbox
    path = home / ".cursor/hooks.json"
    path.parent.mkdir()
    path.write_text("broken")
    run("install.sh", "--agents", "codex")
    assert installer.is_connected(document(home, "codex"), "codex")
    assert path.read_text() == "broken"
    # Removal must still fail safely if another integration cannot be inspected.
    run("uninstall.sh", "--agents", "codex", success=False)
    assert (home / ".claude/soundbar/play.sh").exists()


def test_uninstall_stops_running_panel(sandbox):
    home, env, run = sandbox
    run("install.sh", "--agents", "codex")
    dest = home / ".claude/soundbar"
    panel = subprocess.Popen([sys.executable, str(dest / "server.py")],
                             env={**env, "PORT": "0"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (dest / ".server.pid").exists() and panel.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert (dest / ".server.pid").read_text() == str(panel.pid)
        run("uninstall.sh", "--all", "--purge")
        assert panel.wait(timeout=5) != 0
        assert not dest.exists()
    finally:
        if panel.poll() is None:
            panel.terminate()
        panel.communicate(timeout=5)


@pytest.mark.parametrize("stale", ["invalid", "reused"])
def test_stale_pid_does_not_break_uninstall_or_kill_other_processes(sandbox, stale):
    home, _, run = sandbox
    run("install.sh", "--agents", "codex")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        dest = home / ".claude/soundbar"
        (dest / ".server.pid").write_text(str(unrelated.pid) if stale == "reused" else "not a pid")
        run("uninstall.sh", "--all", "--purge")
        assert unrelated.poll() is None
        assert not dest.exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.parametrize("event,data,expected", [
    ("stop", {"status": "error"}, "error"),
    ("stop", {"status": "aborted"}, "interrupt"),
    ("stop", {"status": "completed"}, "stop"),
    ("afterShellExecution", {"command": "ls", "output": "files"}, "bash"),
    ("afterFileEdit", {"file_path": "test.py"}, "edit"),
])
def test_cursor_event_mapping(event, data, expected):
    sound, payload = cursor.normalize(event, {**data, "conversation_id": "abc"})
    assert sound == expected
    assert payload["soundbar_agent"] == "cursor"
    assert payload["session_id"] == "abc"


def test_cursor_hook_command_executes_without_output(tmp_path):
    soundbar = tmp_path / "path with spaces/soundbar"
    soundbar.mkdir(parents=True)
    shutil.copy2(Path(cursor.__file__), soundbar / "cursor.py")
    (soundbar / "play.sh").write_text('printf "%s" "$1" > "$(dirname "$0")/event"\ncat > "$(dirname "$0")/payload"\necho hidden\n')
    command = cursor.hook_definitions(soundbar / "cursor.py")["stop"][0]["command"]
    result = subprocess.run(command, shell=True, input='{"status":"error"}', text=True, capture_output=True)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    assert (soundbar / "event").read_text() == "error"


@pytest.mark.parametrize("event,data,expected", [
    ("sessionStart", {}, "Cursor session"),
    ("sessionEnd", {}, "Cursor session closed"),
    ("stop", {"status": "completed"}, "Cursor finished this turn"),
    ("stop", {"status": "aborted"}, "interrupted the current Cursor turn"),
    ("postToolUseFailure", {"error_message": "access denied"}, "Cursor encountered a failure: access denied"),
    ("subagentStop", {}, "Sub-agent (agent) returned"),
    ("preCompact", {}, "Cursor is preparing to compact"),
    ("afterShellExecution", {"command": "ls", "output": "files"}, "Cursor ran a shell command: ls"),
])
def test_cursor_narration_uses_normalized_events(event, data, expected):
    from narrate import build_context
    _, payload = cursor.normalize(event, data)
    context = build_context(payload)
    assert expected in context
    assert "Codex" not in context
    assert "exit 0" not in context.lower()  # Cursor does not supply an exit code here.
