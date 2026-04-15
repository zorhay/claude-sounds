#!/usr/bin/env python3
"""Soundbar — Control panel HTTP server for Claude Code sound system.

API:
  GET  /                  → ui.html
  GET  /api/status        → full state (config + profiles + phrases + voices)
  POST /api/config        → update config (any subset of keys)
  POST /api/phrases       → update phrases for one event
  POST /api/play          → play a specific profile+event sound directly
  POST /api/say           → preview a TTS voice
  POST /api/narrator-check → check narrator provider connectivity
  POST /api/narrator-test  → generate and speak test narration
"""

import json
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("soundbar")

# Debug/error file loggers for Kokoro install diagnostics
_dlog = logging.getLogger("soundbar.debug")
_elog = logging.getLogger("soundbar.errors")

SND = Path(__file__).parent
UI_FILE = SND / "ui.html"
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"
PHRASES_FILE = SND / "phrases.json"
PHRASES_DEFAULTS = SND / "phrases.defaults.json"
SOUNDS_FILE = SND / "sounds.json"
KOKORO_VENV = SND / ".venv"
KOKORO_VENV_PY = KOKORO_VENV / "bin" / "python3"

EVENTS = [
    "session_start", "edit", "bash", "search",
    "permission", "error", "subagent_start",
    "subagent_stop", "compact", "stop",
]

DIALOGUE_EVENTS = {"subagent_start", "subagent_stop"}
VOICE_PROFILES = ["senior", "narrator", "generals"]

CONFIG_KEYS = {"python3_path", "effects_on", "effects_profile", "effects_volume", "voice_on", "voice_profile", "voice_volume", "voice_main", "voice_sub", "tts_engine", "kokoro_voice", "narrator_provider", "narrator_model", "narrator_api_key", "narrator_style"}


# ── Config ──

DEFAULTS = {
    "python3_path": "/usr/bin/python3",
    "effects_on": True, "effects_profile": "default", "effects_volume": 100,
    "voice_on": False, "voice_profile": "senior", "voice_volume": 100,
    "voice_main": "Tara", "voice_sub": "Aman",
    "tts_engine": "say", "kokoro_voice": "af_heart",
    "narrator_provider": "claude_cli", "narrator_model": "", "narrator_api_key": "",
    "narrator_style": "pair_programmer",
}


NARRATOR_PROVIDERS = {
    "claude_cli": {
        "name": "Claude Code CLI",
        "description": "Uses your existing Claude Code auth. No extra setup needed.",
        "needs_key": False,
        "default_model": "haiku",
        "models": ["haiku", "sonnet", "opus"],
    },
    "anthropic": {
        "name": "Anthropic API",
        "description": "Direct Claude API. Requires key from console.anthropic.com",
        "needs_key": True,
        "default_model": "claude-haiku-4-5-20251001",
        "models": [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-5-20250514",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
        ],
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Google AI. Requires key from aistudio.google.com",
        "needs_key": True,
        "default_model": "gemini-2.5-flash",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3-flash-preview",
            "gemini-3-pro",
            "gemini-3.1-flash-lite-preview",
            "gemini-3.1-pro-preview",
        ],
    },
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI API. Requires key from platform.openai.com",
        "needs_key": True,
        "default_model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
        ],
    },
    "ollama": {
        "name": "Ollama (local)",
        "description": "Local LLM via Ollama. No API key. Requires Ollama running.",
        "needs_key": False,
        "default_model": "qwen3.5:4b",
        "models": [
            "qwen3.5:4b",
            "qwen3.5:9b",
            "gemma3:4b",
            "gemma4:latest",
            "gemma4:26b",
        ],
    },
}

NARRATOR_STYLES = ["pair_programmer", "sports", "documentary", "noir", "haiku_poet"]

TTS_ENGINES = {
    "say": {"name": "macOS Say", "description": "Built-in macOS TTS. No setup needed."},
    "kokoro": {"name": "Kokoro", "description": "Local neural TTS via background daemon. Keeps model warm for fast speech."},
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
    config = dict(DEFAULTS)
    for path in (CONFIG_FILE, CONFIG_DEFAULTS):
        try:
            config.update(json.loads(path.read_text()))
            return config
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            log.warning("bad JSON in %s: %s", path, e)
            continue
    return config


def write_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log.info("wrote %s", CONFIG_FILE.name)


def get_python3():
    """Resolve the absolute path to python3 from config, with fallback."""
    config = read_config()
    p = config.get("python3_path", "")
    if p and os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    found = shutil.which("python3")
    return found or "/usr/bin/python3"


def read_phrases():
    for path in (PHRASES_FILE, PHRASES_DEFAULTS):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            log.warning("bad JSON in %s: %s", path, e)
            continue
    return {}


def write_phrases(data):
    PHRASES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log.info("wrote %s", PHRASES_FILE.name)


# ── Sound manifest ──

def read_sounds():
    """Read the sound manifest (structured profile/event → sound mappings)."""
    try:
        return json.loads(SOUNDS_FILE.read_text())
    except FileNotFoundError:
        log.error("sounds manifest not found: %s", SOUNDS_FILE)
        return {"effects": {}, "voice": {}}
    except json.JSONDecodeError as e:
        log.error("bad JSON in sounds manifest %s: %s", SOUNDS_FILE, e)
        return {"effects": {}, "voice": {}}


def _spec_origin(spec):
    """Derive display origin from a sound spec."""
    if "sox" in spec:
        return "generated"
    f = spec.get("file") or ""
    if not f:
        fs = spec.get("files") or spec.get("sequence")
        if fs:
            f = fs[0] if isinstance(fs[0], str) else (fs[0][0] if fs[0] else "")
    if f.startswith("/System/"):
        return "system"
    if f.endswith(".mp3"):
        return "sampled"
    if f.endswith(".aiff"):
        return "recorded"
    return "complex"


def _spec_label(spec):
    """Derive display label from a sound spec."""
    if "sox" in spec:
        args = spec["sox"]
        m = re.match(r'synth\s+[\d.]+\s+(\w+)', args)
        return f"synth {m.group(1)}" if m else args[:30]
    if "file" in spec:
        return spec["file"].rsplit("/", 1)[-1]
    if "files" in spec:
        return spec["files"][0]
    if "sequence" in spec:
        seq = spec["sequence"][0]
        return " + ".join(seq)
    return "..."


def _build_profile_view(profile_data):
    """Convert manifest profile to the status API format {event: {cmd, origin}}."""
    events = profile_data.get("events", {})
    return {
        ev: {"cmd": _spec_label(spec), "origin": _spec_origin(spec)}
        for ev, spec in events.items()
    }


def parse_effects_profiles():
    sounds = read_sounds()
    return {name: _build_profile_view(p) for name, p in sounds.get("effects", {}).items()}


def parse_voice_profiles():
    profiles = {}

    # Narration: built from phrases JSON
    phrases = read_phrases()
    narr_events = {}
    for ev in EVENTS:
        items = phrases.get(ev, [])
        if not items:
            continue
        if ev in DIALOGUE_EVENTS:
            if items and isinstance(items[0], list):
                narr_events[ev] = {
                    "cmd": f'say "{items[0][0]}" \u2192 say "{items[0][1]}"',
                    "origin": "tts",
                }
        else:
            preview = " | ".join(items[:3])
            if len(items) > 3:
                preview += " ..."
            narr_events[ev] = {"cmd": preview, "origin": "tts"}
    profiles["senior"] = narr_events

    # Narrator: LLM-powered commentary (no per-event config)
    profiles["narrator"] = {
        ev: {"cmd": "LLM narration", "origin": "api"}
        for ev in EVENTS
    }

    # Other voice profiles: from manifest
    sounds = read_sounds()
    for name, p in sounds.get("voice", {}).items():
        profiles[name] = _build_profile_view(p)

    return profiles


def get_voices():
    try:
        out = subprocess.check_output(["say", "-v", "?"], text=True, timeout=5)
        voices = []
        seen = set()
        for line in out.strip().split("\n"):
            m = re.match(r'^(.+?)\s+(\w{2}_\w{2})\s+#\s*(.*)', line)
            if m:
                name = m.group(1).strip()
                locale = m.group(2)
                if name not in seen:
                    seen.add(name)
                    voices.append({"name": name, "locale": locale})
        return voices
    except Exception as e:
        log.warning("could not list voices: %s", e)
        return []


def get_status():
    config = read_config()
    # Mask API key in status response
    api_key = config.get("narrator_api_key", "")
    if api_key:
        config["narrator_api_key"] = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"
    return {
        **config,
        "effects_profiles": parse_effects_profiles(),
        "voice_profiles": parse_voice_profiles(),
        "voice_profile_names": VOICE_PROFILES,
        "phrases": read_phrases(),
        "events": EVENTS,
        "dialogue_events": list(DIALOGUE_EVENTS),
        "voices": get_voices(),
        "tts_engines": TTS_ENGINES,
        "kokoro_voices": KOKORO_VOICES,
        "narrator_providers": NARRATOR_PROVIDERS,
        "narrator_styles": NARRATOR_STYLES,
    }


def _vol_str(vol_int):
    """Convert volume 0-100 to '0.XX' string (locale-safe)."""
    return f"{vol_int // 100}.{vol_int % 100:02d}"


def _resolve_file(spec, profile_data):
    """Resolve a file path from a sound spec, handling random selection."""
    if "file" in spec:
        f = spec["file"]
    elif "files" in spec:
        f = spec["files"][int(time.time() * 1000) % len(spec["files"])]
    else:
        return None
    if f.startswith("/"):
        return f
    d = profile_data.get("dir", "")
    return str(SND / d / f) if d else f


def _say_vol_cmd(voice, rate, phrase, vol):
    """Build a shell command for volume-controlled say (render to temp, play with afplay)."""
    safe = subprocess.list2cmdline([phrase])
    return (f't=$(mktemp /tmp/soundbar_say.XXXXXX.aiff);'
            f' say -v "{voice}" -r {rate} {safe} -o "$t"'
            f' && afplay -v {vol} "$t"; rm -f "$t"')


def _narrate_speak_cmd(phrase):
    """Build a shell command to speak a phrase via narrate.py --speak (Kokoro-aware)."""
    safe = subprocess.list2cmdline([phrase])
    py3 = subprocess.list2cmdline([get_python3()])
    return f'{py3} {subprocess.list2cmdline([str(SND / "narrate.py")])} --speak {safe}'


def _play_narration(event, config, vol):
    """Play narration voice profile (TTS from phrases.json)."""
    phrases = read_phrases()
    items = phrases.get(event, [])
    if not items:
        return
    idx = int(time.time() * 1000) % len(items)
    tts_engine = config.get("tts_engine", "say")
    main_v = config.get("voice_main", "Tara")
    sub_v = config.get("voice_sub", "Aman")

    if event in DIALOGUE_EVENTS and isinstance(items[idx], list):
        pair = items[idx]
        if event == "subagent_start":
            first_v, second_v, first_p, second_p = main_v, sub_v, pair[0], pair[1]
        else:
            first_v, second_v, first_p, second_p = sub_v, main_v, pair[0], pair[1]
        if tts_engine == "kokoro":
            cmd = (_narrate_speak_cmd(first_p)
                   + " && " + _narrate_speak_cmd(second_p))
        else:
            cmd = (_say_vol_cmd(first_v, 200, first_p, vol)
                   + " && sleep 0.2 && "
                   + _say_vol_cmd(second_v, 190, second_p, vol))
        subprocess.Popen(["bash", "-c", cmd],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        phrase = items[idx] if not isinstance(items[idx], list) else items[idx][0]
        if tts_engine == "kokoro":
            subprocess.Popen(
                [get_python3(), str(SND / "narrate.py"), "--speak", phrase],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["bash", "-c", _say_vol_cmd(main_v, 200, phrase, vol)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)


def _random_rate(spec):
    """Compute random playback rate from spec's rate range, or None."""
    r = spec.get("rate")
    if not r or len(r) != 2 or r[0] == r[1]:
        return None
    return random.uniform(r[0], r[1])


def play_profile_event(layer, profile, event):
    """Play a sound directly from the manifest — no shell script."""
    log.info("play %s/%s/%s", layer, profile, event)
    config = read_config()
    vol_key = "effects_volume" if layer == "effects" else "voice_volume"
    vol = _vol_str(config.get(vol_key, 100))

    # Narration: TTS from phrases.json, not in manifest
    if layer == "voice" and profile == "senior":
        _play_narration(event, config, vol)
        return

    sounds = read_sounds()
    profile_data = sounds.get(layer, {}).get(profile, {})
    spec = profile_data.get("events", {}).get(event)
    if not spec:
        log.warning("no sound spec for %s/%s/%s", layer, profile, event)
        return

    rate = _random_rate(spec)
    rate_args = ["-r", f"{rate:.3f}"] if rate else []

    if "file" in spec or "files" in spec:
        f = _resolve_file(spec, profile_data)
        if f:
            subprocess.Popen(["afplay", "-v", vol] + rate_args + [f],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    elif "sequence" in spec:
        seqs = spec["sequence"]
        seq = seqs[int(time.time() * 1000) % len(seqs)]
        gap = spec.get("gap", 0.15)
        d = profile_data.get("dir", "")
        rate_flag = f" -r {rate:.3f}" if rate else ""
        parts = []
        for i, fname in enumerate(seq):
            if i > 0:
                parts.append(f"sleep {gap}")
            f = str(SND / d / fname) if d and not fname.startswith("/") else fname
            parts.append(f'afplay -v {vol}{rate_flag} "{f}"')
        cmd = " && ".join(parts)
        subprocess.Popen(["bash", "-c", cmd],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    elif "sox" in spec:
        subprocess.Popen(["play", "-qn"] + spec["sox"].split() + ["vol", vol],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


# ── Integrations state ──

INTEGRATIONS_FILE = SND / "integrations.json"

# In-memory install progress (only while install is running)
_kokoro_install = {"status": "idle", "message": ""}


def read_integrations():
    try:
        return json.loads(INTEGRATIONS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_integration(key, value):
    data = read_integrations()
    data[key] = value
    INTEGRATIONS_FILE.write_text(json.dumps(data, indent=2) + "\n")
    log.info("wrote %s (%s=%s)", INTEGRATIONS_FILE.name, key, value)


def check_kokoro_installed():
    """Check if kokoro is actually importable. Fast path via integrations.json flag."""
    data = read_integrations()
    if data.get("kokoro_installed"):
        return True
    # Flag not set — verify by importing
    if not KOKORO_VENV_PY.exists():
        return False
    try:
        r = subprocess.run(
            [str(KOKORO_VENV_PY), "-c", "import kokoro"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            write_integration("kokoro_installed", True)
            return True
    except Exception:
        pass
    return False


# Kokoro requires Python >=3.9, <3.12 (PyTorch constraint).
KOKORO_PY_MIN = (3, 9)
KOKORO_PY_MAX = (3, 12)  # exclusive


def _get_python_version(python_path):
    """Get (major, minor) tuple for a Python binary, or None."""
    try:
        r = subprocess.run(
            [python_path, "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
            capture_output=True, text=True, timeout=5,
        )
        _dlog.debug("version check %s: rc=%s stdout=%r stderr=%r", python_path, r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            return (int(parts[0]), int(parts[1]))
    except Exception as e:
        _dlog.debug("version check %s: exception %s", python_path, e)
    return None


def _find_kokoro_python():
    """Find a Python >=3.9, <3.12 for Kokoro venv creation.

    Detection order:
    1. Direct binaries: python3.11, python3.10, python3.9 in PATH
    2. uv: can create venvs with specific Python versions
    3. pyenv: check installed versions
    4. conda: check for compatible environments

    Returns dict with keys:
      - ok: bool
      - python: str (absolute path to python binary)
      - method: str ("direct", "uv", "pyenv", "conda")
      - version: str (e.g. "3.11") — human-readable
      - message: str — describes what was found or what failed
    """
    _dlog.debug("=== _find_kokoro_python start ===")

    # Fast path: cached in integrations.json
    data = read_integrations()
    cached = data.get("kokoro_python")
    _dlog.debug("cached kokoro_python: %s", cached)
    if cached and isinstance(cached, dict) and cached.get("ok"):
        py = cached.get("python", "")
        _dlog.debug("cached python: %s exists=%s executable=%s", py, os.path.isfile(py) if py else False, os.access(py, os.X_OK) if py else False)
        if py and os.path.isfile(py) and os.access(py, os.X_OK):
            ver = _get_python_version(py)
            if ver and KOKORO_PY_MIN <= ver < KOKORO_PY_MAX:
                _dlog.debug("using cached python: %s (%s)", py, ver)
                return cached

    tried = []

    # 1. Direct binaries in PATH
    for minor in (11, 10, 9):
        name = f"python3.{minor}"
        path = shutil.which(name)
        if path:
            ver = _get_python_version(path)
            if ver and KOKORO_PY_MIN <= ver < KOKORO_PY_MAX:
                result = {
                    "ok": True, "python": path, "method": "direct",
                    "version": f"{ver[0]}.{ver[1]}",
                    "message": f"Using {name} from PATH",
                }
                write_integration("kokoro_python", result)
                return result
        tried.append(name)

    # 2. uv — install + find a compatible Python binary
    uv_bin = shutil.which("uv")
    _dlog.debug("uv binary: %s", uv_bin)
    if uv_bin:
        try:
            _dlog.debug("running: uv python install 3.11")
            r_install = subprocess.run(
                [uv_bin, "python", "install", "3.11"],
                capture_output=True, text=True, timeout=120,
            )
            _dlog.debug("uv python install: rc=%s stdout=%r stderr=%r", r_install.returncode, r_install.stdout.strip()[:200], r_install.stderr.strip()[:200])
            r = subprocess.run(
                [uv_bin, "python", "find", "3.11"],
                capture_output=True, text=True, timeout=10,
            )
            _dlog.debug("uv python find: rc=%s stdout=%r stderr=%r", r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
            if r.returncode == 0:
                raw_path = r.stdout.strip()
                py_path = os.path.realpath(raw_path)
                _dlog.debug("uv python path: raw=%s resolved=%s", raw_path, py_path)
                _dlog.debug("uv python exists=%s executable=%s", os.path.isfile(py_path), os.access(py_path, os.X_OK))
                ver = _get_python_version(py_path)
                if ver and KOKORO_PY_MIN <= ver < KOKORO_PY_MAX:
                    result = {
                        "ok": True, "python": py_path, "method": "uv",
                        "version": f"{ver[0]}.{ver[1]}",
                        "message": f"Using Python {ver[0]}.{ver[1]} via uv",
                    }
                    write_integration("kokoro_python", result)
                    return result
        except Exception:
            pass
    tried.append("uv")

    # 3. pyenv — check installed versions
    pyenv_bin = shutil.which("pyenv")
    if pyenv_bin:
        try:
            r = subprocess.run(
                [pyenv_bin, "versions", "--bare"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    ver_str = line.strip()
                    if not ver_str:
                        continue
                    parts = ver_str.split(".")
                    if len(parts) >= 2:
                        try:
                            ver = (int(parts[0]), int(parts[1]))
                        except ValueError:
                            continue
                        if KOKORO_PY_MIN <= ver < KOKORO_PY_MAX:
                            # Find the actual binary
                            r2 = subprocess.run(
                                [pyenv_bin, "root"],
                                capture_output=True, text=True, timeout=5,
                            )
                            if r2.returncode == 0:
                                pyenv_root = r2.stdout.strip()
                                py_path = os.path.join(pyenv_root, "versions", ver_str, "bin", "python3")
                                if os.path.isfile(py_path):
                                    result = {
                                        "ok": True, "python": py_path, "method": "pyenv",
                                        "version": ver_str,
                                        "message": f"Using Python {ver_str} from pyenv",
                                    }
                                    write_integration("kokoro_python", result)
                                    return result
        except Exception:
            pass
    tried.append("pyenv")

    # 4. conda — check for compatible environments
    conda_bin = shutil.which("conda")
    if conda_bin:
        try:
            r = subprocess.run(
                [conda_bin, "info", "--envs", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                info = json.loads(r.stdout)
                for env_path in info.get("envs", []):
                    py_path = os.path.join(env_path, "bin", "python3")
                    if os.path.isfile(py_path):
                        ver = _get_python_version(py_path)
                        if ver and KOKORO_PY_MIN <= ver < KOKORO_PY_MAX:
                            result = {
                                "ok": True, "python": py_path, "method": "conda",
                                "version": f"{ver[0]}.{ver[1]}",
                                "message": f"Using Python {ver[0]}.{ver[1]} from conda env",
                            }
                            write_integration("kokoro_python", result)
                            return result
        except Exception:
            pass
    tried.append("conda")

    # 5. Nothing found
    result = {
        "ok": False, "python": "", "method": "", "version": "",
        "message": (
            f"No compatible Python (3.9-3.11) found. "
            f"Checked: {', '.join(tried)}.\n"
            "Install one via:\n"
            "  brew install python@3.11\n"
            "  uv python install 3.11\n"
            "  pyenv install 3.11"
        ),
    }
    write_integration("kokoro_python", result)
    return result


def _run_kokoro_install():
    """Background thread: create venv + pip install kokoro soundfile."""
    _dlog.debug("=== _run_kokoro_install start ===")
    log.info("kokoro install started")
    _kokoro_install["status"] = "running"
    _kokoro_install["message"] = "Finding compatible Python (3.9-3.11)..."
    try:
        py_info = _find_kokoro_python()
        _dlog.debug("python detection result: %s", py_info)
        if not py_info["ok"]:
            _kokoro_install["status"] = "error"
            _kokoro_install["message"] = py_info["message"]
            log.error("kokoro install: no compatible Python found")
            return

        method = py_info["method"]
        _dlog.debug("install method: %s python: %s", method, py_info.get("python"))

        # Detect if "direct" python is actually uv-managed (symlink into uv store)
        py_real = os.path.realpath(py_info["python"])
        is_uv_python = "/uv/python/" in py_real or (method == "uv")
        _dlog.debug("python realpath: %s is_uv_python: %s", py_real, is_uv_python)

        if not KOKORO_VENV_PY.exists():
            _kokoro_install["message"] = f"Creating venv ({py_info['message']})..."
            log.info("kokoro install: creating venv via %s at %s", method, KOKORO_VENV)

            if is_uv_python and shutil.which("uv"):
                # uv-managed Python: use uv venv (handles standalone builds correctly)
                cmd = ["uv", "venv", "--python", py_info["python"], str(KOKORO_VENV)]
                _dlog.debug("venv cmd (uv): %s", cmd)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            else:
                cmd = [py_info["python"], "-m", "venv", str(KOKORO_VENV)]
                _dlog.debug("venv cmd: %s", cmd)
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

                # If ensurepip fails, retry without pip — standalone/uv Pythons lack it
                if result.returncode != 0 and "ensurepip" in result.stderr:
                    _dlog.debug("ensurepip failed, retrying with --without-pip")
                    # Clean up partial venv
                    import shutil as _sh
                    _sh.rmtree(str(KOKORO_VENV), ignore_errors=True)
                    cmd = [py_info["python"], "-m", "venv", "--without-pip", str(KOKORO_VENV)]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        is_uv_python = True  # force uv pip path for install step

            _dlog.debug("venv creation: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip()[:300], result.stderr.strip()[:300])

            if result.returncode != 0:
                _kokoro_install["status"] = "error"
                _kokoro_install["message"] = f"venv creation failed: {result.stderr.strip()[-200:]}"
                log.error("kokoro install: venv creation failed: %s", result.stderr.strip())
                _elog.error("venv creation failed: cmd=%s stderr=%s", cmd, result.stderr.strip())
                write_integration("kokoro_python", None)
                return

            if not KOKORO_VENV_PY.exists():
                _kokoro_install["status"] = "error"
                _kokoro_install["message"] = "venv created but bin/python3 not found."
                log.error("kokoro install: venv python missing after creation")
                # Dump venv directory contents for debugging
                try:
                    venv_bin = KOKORO_VENV / "bin"
                    if venv_bin.exists():
                        _dlog.debug("venv bin contents: %s", list(venv_bin.iterdir()))
                    else:
                        _dlog.debug("venv bin dir missing. venv contents: %s", list(KOKORO_VENV.iterdir()) if KOKORO_VENV.exists() else "venv dir missing")
                except Exception:
                    pass
                write_integration("kokoro_python", None)
                return

        # Dump pyvenv.cfg and venv python details for debugging
        pyvenv_cfg = KOKORO_VENV / "pyvenv.cfg"
        if pyvenv_cfg.exists():
            _dlog.debug("pyvenv.cfg contents:\n%s", pyvenv_cfg.read_text())
        else:
            _dlog.debug("pyvenv.cfg MISSING at %s", pyvenv_cfg)

        venv_py_real = os.path.realpath(str(KOKORO_VENV_PY))
        _dlog.debug("venv python: %s -> realpath: %s", KOKORO_VENV_PY, venv_py_real)

        # Verify venv python can start (catches broken pyvenv.cfg / standalone builds)
        verify = subprocess.run(
            [str(KOKORO_VENV_PY), "-c", "import sys; print('prefix:', sys.prefix, 'base:', sys.base_prefix, 'exec_prefix:', sys.exec_prefix)"],
            capture_output=True, text=True, timeout=10,
        )
        _dlog.debug("venv verify: rc=%s stdout=%r stderr=%r", verify.returncode, verify.stdout.strip()[:300], verify.stderr.strip()[:500])

        if verify.returncode != 0:
            _elog.error("venv python broken: stderr=%s", verify.stderr.strip()[:500])
            log.warning("kokoro install: venv python broken, attempting pyvenv.cfg repair")
            real_bin = os.path.dirname(os.path.realpath(py_info["python"]))
            _dlog.debug("repair target: home = %s (from python %s)", real_bin, py_info["python"])

            if pyvenv_cfg.exists():
                try:
                    original = pyvenv_cfg.read_text()
                    _dlog.debug("pyvenv.cfg BEFORE repair:\n%s", original)
                    lines = original.splitlines()
                    new_lines = []
                    for line in lines:
                        if line.startswith("home"):
                            new_lines.append(f"home = {real_bin}")
                        else:
                            new_lines.append(line)
                    pyvenv_cfg.write_text("\n".join(new_lines) + "\n")
                    _dlog.debug("pyvenv.cfg AFTER repair:\n%s", pyvenv_cfg.read_text())
                    log.info("kokoro install: pyvenv.cfg home rewritten to %s", real_bin)
                except Exception as e:
                    log.error("kokoro install: pyvenv.cfg repair failed: %s", e)
                    _elog.error("pyvenv.cfg repair failed: %s", e)

                # Re-verify after repair
                verify2 = subprocess.run(
                    [str(KOKORO_VENV_PY), "-c", "import sys; print('prefix:', sys.prefix, 'base:', sys.base_prefix)"],
                    capture_output=True, text=True, timeout=10,
                )
                _dlog.debug("venv re-verify: rc=%s stdout=%r stderr=%r", verify2.returncode, verify2.stdout.strip()[:300], verify2.stderr.strip()[:500])
                if verify2.returncode != 0:
                    _kokoro_install["status"] = "error"
                    _kokoro_install["message"] = (
                        "venv python is broken (can't import stdlib). "
                        "Try: uv python upgrade --reinstall"
                    )
                    log.error("kokoro install: venv python still broken after repair")
                    _elog.error("venv still broken after repair: stderr=%s", verify2.stderr.strip()[:500])
                    write_integration("kokoro_python", None)
                    return
            else:
                _kokoro_install["status"] = "error"
                _kokoro_install["message"] = "venv python is broken and pyvenv.cfg not found."
                log.error("kokoro install: pyvenv.cfg missing")
                _elog.error("pyvenv.cfg missing at %s", pyvenv_cfg)
                write_integration("kokoro_python", None)
                return

        # Install kokoro: uv pip for uv/standalone venvs (no pip inside), standard pip otherwise
        _kokoro_install["message"] = "Installing kokoro + dependencies (this may take a few minutes)..."
        if is_uv_python and shutil.which("uv"):
            log.info("kokoro install: uv pip install kokoro + soundfile")
            result = subprocess.run(
                ["uv", "pip", "install", "--python", str(KOKORO_VENV_PY), "kokoro", "soundfile"],
                capture_output=True, text=True, timeout=600,
            )
        else:
            # Ensure pip is available (ensurepip as fallback for edge cases)
            venv_pip = KOKORO_VENV / "bin" / "pip"
            if not venv_pip.exists():
                log.info("kokoro install: bootstrapping pip via ensurepip")
                subprocess.run(
                    [str(KOKORO_VENV_PY), "-m", "ensurepip", "--upgrade"],
                    capture_output=True, timeout=30,
                )
            log.info("kokoro install: pip install kokoro + soundfile")
            result = subprocess.run(
                [str(KOKORO_VENV_PY), "-m", "pip", "install", "-q", "kokoro", "soundfile"],
                capture_output=True, text=True, timeout=600,
            )
        _dlog.debug("pip install: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip()[:500], result.stderr.strip()[:500])
        if result.returncode != 0:
            _kokoro_install["status"] = "error"
            _kokoro_install["message"] = f"pip install failed: {result.stderr.strip()[-200:]}"
            log.error("kokoro install: pip install failed: %s", result.stderr.strip()[-200:])
            _elog.error("pip install failed: rc=%s stderr=%s", result.returncode, result.stderr.strip()[:1000])
            return

        log.info("kokoro install: verifying import")
        result = subprocess.run(
            [str(KOKORO_VENV_PY), "-c", "import kokoro; print('kokoro ok')"],
            capture_output=True, text=True, timeout=10,
        )
        _dlog.debug("import verify: rc=%s stdout=%r stderr=%r", result.returncode, result.stdout.strip(), result.stderr.strip()[:500])
        if result.returncode != 0:
            _kokoro_install["status"] = "error"
            _kokoro_install["message"] = "Install succeeded but import failed."
            log.error("kokoro install: import verification failed")
            _elog.error("import verification failed: stderr=%s", result.stderr.strip()[:500])
            return

        write_integration("kokoro_installed", True)
        _kokoro_install["status"] = "done"
        _kokoro_install["message"] = "Installed. Daemon will auto-start on first use."
        log.info("kokoro install completed successfully")
        _dlog.debug("=== _run_kokoro_install success ===")

    except subprocess.TimeoutExpired as e:
        _kokoro_install["status"] = "error"
        _kokoro_install["message"] = "Install timed out."
        _elog.error("install timed out: %s", e)
        log.error("kokoro install timed out")
    except Exception as e:
        _kokoro_install["status"] = "error"
        _kokoro_install["message"] = str(e)
        log.error("kokoro install failed: %s", e)
        _elog.error("install exception: %s", e, exc_info=True)


# ── HTTP Server ──

class ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True


class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/ui"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(UI_FILE.read_bytes())

            elif path == "/api/status":
                self.json_response(get_status())

            else:
                self.send_error(404)
        except Exception as e:
            log.error("GET %s failed: %s", path, e)
            self.send_error(500)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        try:
            self._handle_post(path, body)
        except Exception as e:
            log.error("POST %s failed: %s", path, e)
            self.json_response({"ok": False, "error": str(e)})

    def _handle_post(self, path, body):
        if path == "/api/config":
            config = read_config()
            updates = {k: v for k, v in body.items() if k in CONFIG_KEYS}
            # Log profile changes
            for k in ("effects_profile", "voice_profile"):
                if k in updates and updates[k] != config.get(k):
                    log.info("config %s: %s -> %s", k, config.get(k), updates[k])
            # Log narrator setting changes
            for k in ("narrator_provider", "narrator_model", "narrator_style", "tts_engine"):
                if k in updates and updates[k] != config.get(k):
                    log.info("config %s: %s -> %s", k, config.get(k), updates[k])
            config.update(updates)
            write_config(config)
            self.json_response({"ok": True, **config})

        elif path == "/api/phrases":
            event = body.get("event", "")
            phrases = body.get("phrases", [])
            if event in EVENTS:
                data = read_phrases()
                data[event] = phrases
                write_phrases(data)
                self.json_response({"ok": True})
            else:
                self.json_response({"ok": False, "error": "Unknown event"})

        elif path == "/api/play":
            layer = body.get("layer", "effects")
            profile = body.get("profile", "")
            event = body.get("event", "")
            play_profile_event(layer, profile, event)
            self.json_response({"ok": True})

        elif path == "/api/say":
            voice = body.get("voice", "")
            phrase = body.get("phrase", "")
            rate = body.get("rate", 200)
            engine = body.get("engine", "say")
            if voice and phrase:
                if engine == "kokoro":
                    # Use narrate.py --speak for Kokoro TTS
                    subprocess.Popen(
                        [get_python3(), str(SND / "narrate.py"), "--speak", phrase],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        ["say", "-v", voice, "-r", str(rate), phrase],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            self.json_response({"ok": True})

        elif path == "/api/tts-check":
            try:
                result = subprocess.run(
                    [get_python3(), str(SND / "narrate.py"), "--check-tts"],
                    capture_output=True, text=True, timeout=10,
                )
                self.json_response(json.loads(result.stdout) if result.stdout.strip() else {"ok": False, "message": "No output"})
            except Exception as e:
                log.error("tts-check failed: %s", e)
                self.json_response({"ok": False, "message": str(e)})

        elif path == "/api/kokoro-install":
            if check_kokoro_installed():
                self.json_response({"ok": True, "status": "done", "installed": True})
            elif _kokoro_install["status"] == "running":
                self.json_response({"ok": True, "status": "running", "message": _kokoro_install["message"]})
            else:
                threading.Thread(target=_run_kokoro_install, daemon=True).start()
                self.json_response({"ok": True, "status": "running", "message": "Starting install..."})

        elif path == "/api/kokoro-status":
            py_info = _find_kokoro_python()
            if _kokoro_install["status"] == "running":
                self.json_response({"ok": True, "status": "running", "message": _kokoro_install["message"], "installed": False, "python_info": py_info})
            elif _kokoro_install["status"] == "error":
                self.json_response({"ok": False, "status": "error", "message": _kokoro_install["message"], "installed": False, "python_info": py_info})
            else:
                installed = check_kokoro_installed()
                self.json_response({"ok": installed, "status": "done" if installed else "idle", "installed": installed, "python_info": py_info})

        elif path == "/api/narrator-check":
            try:
                result = subprocess.run(
                    [get_python3(), str(SND / "narrate.py"), "--check"],
                    capture_output=True, text=True, timeout=15,
                )
                self.json_response(json.loads(result.stdout) if result.stdout.strip() else {"ok": False, "message": "No output"})
            except subprocess.TimeoutExpired:
                log.error("narrator-check timed out")
                self.json_response({"ok": False, "message": "Connection check timed out"})
            except Exception as e:
                log.error("narrator-check failed: %s", e)
                self.json_response({"ok": False, "message": str(e)})

        elif path == "/api/narrator-test":
            event = body.get("event", "edit")
            test_context = json.dumps({
                "hook_event_name": "PreToolUse",
                "tool_name": {"edit": "Edit", "bash": "Bash", "search": "Grep", "stop": "Stop"}.get(event, "Edit"),
                "tool_input": {
                    "edit": {"file_path": "/src/components/App.tsx", "old_string": "const x = 1", "new_string": "const x = 2"},
                    "bash": {"command": "npm test", "description": "Running test suite"},
                    "search": {"pattern": "handleSubmit", "path": "src/"},
                }.get(event, {"file_path": "/src/app.ts"}),
            })
            try:
                result = subprocess.run(
                    [get_python3(), str(SND / "narrate.py"), "--dry-run"],
                    input=test_context, capture_output=True, text=True, timeout=15,
                )
                text = result.stdout.strip()
                if text:
                    # Speak using narrate.py --speak (respects tts_engine config)
                    subprocess.Popen(
                        [get_python3(), str(SND / "narrate.py"), "--speak", text],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.json_response({"ok": True, "text": text})
                else:
                    stderr = result.stderr.strip()
                    self.json_response({"ok": False, "text": "", "error": stderr or "No narration generated"})
            except subprocess.TimeoutExpired:
                log.error("narrator-test timed out")
                self.json_response({"ok": False, "text": "", "error": "Narration timed out"})
            except Exception as e:
                log.error("narrator-test failed: %s", e)
                self.json_response({"ok": False, "text": "", "error": str(e)})

        else:
            self.send_error(404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            try:
                return json.loads(self.rfile.read(length))
            except json.JSONDecodeError as e:
                log.error("bad JSON in request body: %s", e)
                return {}
        return {}

    def json_response(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def _setup_file_loggers():
    """Set up debug.log and error.log in the soundbar directory."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # debug.log — everything (DEBUG+)
    dh = logging.FileHandler(SND / "debug.log", mode="a")
    dh.setLevel(logging.DEBUG)
    dh.setFormatter(fmt)
    _dlog.addHandler(dh)
    _dlog.setLevel(logging.DEBUG)
    # error.log — errors only
    eh = logging.FileHandler(SND / "error.log", mode="a")
    eh.setLevel(logging.ERROR)
    eh.setFormatter(fmt)
    _elog.addHandler(eh)
    _elog.setLevel(logging.ERROR)


def main():
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
    _setup_file_loggers()
    port = int(os.environ.get("PORT", 8111))
    server = ReuseHTTPServer(("127.0.0.1", port), Handler)
    print(f"Soundbar: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\nStopped.")


if __name__ == "__main__":
    main()
