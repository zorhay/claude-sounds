#!/usr/bin/env python3
"""Soundbar narrator — multi-provider AI narration for Claude Code sessions.

Called by play.sh when voice_profile is "narrator". Reads hook event JSON from
stdin, calls an LLM to generate a short narration line, speaks it via macOS TTS.
Supports five providers: claude_cli, anthropic, gemini, openai, ollama.
"""

import json
import logging
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

# File logger — narrate.py runs as subprocess with stdout/stderr suppressed,
# so file logging is the only way to trace issues.
_log = logging.getLogger("narrate")
_log.setLevel(logging.DEBUG)
_fh = logging.FileHandler(SND / "debug.log", mode="a")
_fh.setFormatter(logging.Formatter("%(asctime)s NARRATE %(levelname)s %(message)s", datefmt="%H:%M:%S"))
_fh.setLevel(logging.DEBUG)
_log.addHandler(_fh)
_eh = logging.FileHandler(SND / "error.log", mode="a")
_eh.setFormatter(logging.Formatter("%(asctime)s NARRATE %(levelname)s %(message)s", datefmt="%H:%M:%S"))
_eh.setLevel(logging.ERROR)
_log.addHandler(_eh)
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"
STYLES_FILE = SND / "narrator_styles.json"
STYLES_DEFAULTS = SND / "narrator_styles.defaults.json"

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

# Fallback prompt used only if no styles file is available at all.
FALLBACK_STYLE_PROMPT = "You are a friendly pair programmer. Comment briefly on what the developer is doing — natural, supportive, sometimes amused."

PROMPT_PREFIX = "Narrate this coding moment in one short sentence: "

# Appended to every narrator system prompt. Output will be read aloud by TTS,
# so markdown syntax and URLs would be spoken literally and sound awful.
AUDIO_CLARITY_RULES = (
    "\n\nOutput plain spoken prose only — this will be read aloud by a TTS engine. "
    "Do NOT use any markdown or formatting: no asterisks, underscores, backticks, "
    "code blocks, hashes, brackets, bullet points, or numbered lists. "
    "Do NOT include URLs or full file paths; refer to files by their base name "
    "(e.g. \"app.ts\"), and omit links entirely. "
    "Do NOT write emoji, stage directions, parentheticals, or quote marks around the line. "
    "One short, conversational sentence that sounds natural when spoken."
)

# System prompt for context compression (separate from narrator style).
COMPRESS_SYSTEM = (
    "You are summarizing a running log of a coding session for a narrator to reference. "
    "Respond with 2 short sentences capturing the main files touched, tasks attempted, "
    "and any arc of progress. Plain prose, no formatting, no lists."
)

# Session context parameters.
SESSION_DIR = SND / "session_context"
SESSION_RECENT_KEEP = 4     # how many recent entries survive a compression pass
SESSION_RECENT_TRIGGER = 10 # compress once recent exceeds this
SESSION_MAX_AGE = 7 * 24 * 3600  # drop session files older than 7 days


def read_styles():
    """Read narrator styles (user file overrides defaults).

    Returns dict of {id: {"label": str, "prompt": str}}.
    """
    for path in (STYLES_FILE, STYLES_DEFAULTS):
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def style_prompt(style_id):
    """Return the raw style prompt for a style id, with fallback."""
    styles = read_styles()
    s = styles.get(style_id) if style_id else None
    if isinstance(s, dict) and s.get("prompt"):
        return s["prompt"]
    # Legacy flat mapping {id: "prompt string"}
    if isinstance(s, str):
        return s
    # Fall back to pair_programmer default, then hardcoded string
    pp = styles.get("pair_programmer")
    if isinstance(pp, dict) and pp.get("prompt"):
        return pp["prompt"]
    if isinstance(pp, str):
        return pp
    return FALLBACK_STYLE_PROMPT


def build_system_prompt(style_id):
    """Return the full system prompt: style persona + audio-clarity constraints."""
    return style_prompt(style_id) + AUDIO_CLARITY_RULES

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


def _call_claude_cli(model, system, user, max_tokens=80, timeout=15):
    prompt = f"{system}\n\n{user}"
    result = subprocess.run(
        ["claude", "-p", "--model", model, prompt],
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _call_anthropic(model, api_key, system, user, max_tokens=80, timeout=5):
    result = _http_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": api_key, "anthropic-version": "2023-06-01",
         "Content-Type": "application/json"},
        {"model": model, "max_tokens": max_tokens, "system": system,
         "messages": [{"role": "user", "content": user}]},
        timeout=timeout,
    )
    return result["content"][0]["text"].strip()


def _call_gemini(model, api_key, system, user, max_tokens=80, timeout=5):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    result = _http_json(
        url, {"Content-Type": "application/json"},
        {"system_instruction": {"parts": [{"text": system}]},
         "contents": [{"parts": [{"text": user}]}],
         "generationConfig": {"maxOutputTokens": max_tokens}},
        timeout=timeout,
    )
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_openai(model, api_key, system, user, max_tokens=80, timeout=5):
    result = _http_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        {"model": model, "max_tokens": max_tokens,
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": user}]},
        timeout=timeout,
    )
    return result["choices"][0]["message"]["content"].strip()


def _call_ollama(model, system, user, max_tokens=80, timeout=10):
    result = _http_json(
        "http://localhost:11434/api/chat",
        {"Content-Type": "application/json"},
        {"model": model, "stream": False,
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": user}],
         "options": {"num_predict": max_tokens}},
        timeout=timeout,
    )
    return result["message"]["content"].strip()


def _dispatch(provider, model, api_key, system, user, max_tokens=80, timeout=None):
    """Low-level provider dispatch. Returns text or None."""
    meta = PROVIDERS.get(provider)
    if not meta:
        return None
    if not model:
        model = meta["default_model"]
    if provider == "claude_cli":
        return _call_claude_cli(model, system, user, max_tokens, timeout or 15)
    if provider == "anthropic":
        return _call_anthropic(model, api_key, system, user, max_tokens, timeout or 5)
    if provider == "gemini":
        return _call_gemini(model, api_key, system, user, max_tokens, timeout or 5)
    if provider == "openai":
        return _call_openai(model, api_key, system, user, max_tokens, timeout or 5)
    if provider == "ollama":
        return _call_ollama(model, system, user, max_tokens, timeout or 10)
    return None


def call_provider(provider, model, api_key, context, style, session=None):
    """Generate narration.

    When `session` is provided (deep-context mode), prior summary and recent
    moments from this session are embedded in the user turn so the LLM can
    reference earlier work.
    """
    system = build_system_prompt(style)
    user = _build_user_turn(context, session)
    return _dispatch(provider, model, api_key, system, user)


def _build_user_turn(context, session):
    """Compose the user message, optionally enriched with prior session memory."""
    if not session:
        return PROMPT_PREFIX + context

    summary = (session.get("summary") or "").strip()
    recent = session.get("recent") or []
    if not summary and not recent:
        return PROMPT_PREFIX + context

    parts = ["Prior context from this coding session (for continuity — do not narrate it, just reference it if natural):\n"]
    if summary:
        parts.append(f"Earlier work: {summary}\n")
    if recent:
        parts.append("Recent moments you already narrated:\n")
        for item in recent[-SESSION_RECENT_TRIGGER:]:
            ctx = (item.get("ctx") or "").strip()
            text = (item.get("text") or "").strip()
            if ctx and text:
                parts.append(f"  • ({ctx}) — you said: \"{text}\"\n")
            elif ctx:
                parts.append(f"  • {ctx}\n")
    parts.append("\nNow, ")
    parts.append(PROMPT_PREFIX.lower())
    parts.append(context)
    return "".join(parts)


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
            _dispatch(provider, model, api_key, "You are a connection test.", "Reply with OK.", max_tokens=8)
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
    _log.info("speak: engine=%s voice=%s volume=%s", engine, voice, volume)
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
    _log.debug("kokoro request: cmd=%s timeout=%s", cmd, timeout)
    s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
    try:
        s.connect(str(KOKORO_SOCK))
        s.settimeout(timeout)
        req = json.dumps({"cmd": cmd, **kwargs})
        s.sendall(req.encode() + b"\n")
        resp = s.makefile().readline()
        result = json.loads(resp) if resp else None
        _log.debug("kokoro response: %s", result)
        return result
    except Exception as e:
        _log.error("kokoro request failed: cmd=%s err=%s", cmd, e)
        return None
    finally:
        s.close()


def _ensure_kokoro_daemon():
    """Start Kokoro daemon if not running. Returns True if daemon is available."""
    _log.debug("ensure_kokoro_daemon: sock=%s exists=%s", KOKORO_SOCK, KOKORO_SOCK.exists())

    # Already running?
    if KOKORO_SOCK.exists():
        resp = _kokoro_request("health", timeout=3)
        if resp and resp.get("ok"):
            _log.debug("daemon already running")
            return True
        # Stale socket
        _log.debug("stale socket, removing")
        try:
            KOKORO_SOCK.unlink()
        except OSError:
            pass

    # Venv must exist
    if not KOKORO_VENV.exists():
        _log.error("venv python missing: %s", KOKORO_VENV)
        return False

    # Start daemon (detached)
    daemon_cmd = [str(KOKORO_VENV), str(KOKORO_DAEMON)]
    _log.info("starting kokoro daemon: %s", daemon_cmd)

    # Capture daemon stderr to a log file for debugging
    daemon_log = SND / "kokoro_daemon.log"
    try:
        daemon_err = open(daemon_log, "a")
    except Exception:
        daemon_err = subprocess.DEVNULL

    subprocess.Popen(
        daemon_cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=daemon_err,
        start_new_session=True,
    )

    # Wait for socket — daemon creates it before model finishes loading
    for i in range(50):
        time.sleep(0.1)
        if KOKORO_SOCK.exists():
            resp = _kokoro_request("health", timeout=3)
            if resp and resp.get("ok"):
                _log.info("daemon started after %.1fs", (i + 1) * 0.1)
                return True
    _log.error("daemon failed to start within 5s. Check %s", daemon_log)
    return False


def speak_kokoro(text, voice, volume):
    """Speak text via Kokoro daemon (auto-starts if needed).

    Does NOT fall back to macOS say — if kokoro fails, it fails silently.
    Callers (play.sh hooks) should not degrade to a different engine
    without the user knowing. The UI shows engine status so the user
    can switch manually if kokoro is broken.
    """
    _log.info("speak_kokoro: voice=%s volume=%s text=%r", voice, volume, text[:60])
    if not _ensure_kokoro_daemon():
        _log.error("speak_kokoro: daemon unavailable, skipping")
        return

    resp = _kokoro_request("speak", timeout=30, text=text, voice=voice, volume=volume)
    if not resp or not resp.get("ok"):
        _log.error("speak_kokoro: speak failed: %s", resp)


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


# --- Session context (deep mode) ---

def _session_path(session_id):
    """Return the path for a session's context file. None for invalid ids."""
    if not session_id:
        return None
    # Keep filename safe: only allow letters, digits, dash, underscore.
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:80]
    if not safe:
        return None
    return SESSION_DIR / f"{safe}.json"


def load_session(session_id):
    """Load or create a session context blob."""
    path = _session_path(session_id)
    if not path:
        return None
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _log.error("session dir create failed: %s", e)
        return None
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("session_id", session_id)
                data.setdefault("summary", "")
                data.setdefault("recent", [])
                data.setdefault("created", int(time.time()))
                return data
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("bad session file %s: %s", path, e)
    return {
        "session_id": session_id,
        "created": int(time.time()),
        "updated": int(time.time()),
        "summary": "",
        "recent": [],
    }


def save_session(session):
    """Persist session context, pruning old sessions opportunistically."""
    if not isinstance(session, dict):
        return
    path = _session_path(session.get("session_id"))
    if not path:
        return
    session["updated"] = int(time.time())
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2))
    except OSError as e:
        _log.error("session save failed: %s", e)
    _prune_sessions()


def _prune_sessions():
    """Drop session files older than SESSION_MAX_AGE."""
    try:
        cutoff = time.time() - SESSION_MAX_AGE
        for p in SESSION_DIR.glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass


def compress_session_if_needed(session, provider, model, api_key):
    """If recent history is too long, compress older entries into the summary."""
    recent = session.get("recent") or []
    if len(recent) <= SESSION_RECENT_TRIGGER:
        return
    # Split: fold everything but the last SESSION_RECENT_KEEP entries into summary.
    to_fold = recent[:-SESSION_RECENT_KEEP]
    keep = recent[-SESSION_RECENT_KEEP:]

    prior = (session.get("summary") or "").strip()
    bullets = "\n".join(
        f"- ({(it.get('ctx') or '').strip()}) {(it.get('text') or '').strip()}"
        for it in to_fold
    )
    user = "Prior summary:\n" + (prior or "(none)") + "\n\nNew events to fold in:\n" + bullets
    try:
        new_summary = _dispatch(provider, model, api_key, COMPRESS_SYSTEM, user,
                                max_tokens=160, timeout=10)
    except Exception as e:
        _log.warning("compression failed: %s", e)
        new_summary = None

    if new_summary:
        session["summary"] = new_summary.strip()
        session["recent"] = keep
    else:
        # If compression failed, at least trim to prevent unbounded growth.
        session["recent"] = keep


def update_session(session, context, text):
    """Append a narration to the session's recent-moments log."""
    if not isinstance(session, dict):
        return
    recent = session.setdefault("recent", [])
    recent.append({
        "t": int(time.time()),
        "ctx": context,
        "text": text,
    })
    session["event_count"] = session.get("event_count", 0) + 1


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

def main(force_deep=False):
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
        deep = force_deep or bool(config.get("narrator_deep_context", False))
        tts_engine = config.get("tts_engine", "say")
        voice = config.get("kokoro_voice", "af_heart") if tts_engine == "kokoro" \
            else config.get("voice_main", "Tara")
        volume = int(config.get("voice_volume", 100))

        context = build_context(data)
        if not context:
            return

        session = None
        session_id = data.get("session_id")
        if deep and session_id:
            session = load_session(session_id)

        text = call_provider(provider, model, api_key, context, style, session=session)
        if text:
            speak(text, tts_engine, voice, volume)
            if session is not None:
                update_session(session, context, text)
                compress_session_if_needed(session, provider, model, api_key)
                save_session(session)
    except Exception as e:
        _log.error("main failed: %s", e)
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
        deep = ("--deep" in sys.argv) or bool(config.get("narrator_deep_context", False))
        session = None
        session_id = data.get("session_id")
        if deep and session_id:
            session = load_session(session_id)
        try:
            text = call_provider(provider, model, api_key, context, style, session=session)
            print(text or "")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        main(force_deep="--deep" in sys.argv)
