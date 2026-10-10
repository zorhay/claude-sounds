"""Codex adapter tests; runnable with the standard library or pytest."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import os
import shutil
import sys
import tempfile
import time
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

    def test_permission_cues_default_on_for_existing_or_invalid_config(self):
        configs = [None, '{}', '{"codex_permission_sound_on": true}', 'broken',
                   '[]', 'null', '{"codex_permission_sound_on": "false"}']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for config in configs:
                with self.subTest(config=config):
                    if config is not None:
                        (root / 'config.json').write_text(config)
                    with patch.object(codex.subprocess, 'run') as run:
                        codex.dispatch({'hook_event_name': 'PermissionRequest'}, root)
                    run.assert_called_once()
                    self.assertEqual(run.call_args.args[0][-1], 'permission')

    def test_permission_mute_is_live_and_keeps_other_feedback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'config.json'
            permission = {'hook_event_name': 'PermissionRequest', 'session_id': 'one'}
            with patch.object(codex.subprocess, 'run') as run:
                config.write_text('{"codex_permission_sound_on": true}')
                codex.dispatch(permission, root)
                self.assertEqual(run.call_count, 1)
                config.write_text('{"codex_permission_sound_on": false}')
                for session in ('one', 'two'):
                    codex.dispatch({**permission, 'session_id': session}, root)
                self.assertEqual(run.call_count, 1)
                codex.dispatch({'hook_event_name': 'Stop'}, root)
                codex.dispatch({'hook_event_name': 'PreToolUse', 'tool_name': 'read_file'}, root)
                codex.dispatch({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                    'tool_input': {'command': 'pytest'}, 'tool_response': {'exit_code': 1}}, root)
                self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                                 ['permission', 'stop', 'read', 'error'])
                config.write_text('{"codex_permission_sound_on": true}')
                codex.dispatch(permission, root)
                self.assertEqual(run.call_count, 5)

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

    def test_all_codex_hook_events_are_registered(self):
        hooks = codex.hook_definitions(Path('/tmp/soundbar/codex.py'))
        self.assertEqual(set(hooks), {
            'SessionStart', 'SessionEnd', 'PreCompact', 'PostCompact',
            'Interrupt', 'UserPromptSubmit', 'PermissionRequest', 'Stop',
            'SubagentStart', 'SubagentStop', 'PreToolUse', 'PostToolUse',
        })
        self.assertNotIn('matcher', hooks['PreToolUse'][0])
        self.assertNotIn('matcher', hooks['PostToolUse'][0])
        self.assertLessEqual(hooks['Interrupt'][0]['hooks'][0]['timeout'], 3)

    def test_codex_command_styles(self):
        cases = {
            'cd repo && rg -n "hello world" src | head -20': 'search',
            'env TERM=xterm bash -lc "cd repo; rg needle src"': 'search',
            'bash --norc -lc "rg needle src"': 'search',
            'FOO=bar /usr/bin/grep needle file': 'search',
            'cd repo\npython3 -m unittest tests.test_codex': 'test',
            'uv run --project repo pytest -q': 'test',
            'pnpm run test:unit': 'test',
            'cargo test --workspace': 'test',
            'go test ./...': 'test',
            'npm run build && echo done': 'build',
            'cmake --build build': 'build',
            'git -C repo diff --stat': 'git',
            'sed -n "1,40p" src/app.py': 'read',
            'echo "pytest and git are words, not commands"': 'bash',
            'python3 script.py': 'bash',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(codex.classify_command(command), expected)

    def test_search_no_matches_and_real_failures(self):
        for command, code, expected in [
            ('cd repo && rg needle src', 1, 'search'),
            ('grep needle file | head', 1, 'search'),
            ('rg needle src', 2, 'error'),
            ('find missing', 1, 'error'),
            ('pytest -q', 1, 'error'),
        ]:
            data = {'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                    'tool_input': {'command': command}, 'tool_response': {'exit_code': code}}
            result = codex.normalize(data)
            self.assertEqual(result[0], expected)
            self.assertEqual(bool(result[1].get('soundbar_no_matches')), expected == 'search')

    def test_mcp_and_patch_failures(self):
        for tool, response in [
            ('mcp__docs__read_page', {'isError': True, 'content': [{'type': 'text', 'text': 'Denied'}]}),
            ('apply_patch', {'success': False, 'error': 'Invalid patch'}),
            ('read_file', {'error': {'message': 'Missing file'}}),
        ]:
            result = codex.normalize({'hook_event_name': 'PostToolUse', 'tool_name': tool,
                                      'tool_input': {}, 'tool_response': response})
            self.assertEqual(result[0], 'error')
            self.assertIn(tool, result[1]['error_message'])
        self.assertIsNone(codex.normalize({'hook_event_name': 'PostToolUse', 'tool_name': 'apply_patch',
                                          'tool_response': {'success': True}}))

    def test_tool_categories_and_coordination_silence(self):
        for tool, expected in [('Read', 'read'), ('mcp__docs__read_page', 'read'),
                               ('mcp__docs__search', 'search'), ('update_plan', 'plan'),
                               ('mcp__fs__write_file', 'edit'), ('mcp__browser__click', 'tool')]:
            self.assertEqual(codex.normalize({'hook_event_name': 'PreToolUse', 'tool_name': tool})[0], expected)
        for tool in ('write_stdin', 'spawn_agent', 'Agent', 'wait_threads'):
            self.assertIsNone(codex.normalize({'hook_event_name': 'PreToolUse', 'tool_name': tool}))

    def test_background_shell_is_not_reported_as_complete(self):
        for response in ({'session_id': 123, 'output': 'still running'},
                         'Process running with session ID 123\nworking'):
            self.assertIsNone(codex.normalize({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                                              'tool_input': {'command': 'pytest'}, 'tool_response': response}))
        result = codex.normalize({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash',
                                  'tool_input': {'command': ['bash', '-lc', 'cd repo && pytest -q']},
                                  'tool_response': {'session_id': 123, 'exit_code': 0}})
        self.assertEqual(result[0], 'test')


class CodexPlaybackTests(unittest.TestCase):
    """Simulate real hook JSON through the adapter and shell dispatcher."""

    def test_permission_mute_skips_codex_audio_but_shared_engine_still_plays(self):
        source = Path(SPEC.origin).parent
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            soundbar = root / 'soundbar'
            soundbar.mkdir()
            for name in ('play.sh', 'sounds.json', 'config.defaults.json'):
                shutil.copy2(source / name, soundbar / name)
            (soundbar / 'config.json').write_text(json.dumps({
                'codex_permission_sound_on': False, 'effects_on': True,
                'effects_profile': 'default', 'voice_on': True, 'voice_profile': 'generals',
            }))
            log = root / 'audio.log'
            player = root / 'afplay'
            player.write_text(f'#!{sys.executable}\nimport os,sys\nwith open(os.environ["SOUNDBAR_TEST_LOG"],"a") as f: f.write(sys.argv[-1]+"\\n")\n')
            player.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       SOUNDBAR_TEST_LOG=str(log))
            runner = 'import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import codex; codex.dispatch(json.load(sys.stdin),Path(sys.argv[2]))'
            result = subprocess.run([sys.executable, '-c', runner, str(source), str(soundbar)],
                input='{"hook_event_name":"PermissionRequest"}', env=env,
                text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')
            self.assertFalse(log.exists())
            # Claude Code hooks call the engine directly. It ignores this
            # adapter preference; the panel's previews also bypass the adapter.
            for overrides in ({}, {'FORCE_LAYER': 'voice', 'FORCE_VOICE_PROFILE': 'generals'}):
                result = subprocess.run(['/bin/bash', str(soundbar / 'play.sh'), 'permission'],
                    input='{"hook_event_name":"PermissionRequest"}', env={**env, **overrides},
                    text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 3)

    def test_new_senior_events_fall_back_without_overwriting_user_phrases(self):
        source = Path(SPEC.origin).parent
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            soundbar = root / 'soundbar'
            soundbar.mkdir()
            for name in ('play.sh', 'sounds.json', 'config.defaults.json', 'phrases.defaults.json'):
                shutil.copy2(source / name, soundbar / name)
            player = root / 'afplay'
            player.write_text('#!/bin/sh\nexit 0\n')
            player.chmod(0o755)
            say = root / 'say'
            say.write_text(f'#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\nPath(os.environ["SOUNDBAR_TEST_LOG"]).write_text(" ".join(sys.argv[1:]))\nPath(sys.argv[sys.argv.index("-o")+1]).touch()\n')
            say.chmod(0o755)
            default_test_phrases = json.loads((soundbar / 'phrases.defaults.json').read_text())['test']
            for index, (phrases, expected) in enumerate([
                ({'bash': ['Custom shell']}, default_test_phrases),
                ({'test': ['My custom checks']}, ['My custom checks']),
                ({'test': []}, None),
            ]):
                user = soundbar / 'phrases.json'
                user.write_text(json.dumps(phrases))
                before = user.read_bytes()
                log = root / f'{index}.txt'
                env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                           FORCE_LAYER='voice', FORCE_VOICE_PROFILE='senior', SOUNDBAR_TEST_LOG=str(log))
                result = subprocess.run(['/bin/bash', str(soundbar / 'play.sh'), 'test'], input='{}',
                                        text=True, capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(user.read_bytes(), before)
                if expected:
                    spoken = log.read_text()
                    self.assertTrue(any(f' {phrase} -o ' in spoken for phrase in expected), spoken)
                else:
                    self.assertFalse(log.exists())

    def test_generals_codex_events_select_the_correct_audio(self):
        source = Path(SPEC.origin).parent
        cases = [
            ({'hook_event_name': 'SessionEnd'}, ['command_center_offline.aiff']),
            ({'hook_event_name': 'PreCompact'}, ['consolidating_intel.aiff']),
            ({'hook_event_name': 'Interrupt'}, ['hold_position.aiff']),
            ({'hook_event_name': 'UserPromptSubmit', 'prompt': 'Fix tests'}, ['orders_received.aiff']),
            ({'hook_event_name': 'PreToolUse', 'tool_name': 'read_file'}, ['gathering_intel.aiff']),
            ({'hook_event_name': 'PreToolUse', 'tool_name': 'update_plan'}, ['battle_plan.aiff']),
            ({'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__browser__click'}, ['equipment_ready.aiff']),
            ({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'cmd': 'cd repo && uv run pytest -q'}, 'tool_response': {'exit_code': 0}}, ['checks_complete.aiff']),
            ({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'npm run build'}, 'tool_response': {'exit_code': 0}}, ['build_complete.aiff']),
            ({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'git status'}, 'tool_response': {'exit_code': 0}}, ['repository_updated.aiff']),
            ({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'tool_input': {'command': 'cd repo && rg needle .'}, 'tool_response': {'exit_code': 1}}, ['scanning_area.aiff']),
            ({'hook_event_name': 'PostToolUse', 'tool_name': 'apply_patch', 'tool_response': {'success': False}}, ['unit_lost.aiff']),
            ({'hook_event_name': 'SubagentStart'}, None),
            ({'hook_event_name': 'Stop'}, ['construction_complete.aiff']),
            ({'hook_event_name': 'SessionStart', 'source': 'compact'}, []),
            ({'hook_event_name': 'PreToolUse', 'tool_name': 'write_stdin'}, []),
        ]
        with tempfile.TemporaryDirectory(prefix='codex audio test ') as temp:
            root = Path(temp)
            soundbar = root / 'soundbar'
            soundbar.mkdir()
            for name in ('play.sh', 'sounds.json', 'config.defaults.json', 'phrases.defaults.json'):
                shutil.copy2(source / name, soundbar / name)
            player = root / 'afplay'
            player.write_text(f'#!{sys.executable}\nimport json, os, sys\nwith open(os.environ["SOUNDBAR_TEST_LOG"], "a") as f:\n f.write(json.dumps(sys.argv[1:])+"\\n")\n')
            player.chmod(0o755)
            runner = 'import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import codex; codex.dispatch(json.load(sys.stdin),Path(sys.argv[2]))'
            for index, (payload, expected) in enumerate(cases):
                with self.subTest(event=payload):
                    log = root / f'{index}.log'
                    env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                               FORCE_LAYER='voice', FORCE_VOICE_PROFILE='generals', SOUNDBAR_TEST_LOG=str(log))
                    result = subprocess.run([sys.executable, '-c', runner, str(source), str(soundbar)],
                                            input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(result.stderr, '')
                    count = 2 if expected is None else len(expected)
                    deadline = time.monotonic() + (3 if count else .15)
                    names = []
                    while time.monotonic() < deadline:
                        if log.exists():
                            try:
                                names = [Path(json.loads(line)[-1]).name for line in log.read_text().splitlines()]
                            except json.JSONDecodeError:
                                continue
                        if count and len(names) >= count:
                            break
                        time.sleep(.025)
                    if expected is None:
                        variants = json.loads((source / 'sounds.json').read_text())['voice']['generals']['events']['subagent_start']['sequence']
                        self.assertIn(names, variants)
                    else:
                        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
