"""Configuration tests.

Validates config.defaults.json structure and consistency with server.py.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

if str(SOUNDBAR_DIR) not in sys.path:
    sys.path.insert(0, str(SOUNDBAR_DIR))


class TestConfigDefaults:
    """config.defaults.json validity."""

    def test_valid_json(self):
        text = (SOUNDBAR_DIR / "config.defaults.json").read_text()
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_required_keys_present(self, config_defaults):
        required = {
            "python3_path", "effects_on", "effects_profile", "effects_volume",
            "voice_on", "voice_profile", "voice_volume", "voice_main", "voice_sub",
            "tts_engine", "kokoro_voice",
            "narrator_provider", "narrator_model", "narrator_api_key", "narrator_style",
        }
        missing = required - set(config_defaults.keys())
        assert not missing, f"Missing keys in config.defaults.json: {missing}"

    def test_python3_path_is_valid_format(self, config_defaults):
        p = config_defaults["python3_path"]
        assert p.startswith("/"), f"python3_path should be absolute: {p}"
        assert "python" in p, f"python3_path doesn't look like a python path: {p}"

    def test_voice_profile_default_is_senior(self, config_defaults):
        """Regression: was 'narration', renamed to 'senior'."""
        assert config_defaults["voice_profile"] == "senior", (
            f"voice_profile default is '{config_defaults['voice_profile']}', should be 'senior'"
        )

    def test_volume_values_are_valid(self, config_defaults):
        for key in ("effects_volume", "voice_volume"):
            v = config_defaults[key]
            assert isinstance(v, int), f"{key} should be int, got {type(v)}"
            assert 0 <= v <= 100, f"{key}={v} not in 0-100"

    def test_boolean_fields_are_booleans(self, config_defaults):
        for key in ("effects_on", "voice_on"):
            v = config_defaults[key]
            assert isinstance(v, bool), f"{key} should be bool, got {type(v).__name__}: {v}"

    def test_tts_engine_valid(self, config_defaults):
        assert config_defaults["tts_engine"] in ("say", "kokoro")

    def test_narrator_provider_valid(self, config_defaults):
        import narrate
        assert config_defaults["narrator_provider"] in narrate.PROVIDERS

    def test_narrator_style_valid(self, config_defaults):
        import narrate
        assert config_defaults["narrator_style"] in narrate.read_styles()


class TestConfigKeysSync:
    """server.py CONFIG_KEYS must match config.defaults.json keys."""

    def test_config_keys_match_defaults(self, config_defaults):
        import server
        defaults_keys = set(config_defaults.keys())
        assert server.CONFIG_KEYS == defaults_keys, (
            f"server.py CONFIG_KEYS vs config.defaults.json mismatch:\n"
            f"  Only in CONFIG_KEYS: {server.CONFIG_KEYS - defaults_keys}\n"
            f"  Only in defaults: {defaults_keys - server.CONFIG_KEYS}"
        )

    def test_server_defaults_dict_matches_file(self, config_defaults):
        """server.py DEFAULTS dict should have the same keys as config.defaults.json."""
        import server
        server_keys = set(server.DEFAULTS.keys())
        file_keys = set(config_defaults.keys())
        assert server_keys == file_keys, (
            f"server.py DEFAULTS keys mismatch:\n"
            f"  Only in DEFAULTS: {server_keys - file_keys}\n"
            f"  Only in file: {file_keys - server_keys}"
        )
