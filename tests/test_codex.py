"""Codex adapter tests; runnable with the standard library or pytest."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "soundbar_codex", Path(__file__).resolve().parents[1] / "soundbar" / "codex.py"
)
codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex)


class CodexTests(unittest.TestCase):
    def test_lifecycle_and_compaction(self):
        for name, sound in codex.EVENTS.items():
            self.assertEqual(codex.normalize({"hook_event_name": name})[0], sound)
        self.assertIsNone(codex.normalize({"hook_event_name": "SessionStart", "source": "compact"}))
        self.assertIsNone(codex.normalize({"hook_event_name": "Unknown"}))
        self.assertIsNone(codex.normalize([]))

    def test_patch_narration(self):
        data = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                "session_id": "abc", "tool_input": {"command": "*** Begin Patch\n*** Update File: src/app.py\n+x\n*** End Patch"}}
        event, payload = codex.normalize(data)
        self.assertEqual(event, "edit")
        self.assertEqual(payload["tool_input"]["file_path"], "src/app.py")
        self.assertEqual(payload["tool_name"], "Edit")
        self.assertEqual(payload["session_id"], "abc")
        self.assertEqual(data["tool_name"], "apply_patch")

    def test_commands_search_and_failures(self):
        for response, expected in [({"exit_code": 0, "output": "ok"}, "bash"),
                                   ({"exitCode": 2, "stderr": "failed"}, "error"),
                                   ("Process exited with code 1\nbad", "error"),
                                   ("Exit code: 0\nok", "bash")]:
            data = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                    "tool_input": {"command": "python3 check.py"}, "tool_response": response}
            event, payload = codex.normalize(data)
            self.assertEqual(event, expected)
            self.assertIsInstance(payload["tool_response"], dict)
        data["tool_input"] = {"cmd": "rg needle src"}
        data["tool_name"] = "exec_command"
        self.assertEqual(codex.normalize(data)[0], "search")
        data["hook_event_name"] = "PreToolUse"
        self.assertIsNone(codex.normalize(data))

    def test_playback_receives_event_and_normalized_json(self):
        with patch.object(codex.subprocess, "run") as run:
            codex.dispatch({"hook_event_name": "Stop", "session_id": "abc"}, Path("/tmp/soundbar"))
        self.assertEqual(run.call_args.args[0], ["/bin/bash", "/tmp/soundbar/play.sh", "stop"])
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["session_id"], "abc")
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_install_idempotence_backup_and_surgical_uninstall(self):
        with tempfile.TemporaryDirectory(prefix="soundbar codex ") as temp:
            root = Path(temp)
            soundbar = root / "soundbar"
            soundbar.mkdir()
            (soundbar / "play.sh").touch()
            other = {"type": "command", "command": "echo keep"}
            original = {"description": "keep me", "hooks": {"Stop": [{"hooks": [other]}]}}
            target = root / "hooks.json"
            target.write_text(json.dumps(original))
            with redirect_stdout(io.StringIO()):
                codex.install(root, soundbar, dry_run=True)
                self.assertEqual(json.loads(target.read_text()), original)
                self.assertFalse((soundbar / "codex.py").exists())
                codex.install(root, soundbar)
                once = target.read_text()
                codex.install(root, soundbar)
                self.assertEqual(target.read_text(), once)
                self.assertEqual(json.loads((root / "hooks.json.soundbar-backup").read_text()), original)
                installed = json.loads(once)
                # An unrelated handler in the very same matcher group survives.
                installed["hooks"]["Stop"][-1]["hooks"].append(other)
                target.write_text(json.dumps(installed))
                codex.install(root, soundbar, uninstall=True)
            cleaned = json.loads(target.read_text())
            self.assertEqual(cleaned["description"], "keep me")
            self.assertEqual(cleaned["hooks"]["Stop"], [{"hooks": [other]}, {"hooks": [other]}])

    def test_invalid_json_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "play.sh").touch()
            (root / "hooks.json").write_text("broken")
            with self.assertRaises(ValueError):
                codex.install(root, root)
            self.assertEqual((root / "hooks.json").read_text(), "broken")


if __name__ == "__main__":
    unittest.main()
