#!/usr/bin/env python3
"""Soundbar narrator — multi-provider AI narration for Claude Code sessions.

Called by play.sh when voice_profile is "narrator". Reads hook event JSON from
stdin, calls an LLM to generate a short narration line, speaks it via macOS TTS.
Supports five providers: claude_cli, anthropic, gemini, openai, ollama.
"""

import json
import os
import shutil
import signal
import socket as sock_mod
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

LOCK_FILE = "/tmp/soundbar_narrator.lock"
SND = Path.home() / ".claude" / "soundbar"
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"

PROVIDERS = {
    "claude_cli": {
        "name": "Claude Code CLI",
        "description": "Uses your existing Claude Code auth. No extra setup needed.",
        "needs_key": False,
        "default_model": "haiku",
    },
    "anthropic": {
        "name": "Anthropic API",
        "description": "Direct Claude API. Requires key from console.anthropic.com",
        "needs_key": True,
        "default_model": "claude-haiku-4-5-20251001",
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Google AI. Requires key from aistudio.google.com",
        "needs_key": True,
        "default_model": "gemini-2.5-flash",
    },
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI API. Requires key from platform.openai.com",
        "needs_key": True,
        "default_model": "gpt-4o-mini",
    },
    "ollama": {
        "name": "Ollama (local)",
        "description": "Local LLM via Ollama. No API key. Requires Ollama running.",
        "needs_key": False,
        "default_model": "qwen3.5:4b",
    },
}

NARRATOR_STYLES = {
    "pair_programmer": "You are a friendly pair programmer. Comment briefly on what the developer is doing — natural, supportive, sometimes amused.",
    "sports": "You are an enthusiastic sports commentator narrating a live coding session. High energy, dramatic, play-by-play style.",
    "documentary": "You are David Attenborough narrating a nature documentary about a programmer in their natural habitat. Gentle wonder and dry wit.",
    "noir": "You are a hardboiled detective narrating a noir film about code. World-weary, sardonic, everything sounds suspicious.",
    "haiku_poet": "Respond ONLY with a haiku (5-7-5 syllables) about what the programmer is doing. No other text.",
}

PROMPT_PREFIX = "Narrate this coding moment in one short sentence: "

TTS_ENGINES = {
    "say": {
        "name": "macOS Say",
        "description": "Built-in macOS TTS. No setup needed.",
    },
    "kokoro": {
        "name": "Kokoro",
        "description": "Local neural TTS via background daemon. Keeps model warm for fast speech.",
    },
}

KOKORO_VOICES = [
    {"id": "af_heart", "name": "Heart", "gender": "female"},
    {"id": "af_bella", "name": "Bella", "gender": "female"},
    {"id": "af_nicole", "name": "Nicole", "gender": "female"},
    {"id": "af_sarah", "name": "Sarah", "gender": "female"},
    {"id": "af_sky", "name": "Sky", "gender": "female"},
    {"id": "am_adam", "name": "Adam", "gender": "male"},
    {"id": "am_michael", "name": "Michael", "gender": "male"},
    {"id": "bf_emma", "name": "Emma (British)", "gender": "female"},
    {"id": "bf_isabella", "name": "Isabella (British)", "gender": "female"},
    {"id": "bm_george", "name": "George (British)", "gender": "male"},
    {"id": "bm_lewis", "name": "Lewis (British)", "gender": "male"},
]


def read_config():
    for path in (CONFIG_FILE, CONFIG_DEFAULTS):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def build_context(data):
    """Extract a short narration-relevant string from hook event JSON."""
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    if event == "SessionStart":
        return "New coding session started."

    if event in ("SubagentStart", "SubagentStop"):
        atype = data.get("agent_type", "agent")
        verb = "spawned" if "Start" in event else "returned"
        return f"Sub-agent ({atype}) {verb}."

    if event == "Stop":
        reason = data.get("stop_reason", "done")
        return f"Task finished: {reason}."

    if event in ("StopFailure", "PostToolUseFailure"):
        msg = data.get("error_message", "unknown error")[:120]
        return f"Error occurred: {msg}"

    if event == "PostCompact":
        return "Context was compacted (conversation got long)."

    if event == "PermissionRequest":
        return f"Asking permission to use {tool}."

    if tool in ("Edit", "Write"):
        fp = inp.get("file_path", "")
        parts = fp.rsplit("/", 2)
        short = "/".join(parts[-2:]) if len(parts) >= 2 else fp
        verb = "Editing" if tool == "Edit" else "Writing"
        return f"{verb} {short}"

    if tool == "Bash":
        desc = inp.get("description", "")
        if desc:
            return f"Running command: {desc[:150]}"
        cmd = inp.get("command", "")
        return f"Running: {cmd[:150]}"

    if tool in ("Grep", "Glob"):
        pattern = inp.get("pattern", "")
        return f"Searching for: {pattern[:150]}"

    if tool == "Read":
        fp = inp.get("file_path", "")
        parts = fp.rsplit("/", 2)
        short = "/".join(parts[-2:]) if len(parts) >= 2 else fp
        return f"Reading {short}"

    if tool == "Agent":
        desc = inp.get("description", "") or inp.get("prompt", "")[:100]
        return f"Delegating: {desc[:150]}"

    if tool:
        return f"Using {tool}."

    return f"Event: {event}" if event else ""


# --- Provider implementations ---

def _http_json(url, headers, body, timeout):
    """POST JSON, return parsed response."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _call_claude_cli(model, context, style):
    system = NARRATOR_STYLES.get(style, NARRATOR_STYLES["pair_programmer"])
    prompt = f"{system}\n\n{PROMPT_PREFIX}{context}"
    result = subprocess.run(
        ["claude", "-p", "--model", model, prompt],
        capture_output=True, text=True, timeout=15,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _call_anthropic(model, api_key, context, style):
    system = NARRATOR_STYLES.get(style, NARRATOR_STYLES["pair_programmer"])
    result = _http_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": api_key, "anthropic-version": "2023-06-01",
         "Content-Type": "application/json"},
        {"model": model, "max_tokens": 80, "system": system,
         "messages": [{"role": "user", "content": PROMPT_PREFIX + context}]},
        timeout=5,
    )
    return result["content"][0]["text"].strip()


def _call_gemini(model, api_key, context, style):
    system = NARRATOR_STYLES.get(style, NARRATOR_STYLES["pair_programmer"])
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    result = _http_json(
        url, {"Content-Type": "application/json"},
        {"system_instruction": {"parts": [{"text": system}]},
         "contents": [{"parts": [{"text": PROMPT_PREFIX + context}]}],
         "generationConfig": {"maxOutputTokens": 80}},
        timeout=5,
    )
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_openai(model, api_key, context, style):
    system = NARRATOR_STYLES.get(style, NARRATOR_STYLES["pair_programmer"])
    result = _http_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        {"model": model, "max_tokens": 80,
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": PROMPT_PREFIX + context}]},
        timeout=5,
    )
    return result["choices"][0]["message"]["content"].strip()


def _call_ollama(model, context, style):
    system = NARRATOR_STYLES.get(style, NARRATOR_STYLES["pair_programmer"])
    result = _http_json(
        "http://localhost:11434/api/chat",
        {"Content-Type": "application/json"},
        {"model": model, "stream": False,
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": PROMPT_PREFIX + context}],
         "options": {"num_predict": 80}},
        timeout=10,
    )
    return result["message"]["content"].strip()


def call_provider(provider, model, api_key, context, style):
    """Dispatch to the appropriate provider. Returns narration text or None."""
    meta = PROVIDERS.get(provider)
    if not meta:
        return None
    if not model:
        model = meta["default_model"]

    if provider == "claude_cli":
        return _call_claude_cli(model, context, style)
    elif provider == "anthropic":
        return _call_anthropic(model, api_key, context, style)
    elif provider == "gemini":
        return _call_gemini(model, api_key, context, style)
    elif provider == "openai":
        return _call_openai(model, api_key, context, style)
    elif provider == "ollama":
        return _call_ollama(model, context, style)
    return None


def check_provider(provider, model, api_key):
    """Test if a provider is reachable/configured."""
    meta = PROVIDERS.get(provider)
    if not meta:
        return {"ok": False, "message": f"Unknown provider: {provider}"}

    if provider == "claude_cli":
        if shutil.which("claude"):
            return {"ok": True, "message": "claude CLI found in PATH."}
        return {"ok": False, "message": "claude CLI not found in PATH."}

    if provider in ("anthropic", "gemini", "openai"):
        if not api_key:
            return {"ok": False, "message": f"No API key configured. Get one from {meta['description'].split('from ')[-1]}"}
        if not model:
            model = meta["default_model"]
        try:
            call_provider(provider, model, api_key, "Test connection.", "pair_programmer")
            return {"ok": True, "message": f"Connected ({api_key[:8]}...)."}
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                return {"ok": False, "message": f"Invalid API key ({api_key[:8]}...)."}
            return {"ok": False, "message": f"Connection failed: {msg[:100]}"}

    if provider == "ollama":
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                count = len(data.get("models", []))
                return {"ok": True, "message": f"Ollama running, {count} model(s) available."}
        except Exception as e:
            return {"ok": False, "message": f"Cannot reach Ollama at localhost:11434: {e}"}

    return {"ok": False, "message": "Unknown provider."}


def speak(text, engine, voice, volume):
    """Speak text via configured TTS engine."""
    if engine == "kokoro":
        speak_kokoro(text, voice, volume)
    else:
        speak_say(text, voice, volume)


def speak_say(text, voice, volume):
    """Speak text via macOS TTS with volume control."""
    vol_str = f"{volume // 100}.{volume % 100:02d}"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".aiff", prefix="soundbar_narr_")
        os.close(fd)
        subprocess.run(
            ["say", "-v", voice, "-r", "190", text, "-o", tmp],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10,
        )
        subprocess.run(
            ["afplay", "-v", vol_str, tmp],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=30,
        )
    except Exception:
        pass
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


KOKORO_SOCK = SND / "kokoro.sock"
KOKORO_VENV = SND / ".venv" / "bin" / "python"
KOKORO_DAEMON = SND / "kokoro_server.py"


def _kokoro_request(cmd, timeout=30, **kwargs):
    """Send a request to the Kokoro daemon. Returns response dict or None."""
    s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
    try:
        s.connect(str(KOKORO_SOCK))
        s.settimeout(timeout)
        req = json.dumps({"cmd": cmd, **kwargs})
        s.sendall(req.encode() + b"\n")
        resp = s.makefile().readline()
        return json.loads(resp) if resp else None
    except Exception:
        return None
    finally:
        s.close()


def _ensure_kokoro_daemon():
    """Start Kokoro daemon if not running. Returns True if daemon is available."""
    # Already running?
    if KOKORO_SOCK.exists():
        resp = _kokoro_request("health", timeout=3)
        if resp and resp.get("ok"):
            return True
        # Stale socket
        try:
            KOKORO_SOCK.unlink()
        except OSError:
            pass

    # Venv must exist
    if not KOKORO_VENV.exists():
        return False

    # Start daemon (detached)
    subprocess.Popen(
        [str(KOKORO_VENV), str(KOKORO_DAEMON)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for socket — daemon creates it before model finishes loading
    for _ in range(50):
        time.sleep(0.1)
        if KOKORO_SOCK.exists():
            resp = _kokoro_request("health", timeout=3)
            if resp and resp.get("ok"):
                return True
    return False


def speak_kokoro(text, voice, volume):
    """Speak text via Kokoro daemon (auto-starts if needed)."""
    if not _ensure_kokoro_daemon():
        speak_say(text, "Tara", volume)
        return

    resp = _kokoro_request("speak", timeout=30, text=text, voice=voice, volume=volume)
    if not resp or not resp.get("ok"):
        speak_say(text, "Tara", volume)


def _read_integrations():
    """Read integrations.json for cached state."""
    try:
        return json.loads((SND / "integrations.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def check_kokoro():
    """Check if Kokoro TTS is available."""
    if not KOKORO_VENV.exists():
        # Check integrations for python detection info
        data = _read_integrations()
        py_info = data.get("kokoro_python")
        if py_info and not py_info.get("ok"):
            return {"ok": False, "message": py_info.get("message", "No compatible Python found.")}
        return {
            "ok": False,
            "message": "Kokoro not installed. Use the control panel to install, "
            "or set up manually with a compatible Python (3.9-3.11).",
        }

    if KOKORO_SOCK.exists():
        resp = _kokoro_request("health", timeout=3)
        if resp and resp.get("ok"):
            ready = resp.get("ready", False)
            if ready:
                return {"ok": True, "message": "Kokoro daemon running, model loaded."}
            return {"ok": True, "message": "Kokoro daemon running, model loading..."}

    # Daemon not running — check if kokoro package is installed
    try:
        result = subprocess.run(
            [str(KOKORO_VENV), "-c", "import kokoro"],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0:
            return {"ok": True, "message": "Kokoro installed. Daemon will auto-start on first use."}
        return {
            "ok": False,
            "message": "Kokoro not installed in venv. Use the control panel to reinstall.",
        }
    except Exception as e:
        return {"ok": False, "message": f"Error checking venv: {e}"}


# --- Lock file ---

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def acquire_lock():
    try:
        if os.path.exists(LOCK_FILE):
            pid = int(Path(LOCK_FILE).read_text().strip())
            if _pid_alive(pid):
                return False
    except (ValueError, OSError):
        pass
    try:
        Path(LOCK_FILE).write_text(str(os.getpid()))
        return True
    except OSError:
        return False


def release_lock():
    try:
        os.unlink(LOCK_FILE)
    except OSError:
        pass


# --- Entry points ---

def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return

    if not acquire_lock():
        return

    try:
        config = read_config()
        provider = config.get("narrator_provider", "claude_cli")
        model = config.get("narrator_model", "")
        api_key = config.get("narrator_api_key", "")
        style = config.get("narrator_style", "pair_programmer")
        tts_engine = config.get("tts_engine", "say")
        voice = config.get("kokoro_voice", "af_heart") if tts_engine == "kokoro" \
            else config.get("voice_main", "Tara")
        volume = int(config.get("voice_volume", 100))

        context = build_context(data)
        if not context:
            return

        text = call_provider(provider, model, api_key, context, style)
        if text:
            speak(text, tts_engine, voice, volume)
    except Exception:
        pass
    finally:
        release_lock()


if __name__ == "__main__":
    if "--check" in sys.argv:
        config = read_config()
        provider = config.get("narrator_provider", "claude_cli")
        model = config.get("narrator_model", "")
        api_key = config.get("narrator_api_key", "")
        result = check_provider(provider, model, api_key)
        print(json.dumps(result))
    elif "--check-tts" in sys.argv:
        config = read_config()
        engine = config.get("tts_engine", "say")
        if engine == "kokoro":
            print(json.dumps(check_kokoro()))
        else:
            print(json.dumps({"ok": True, "message": "macOS say is built-in."}))
    elif "--speak" in sys.argv:
        # TTS mode: speak text from remaining args using configured engine
        # Usage: narrate.py --speak "text to speak"
        idx = sys.argv.index("--speak")
        text = " ".join(sys.argv[idx + 1:])
        if not text:
            sys.exit(0)
        config = read_config()
        engine = config.get("tts_engine", "say")
        voice = config.get("kokoro_voice", "af_heart") if engine == "kokoro" \
            else config.get("voice_main", "Tara")
        volume = int(config.get("voice_volume", 100))
        speak(text, engine, voice, volume)
    elif "--dry-run" in sys.argv:
        try:
            data = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, ValueError):
            print("Error: invalid JSON on stdin", file=sys.stderr)
            sys.exit(1)
        config = read_config()
        provider = config.get("narrator_provider", "claude_cli")
        model = config.get("narrator_model", "")
        api_key = config.get("narrator_api_key", "")
        style = config.get("narrator_style", "pair_programmer")
        context = build_context(data)
        if not context:
            print("Error: no context extracted from event", file=sys.stderr)
            sys.exit(1)
        try:
            text = call_provider(provider, model, api_key, context, style)
            print(text or "")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        main()
