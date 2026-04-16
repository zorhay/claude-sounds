"""Server API tests.

Tests server.py functions without starting the HTTP server.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

if str(SOUNDBAR_DIR) not in sys.path:
    sys.path.insert(0, str(SOUNDBAR_DIR))


class TestServerImport:
    """server.py must import without side effects."""

    def test_import_server(self):
        import server
        assert hasattr(server, "get_status")
        assert hasattr(server, "CONFIG_KEYS")


class TestVolStr:
    """_vol_str converts integer 0-100 to afplay volume string."""

    def test_zero(self):
        from server import _vol_str
        assert _vol_str(0) == "0.00"

    def test_fifty(self):
        from server import _vol_str
        assert _vol_str(50) == "0.50"

    def test_hundred(self):
        from server import _vol_str
        assert _vol_str(100) == "1.00"

    def test_seventy_five(self):
        from server import _vol_str
        assert _vol_str(75) == "0.75"

    def test_one(self):
        from server import _vol_str
        assert _vol_str(1) == "0.01"

    def test_ninety_nine(self):
        from server import _vol_str
        assert _vol_str(99) == "0.99"


class TestSpecLabel:
    """_spec_label derives display labels from sound specs."""

    def test_sox_spec(self):
        from server import _spec_label
        spec = {"sox": "synth 0.3 sine 300 fade 0.05 0.3 0.2 reverb 40"}
        label = _spec_label(spec)
        assert "synth" in label
        assert "sine" in label

    def test_file_spec(self):
        from server import _spec_label
        spec = {"file": "sounds/paper/bell.mp3"}
        label = _spec_label(spec)
        assert label == "bell.mp3"

    def test_file_spec_basename(self):
        from server import _spec_label
        spec = {"file": "/System/Library/Sounds/Glass.aiff"}
        label = _spec_label(spec)
        assert label == "Glass.aiff"

    def test_files_spec(self):
        from server import _spec_label
        spec = {"files": ["pencil1.mp3", "pencil2.mp3"]}
        label = _spec_label(spec)
        assert label == "pencil1.mp3"

    def test_sequence_spec(self):
        from server import _spec_label
        spec = {"sequence": [["book_open.mp3", "page_flip.mp3"]]}
        label = _spec_label(spec)
        assert "book_open.mp3" in label
        assert "page_flip.mp3" in label

    def test_empty_spec(self):
        from server import _spec_label
        assert _spec_label({}) == "..."


class TestSpecOrigin:
    """_spec_origin classifies sound source type."""

    def test_sox_is_generated(self):
        from server import _spec_origin
        assert _spec_origin({"sox": "synth 0.1 sine 440"}) == "generated"

    def test_system_file(self):
        from server import _spec_origin
        assert _spec_origin({"file": "/System/Library/Sounds/Glass.aiff"}) == "system"

    def test_mp3_is_sampled(self):
        from server import _spec_origin
        assert _spec_origin({"file": "bell.mp3"}) == "sampled"

    def test_aiff_is_recorded(self):
        from server import _spec_origin
        assert _spec_origin({"file": "command_center.aiff"}) == "recorded"

    def test_files_mp3(self):
        from server import _spec_origin
        assert _spec_origin({"files": ["a.mp3", "b.mp3"]}) == "sampled"

    def test_sequence_mp3(self):
        from server import _spec_origin
        assert _spec_origin({"sequence": [["a.mp3", "b.mp3"]]}) == "sampled"


class TestParseProfiles:
    """Profile parsing from manifest."""

    def test_parse_effects_profiles_returns_all(self, sounds_json):
        from server import parse_effects_profiles
        profiles = parse_effects_profiles()
        manifest_names = set(sounds_json["effects"].keys())
        assert set(profiles.keys()) == manifest_names

    def test_parse_voice_profiles_includes_expected(self):
        from server import parse_voice_profiles
        profiles = parse_voice_profiles()
        # Must include senior (TTS), narrator (LLM), and generals (manifest)
        assert "senior" in profiles
        assert "narrator" in profiles
        assert "generals" in profiles

    def test_effects_profile_events_have_cmd_and_origin(self):
        from server import parse_effects_profiles
        profiles = parse_effects_profiles()
        for prof_name, events in profiles.items():
            for event, info in events.items():
                assert "cmd" in info, f"{prof_name}/{event} missing 'cmd'"
                assert "origin" in info, f"{prof_name}/{event} missing 'origin'"


class TestGetStatus:
    """get_status() returns complete state for the UI."""

    def test_returns_required_keys(self):
        from server import get_status
        status = get_status()
        required = {
            "effects_profiles", "voice_profiles", "voice_profile_names",
            "phrases", "events", "dialogue_events", "voices",
            "tts_engines", "kokoro_voices",
            "narrator_providers", "narrator_styles", "narrator_styles_defaults",
        }
        missing = required - set(status.keys())
        assert not missing, f"get_status() missing keys: {missing}"

    def test_narrator_styles_shape(self):
        from server import get_status
        status = get_status()
        styles = status["narrator_styles"]
        assert isinstance(styles, dict) and styles
        for name, entry in styles.items():
            assert isinstance(entry, dict), f"style '{name}' not a dict"
            assert "label" in entry and "prompt" in entry, (
                f"style '{name}' missing label/prompt"
            )

    def test_events_list_has_10_events(self):
        from server import get_status
        status = get_status()
        assert len(status["events"]) == 10


class TestGetPython3:
    """get_python3() resolves a valid executable."""

    def test_returns_executable_path(self):
        from server import get_python3
        p = get_python3()
        assert os.path.isfile(p), f"get_python3() returned non-file: {p}"
        assert os.access(p, os.X_OK), f"get_python3() returned non-executable: {p}"
