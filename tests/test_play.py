"""play.sh validation tests.

Tests shell script syntax, config field extraction, and event consistency.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

ALL_EVENTS = {
    "stop", "edit", "bash", "search", "permission", "error",
    "subagent_start", "subagent_stop", "session_start", "compact",
}


class TestPlayShSyntax:
    """play.sh must have valid bash syntax."""

    def test_bash_syntax_check(self):
        result = subprocess.run(
            ["bash", "-n", str(SOUNDBAR_DIR / "play.sh")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"play.sh has syntax errors:\n{result.stderr}"
        )


class TestPlayShConfig:
    """play.sh reads the right config fields."""

    def test_reads_all_config_fields(self, play_script):
        """play.sh jq call should extract exactly 11 fields."""
        # Find the jq read block: IFS=$'\t' read -r ... < <(jq ...)
        m = re.search(
            r"IFS=\$'\\t' read -r (.+?) < <\(",
            play_script,
        )
        assert m, "Could not find jq config read in play.sh"
        fields = m.group(1).split()
        assert len(fields) == 11, (
            f"Expected 11 config fields, got {len(fields)}: {fields}"
        )

        # Verify the key fields are present
        expected_vars = {
            "EFFECTS_ON", "EFFECTS_PROFILE", "EFFECTS_VOL",
            "VOICE_ON", "VOICE_PROFILE", "VOICE_VOL",
            "VOICE", "SUBVOICE", "TTS_ENGINE", "KOKORO_VOICE", "PYTHON3",
        }
        actual_vars = set(fields)
        assert actual_vars == expected_vars, (
            f"Config field mismatch:\n"
            f"  Missing: {expected_vars - actual_vars}\n"
            f"  Extra: {actual_vars - expected_vars}"
        )

    def test_jq_extracts_effects_profile(self, play_script):
        """jq should read .effects_profile."""
        assert ".effects_profile" in play_script

    def test_jq_extracts_voice_profile(self, play_script):
        """jq should read .voice_profile."""
        assert ".voice_profile" in play_script

    def test_jq_extracts_tts_engine(self, play_script):
        """jq should read .tts_engine."""
        assert ".tts_engine" in play_script


class TestPlayShDefaults:
    """play.sh fallback defaults."""

    def test_default_voice_profile_is_senior(self, play_script):
        """Regression: default fallback should be 'senior', not 'narration'."""
        # The jq fallback: (.voice_profile // "senior")
        assert '"senior"' in play_script
        assert '"narration"' not in play_script

    def test_default_effects_profile_is_default(self, play_script):
        """Default effects profile should be 'default'."""
        assert '(.effects_profile // "default")' in play_script

    def test_fallback_line_uses_senior(self, play_script):
        """The fallback printf after jq failure should use senior."""
        # The fallback: printf 'on\tdefault\t100\toff\tsenior\t...'
        fallback_m = re.search(r"printf\s+'([^']+)'", play_script)
        assert fallback_m, "Could not find fallback printf in play.sh"
        fallback_str = fallback_m.group(1)
        # In the raw file text, \t is literal backslash-t
        parts = fallback_str.split("\\t")
        # parts[4] should be the voice_profile default
        assert parts[4] == "senior", (
            f"Fallback voice_profile is '{parts[4]}', should be 'senior'"
        )


class TestPlayShEvents:
    """Events in play.sh must be consistent with sounds.json."""

    def test_event_header_lists_all_events(self, play_script):
        """The comment header should list all 10 events."""
        # Comment: # Events: stop, edit, bash, search, ...
        m = re.search(r"# Events: (.+)", play_script)
        assert m, "Could not find Events comment in play.sh"
        header_events = {e.strip() for e in m.group(1).split(",")}
        assert header_events == ALL_EVENTS, (
            f"Header events mismatch:\n"
            f"  Missing: {ALL_EVENTS - header_events}\n"
            f"  Extra: {header_events - ALL_EVENTS}"
        )

    def test_manifest_events_are_valid(self, sounds_json):
        """All events in sounds.json should be from the known 10 events."""
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                for event in profile.get("events", {}).keys():
                    assert event in ALL_EVENTS, (
                        f"{layer}/{prof_name} has unknown event: '{event}'"
                    )


class TestPlayShLayerDispatch:
    """play.sh dispatches to the right layer handlers."""

    def test_narrator_pipes_to_narrate_py(self, play_script):
        """When voice_profile is narrator, stdin should be piped to narrate.py."""
        assert 'echo "$STDIN_DATA" | "$PYTHON3" "$SND/narrate.py"' in play_script

    def test_senior_reads_phrases_json(self, play_script):
        """When voice_profile is senior, play.sh reads phrases.json."""
        assert "PHRASES" in play_script
        assert "phrases.json" in play_script

    def test_effects_calls_play_sound(self, play_script):
        """Effects layer should call play_sound with 'effects'."""
        assert 'play_sound "effects"' in play_script

    def test_captures_stdin_before_backgrounding(self, play_script):
        """play.sh must capture stdin BEFORE the background block."""
        # STDIN_DATA should be set before the { ... } & block
        stdin_pos = play_script.find("STDIN_DATA=$(cat)")
        bg_start = play_script.find("{\n\nSND=")
        assert stdin_pos < bg_start, (
            "STDIN_DATA must be captured before the background block"
        )
