"""Sound manifest integrity tests.

Validates sounds.json structure, referenced audio files, and profile
consistency with switch.sh and server.py.
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

ALL_EVENTS = {
    "stop", "edit", "bash", "search", "permission", "error",
    "subagent_start", "subagent_stop", "session_start", "compact",
}


class TestManifestStructure:
    """Basic structural validity of sounds.json."""

    def test_sounds_json_is_valid_json(self):
        text = (SOUNDBAR_DIR / "sounds.json").read_text()
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_has_effects_and_voice_layers(self, sounds_json):
        assert "effects" in sounds_json
        assert "voice" in sounds_json

    def test_all_profiles_have_events_dict(self, sounds_json):
        for layer in ("effects", "voice"):
            for name, profile in sounds_json[layer].items():
                assert "events" in profile, (
                    f"{layer}/{name} is missing 'events' dict"
                )
                assert isinstance(profile["events"], dict), (
                    f"{layer}/{name} 'events' is not a dict"
                )


class TestAudioFileReferences:
    """All referenced audio files must exist on disk."""

    def _collect_file_refs(self, sounds_json):
        """Yield (profile_name, dir, filename) for every file ref in the manifest."""
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                base_dir = profile.get("dir", "")
                for event, spec in profile.get("events", {}).items():
                    if "file" in spec:
                        yield prof_name, base_dir, spec["file"], event
                    if "files" in spec:
                        for f in spec["files"]:
                            yield prof_name, base_dir, f, event
                    if "sequence" in spec:
                        for variant in spec["sequence"]:
                            for f in variant:
                                yield prof_name, base_dir, f, event

    def test_all_referenced_files_exist(self, sounds_json):
        missing = []
        for prof, base_dir, filename, event in self._collect_file_refs(sounds_json):
            if filename.startswith("/"):
                # System files (e.g. /System/Library/Sounds/) -- skip
                continue
            if base_dir:
                full = SOUNDBAR_DIR / base_dir / filename
            else:
                full = SOUNDBAR_DIR / filename
            if not full.exists():
                missing.append(f"{prof}/{event}: {full}")
        assert not missing, f"Missing audio files:\n" + "\n".join(missing)

    def test_files_arrays_are_nonempty(self, sounds_json):
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                for event, spec in profile.get("events", {}).items():
                    if "files" in spec:
                        assert len(spec["files"]) > 0, (
                            f"{layer}/{prof_name}/{event}: 'files' array is empty"
                        )

    def test_sequence_entries_are_nonempty(self, sounds_json):
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                for event, spec in profile.get("events", {}).items():
                    if "sequence" in spec:
                        assert len(spec["sequence"]) > 0, (
                            f"{layer}/{prof_name}/{event}: 'sequence' is empty"
                        )
                        for i, variant in enumerate(spec["sequence"]):
                            assert len(variant) > 0, (
                                f"{layer}/{prof_name}/{event}: sequence[{i}] is empty"
                            )


class TestRateRanges:
    """Rate variation ranges must be valid."""

    def test_rate_min_less_than_max(self, sounds_json):
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                for event, spec in profile.get("events", {}).items():
                    if "rate" in spec:
                        lo, hi = spec["rate"]
                        assert lo < hi, (
                            f"{layer}/{prof_name}/{event}: rate min ({lo}) >= max ({hi})"
                        )

    def test_rate_within_bounds(self, sounds_json):
        for layer in ("effects", "voice"):
            for prof_name, profile in sounds_json[layer].items():
                for event, spec in profile.get("events", {}).items():
                    if "rate" in spec:
                        lo, hi = spec["rate"]
                        assert 0.5 <= lo <= 2.0, (
                            f"{layer}/{prof_name}/{event}: rate min {lo} out of [0.5, 2.0]"
                        )
                        assert 0.5 <= hi <= 2.0, (
                            f"{layer}/{prof_name}/{event}: rate max {hi} out of [0.5, 2.0]"
                        )


class TestProfileNameConsistency:
    """Profile names in manifest must match switch.sh and server.py."""

    def test_effects_profiles_match_switch_sh(self, sounds_json, switch_script):
        # Extract EFFECTS_PROFILES from switch.sh
        m = re.search(r'EFFECTS_PROFILES="([^"]+)"', switch_script)
        assert m, "Could not find EFFECTS_PROFILES in switch.sh"
        switch_profiles = set(m.group(1).split())

        manifest_effects = set(sounds_json["effects"].keys())
        assert manifest_effects == switch_profiles, (
            f"Mismatch: manifest={manifest_effects - switch_profiles} "
            f"extra, switch.sh={switch_profiles - manifest_effects} extra"
        )

    def test_voice_profiles_match_switch_sh(self, switch_script):
        # Extract VOICE_PROFILES from switch.sh
        m = re.search(r'VOICE_PROFILES="([^"]+)"', switch_script)
        assert m, "Could not find VOICE_PROFILES in switch.sh"
        switch_profiles = set(m.group(1).split())

        # server.py should have the same list
        sys.path.insert(0, str(SOUNDBAR_DIR))
        import server
        server_profiles = set(server.VOICE_PROFILES)
        assert switch_profiles == server_profiles, (
            f"switch.sh VOICE_PROFILES={switch_profiles} != "
            f"server.py VOICE_PROFILES={server_profiles}"
        )

    def test_manifest_voice_profiles_in_voice_list(self, sounds_json):
        """All manifest voice profiles should be known to server.py."""
        sys.path.insert(0, str(SOUNDBAR_DIR))
        import server
        known = set(server.VOICE_PROFILES)
        # Manifest voice profiles (like 'generals') should be in the server list
        for name in sounds_json.get("voice", {}).keys():
            assert name in known, (
                f"Manifest voice profile '{name}' not in server.py VOICE_PROFILES"
            )
