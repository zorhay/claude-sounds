"""Verify audible feedback stays sparse under concurrent hook bursts."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'code-gossip'
SPEC = importlib.util.spec_from_file_location('playback_gate', SOURCE / 'engine/playback_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


LAYOUT = {'play.sh': 'engine/play.sh', 'playback_gate.py': 'engine/playback_gate.py', 'sounds.json': 'data/sounds.json', 'phrases.defaults.json': 'configs/phrases.defaults.json', 'config.defaults.json': 'configs/config.defaults.json'}


class PlaybackBudgetTests(unittest.TestCase):
    def admit(self, state, now, **changes):
        args = dict(layer='voice', session='one', profile='generals', event='read',
                    sound={'file': 'read.aiff'}, cooldown=3, now=now)
        args.update(changes)
        return gate.admit(state, **args)

    def test_first_event_is_immediate_and_repeats_do_not_extend_window(self):
        state = {}
        self.assertTrue(self.admit(state, 100))
        self.assertFalse(self.admit(state, 101))
        self.assertFalse(self.admit(state, 102.9))
        self.assertTrue(self.admit(state, 103))

    def test_same_clip_across_categories_shares_cooldown(self):
        state = {}
        self.assertTrue(self.admit(state, 100, event='bash'))
        self.assertFalse(self.admit(state, 102, event='git'))

    def test_important_cues_bypass_routine_budget_but_deduplicate(self):
        state = {}
        self.assertTrue(self.admit(state, 100))
        for index, event in enumerate(('permission', 'error', 'stop', 'interrupt', 'git_commit', 'git_push')):
            now = 100.1 + index * .1
            self.assertTrue(self.admit(state, now, event=event))
            self.assertFalse(self.admit(state, now + .01, event=event))
        self.assertTrue(self.admit(state, 102, event='error'))

    def test_layers_have_independent_budgets_and_sessions_independent_repeats(self):
        state = {}
        self.assertTrue(self.admit(state, 100))
        self.assertTrue(self.admit(state, 100, layer='effects'))
        self.assertFalse(self.admit(state, 100.2, session='two'))
        self.assertTrue(self.admit(state, 102, session='two'))
        self.assertFalse(self.admit(state, 102, session='one'))


class ConcurrentPlaybackTests(unittest.TestCase):
    def setup_soundbar(self, directory):
        root = Path(directory)
        for name in ('playback_gate.py', 'play.sh', 'sounds.json', 'phrases.defaults.json', 'config.defaults.json'):
            target = root / LAYOUT[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / LAYOUT[name], target)
        config = {'effects_on': True, 'effects_profile': 'default', 'voice_on': True,
                  'voice_profile': 'generals', 'effects_cooldown_ms': 10000, 'voice_cooldown_ms': 10000}
        (root / 'configs/config.json').write_text(json.dumps(config))
        return root

    def test_simultaneous_hook_processes_admit_exactly_one_per_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_soundbar(directory)
            processes = [subprocess.Popen([sys.executable, str(root / 'engine/playback_gate.py'),
                         str(root), 'read', 'on', 'default', 'on', 'generals'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for _ in range(12)]
            results = []
            # Release every waiting process before collecting results, so the
            # reservations really race across processes rather than run serially.
            for process in processes:
                process.stdin.write(json.dumps({'session_id': 'burst-test'}))
                process.stdin.close()
                process.stdin = None
            for process in processes:
                out, err = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, err)
                results.append(out.strip())
            self.assertEqual(results.count('on on'), 1, results)
            self.assertEqual(results.count('off off'), 11, results)

    def test_disabled_gate_allows_every_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_soundbar(directory)
            cfg = json.loads((root / 'configs/config.json').read_text())
            cfg['sound_spacing_on'] = False
            (root / 'configs/config.json').write_text(json.dumps(cfg))
            for _ in range(3):
                self.assertEqual(gate.gate(root, 'read', {'effects': (True, 'default'), 'voice': (True, 'generals')},
                                           {'session_id': 'one'}, root / 'state'), {'effects': True, 'voice': True})
            self.assertFalse((root / 'state').exists())

    def test_busy_state_lock_falls_back_without_hanging_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_soundbar(directory)
            name = gate.fingerprint(str(root.resolve()))[:16]
            state = root / 'state/playback'
            state.mkdir(parents=True)
            with (state / 'lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run([sys.executable, str(root / 'engine/playback_gate.py'),
                    str(root), 'permission', 'on', 'default', 'on', 'generals'],
                    input='{}', env=dict(os.environ, TMPDIR=str(root)),
                    text=True, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'on on')
            self.assertFalse((state / 'state.json').exists())

    def test_unmapped_events_do_not_consume_budget_and_corrupt_state_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_soundbar(directory)
            state = root / 'state'
            state.mkdir()
            (state / 'state.json').write_text('broken')
            layers = {'effects': (True, 'silent'), 'voice': (True, 'generals')}
            self.assertEqual(gate.gate(root, 'unknown', layers, {}, state), {'effects': False, 'voice': False})
            self.assertEqual(gate.gate(root, 'read', layers, {}, state), {'effects': False, 'voice': True})
            saved = json.loads((state / 'state.json').read_text())
            self.assertNotIn('session_id', saved)

    def test_real_engine_suppresses_a_burst_and_manual_previews_still_play(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.setup_soundbar(directory)
            log = root / 'audio.log'
            player = root / 'afplay'
            player.write_text(f'#!{sys.executable}\nimport os,sys\nwith open(os.environ["SOUNDBAR_AUDIO_LOG"],"a") as f: f.write(sys.argv[-1]+"\\n")\n')
            player.chmod(0o755)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], SOUNDBAR_AUDIO_LOG=str(log))
            # Direct engine calls await the stub's inherited pipes, so reads are complete.
            for _ in range(4):
                result = subprocess.run(['/bin/bash', str(root / 'engine/play.sh'), 'read'],
                    input=json.dumps({'session_id': 'burst'}), env=env, text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            clips = log.read_text().splitlines()
            self.assertEqual(len(clips), 2, clips)  # one effect plus one voice
            self.assertEqual(sum('gathering_intel.aiff' in clip for clip in clips), 1)
            env.update(FORCE_LAYER='voice', FORCE_VOICE_PROFILE='generals')
            for _ in range(2):
                subprocess.run(['/bin/bash', str(root / 'engine/play.sh'), 'read'], input='{}', env=env,
                               text=True, capture_output=True, check=True, timeout=5)
            self.assertEqual(len(log.read_text().splitlines()), 4)


if __name__ == '__main__':
    unittest.main()
