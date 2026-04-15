"""Hook format tests.

Validates install.sh hook definitions: no trailing &, all events covered,
correct script reference, valid JSON.
"""

import json
import re

import pytest


# All 10 events the soundbar handles
ALL_EVENTS = {
    "stop", "edit", "bash", "search", "permission", "error",
    "subagent_start", "subagent_stop", "session_start", "compact",
}


def _extract_hooks_json(install_script):
    """Extract the HOOKS_JSON heredoc from install.sh and parse it."""
    # The heredoc is between HOOKS_JSON=$(cat <<'EOF' and EOF)
    m = re.search(
        r"HOOKS_JSON=\$\(cat <<'EOF'\n(.*?)\nEOF\s*\)",
        install_script,
        re.DOTALL,
    )
    assert m, "Could not find HOOKS_JSON heredoc in install.sh"
    return json.loads(m.group(1))


class TestHookFormat:
    """Hook command format validation."""

    def test_no_trailing_ampersand_in_hook_commands(self, install_script):
        """Regression: play.sh backgrounds its own work; trailing & causes double-fork."""
        hooks = _extract_hooks_json(install_script)
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    assert not cmd.rstrip().endswith("&"), (
                        f"Hook command for {event_name} has trailing '&': {cmd}\n"
                        f"play.sh handles its own backgrounding"
                    )

    def test_hook_commands_reference_soundbar_play_sh(self, install_script):
        """All hook commands should use soundbar/play.sh, not deprecated play-sound.sh."""
        hooks = _extract_hooks_json(install_script)
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    assert "soundbar/play.sh" in cmd, (
                        f"Hook for {event_name} doesn't reference soundbar/play.sh: {cmd}"
                    )
                    assert "play-sound.sh" not in cmd, (
                        f"Hook for {event_name} uses deprecated play-sound.sh: {cmd}"
                    )


class TestHookCoverage:
    """All 10 events must be covered by hooks."""

    def test_all_events_covered(self, install_script):
        hooks = _extract_hooks_json(install_script)

        # Collect all events dispatched by hook commands
        covered_events = set()
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    # Extract the event argument: play.sh <event>
                    m = re.search(r"play\.sh\s+(\w+)", cmd)
                    if m:
                        covered_events.add(m.group(1))

        missing = ALL_EVENTS - covered_events
        assert not missing, f"Events not covered by hooks: {missing}"


class TestHookJSON:
    """The HOOKS_JSON heredoc must be valid, well-structured JSON."""

    def test_hooks_json_is_valid(self, install_script):
        hooks = _extract_hooks_json(install_script)
        assert isinstance(hooks, dict)

    def test_all_hooks_have_type_command(self, install_script):
        hooks = _extract_hooks_json(install_script)
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert hook.get("type") == "command", (
                        f"Hook for {event_name} has type '{hook.get('type')}', expected 'command'"
                    )

    def test_all_hooks_have_timeout(self, install_script):
        hooks = _extract_hooks_json(install_script)
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert "timeout" in hook, (
                        f"Hook for {event_name} missing timeout"
                    )
