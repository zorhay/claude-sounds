"""Narrator tests.

Tests narrate.py functions without making network calls or playing audio.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDBAR_DIR = REPO_ROOT / "soundbar"

if str(SOUNDBAR_DIR) not in sys.path:
    sys.path.insert(0, str(SOUNDBAR_DIR))


class TestNarrateImport:
    """narrate.py must import cleanly."""

    def test_import_narrate(self):
        import narrate
        assert hasattr(narrate, "build_context")
        assert hasattr(narrate, "PROVIDERS")
        assert hasattr(narrate, "read_styles")
        assert hasattr(narrate, "style_prompt")


class TestProviders:
    """PROVIDERS dict completeness."""

    def test_has_all_five_providers(self):
        from narrate import PROVIDERS
        expected = {"claude_cli", "anthropic", "gemini", "openai", "ollama"}
        assert set(PROVIDERS.keys()) == expected

    def test_providers_have_required_keys(self):
        from narrate import PROVIDERS
        required = {"name", "description", "needs_key", "default_model"}
        for name, meta in PROVIDERS.items():
            missing = required - set(meta.keys())
            assert not missing, f"Provider '{name}' missing keys: {missing}"

    def test_providers_needs_key_types(self):
        from narrate import PROVIDERS
        # claude_cli and ollama don't need keys; the rest do
        assert PROVIDERS["claude_cli"]["needs_key"] is False
        assert PROVIDERS["ollama"]["needs_key"] is False
        assert PROVIDERS["anthropic"]["needs_key"] is True
        assert PROVIDERS["gemini"]["needs_key"] is True
        assert PROVIDERS["openai"]["needs_key"] is True


class TestNarratorStyles:
    """Narrator styles are loaded from narrator_styles.defaults.json."""

    def test_includes_original_five_styles(self):
        from narrate import read_styles
        styles = read_styles()
        expected = {"pair_programmer", "sports", "documentary", "noir", "haiku_poet"}
        assert expected.issubset(set(styles.keys()))

    def test_styles_have_label_and_prompt(self):
        from narrate import read_styles
        styles = read_styles()
        for name, entry in styles.items():
            assert isinstance(entry, dict), f"Style '{name}' should be a dict"
            assert isinstance(entry.get("label"), str) and entry["label"], (
                f"Style '{name}' missing label"
            )
            assert isinstance(entry.get("prompt"), str) and len(entry["prompt"]) > 10, (
                f"Style '{name}' should have a non-trivial prompt"
            )

    def test_style_prompt_resolves_known_style(self):
        from narrate import style_prompt
        prompt = style_prompt("pair_programmer")
        assert isinstance(prompt, str) and len(prompt) > 10

    def test_style_prompt_falls_back_for_unknown(self):
        from narrate import style_prompt
        prompt = style_prompt("__definitely_not_a_real_style__")
        assert isinstance(prompt, str) and len(prompt) > 10


class TestBuildContext:
    """build_context() extracts narration-relevant context from event JSON."""

    def test_session_start(self):
        from narrate import build_context
        ctx = build_context({"hook_event_name": "SessionStart"})
        assert "session" in ctx.lower() or "started" in ctx.lower()

    def test_edit_tool(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/home/user/src/components/App.tsx"},
        })
        assert "Editing" in ctx
        assert "App.tsx" in ctx

    def test_write_tool(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/home/user/README.md"},
        })
        assert "Writing" in ctx

    def test_bash_tool_with_description(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test", "description": "Run the test suite"},
        })
        assert "Run the test suite" in ctx

    def test_bash_tool_without_description(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        })
        assert "git status" in ctx

    def test_grep_tool(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Grep",
            "tool_input": {"pattern": "TODO"},
        })
        assert "TODO" in ctx

    def test_read_tool(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/src/main.py"},
        })
        assert "Reading" in ctx
        assert "main.py" in ctx

    def test_stop_event(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "Stop",
            "stop_reason": "end_turn",
        })
        assert "finished" in ctx.lower() or "done" in ctx.lower() or "end_turn" in ctx

    def test_error_event(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "StopFailure",
            "error_message": "Rate limit exceeded",
        })
        assert "error" in ctx.lower() or "Rate limit" in ctx

    def test_subagent_start(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "SubagentStart",
            "agent_type": "research",
        })
        assert "agent" in ctx.lower() or "spawned" in ctx.lower()

    def test_subagent_stop(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "SubagentStop",
            "agent_type": "research",
        })
        assert "returned" in ctx.lower() or "agent" in ctx.lower()

    def test_compact_event(self):
        from narrate import build_context
        ctx = build_context({"hook_event_name": "PostCompact"})
        assert "compact" in ctx.lower()

    def test_permission_event(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
        })
        assert "permission" in ctx.lower() or "Bash" in ctx

    def test_unknown_tool(self):
        from narrate import build_context
        ctx = build_context({
            "hook_event_name": "PreToolUse",
            "tool_name": "CustomTool",
            "tool_input": {},
        })
        assert "CustomTool" in ctx

    def test_empty_event(self):
        from narrate import build_context
        ctx = build_context({})
        assert ctx == ""


class TestCheckKokoro:
    """check_kokoro() returns correctly typed results."""

    def test_returns_dict_with_ok_and_message(self):
        from narrate import check_kokoro
        result = check_kokoro()
        assert isinstance(result, dict)
        assert "ok" in result
        assert "message" in result
        assert isinstance(result["ok"], bool)
        assert isinstance(result["message"], str)


class TestKokoroPaths:
    """Kokoro paths are under ~/.claude/soundbar/."""

    def test_kokoro_sock_path(self):
        from narrate import KOKORO_SOCK
        assert str(KOKORO_SOCK).endswith("soundbar/kokoro.sock")
        assert ".claude/soundbar" in str(KOKORO_SOCK)

    def test_kokoro_venv_path(self):
        from narrate import KOKORO_VENV
        assert ".claude/soundbar/.venv" in str(KOKORO_VENV)
