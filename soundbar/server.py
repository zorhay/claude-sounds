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

from integrations import kokoro

log = logging.getLogger("soundbar")

SND = Path(__file__).parent
UI_FILE = SND / "ui.html"
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"
PHRASES_FILE = SND / "phrases.json"
PHRASES_DEFAULTS = SND / "phrases.defaults.json"
STYLES_FILE = SND / "narrator_styles.json"
STYLES_DEFAULTS = SND / "narrator_styles.defaults.json"
SOUNDS_FILE = SND / "sounds.json"

EVENTS = [
    "session_start", "edit", "bash", "search",
    "permission", "error", "subagent_start",
    "subagent_stop", "compact", "stop",
]

DIALOGUE_EVENTS = {"subagent_start", "subagent_stop"}
VOICE_PROFILES = ["senior", "narrator", "generals"]

CONFIG_KEYS = {"python3_path", "effects_on", "effects_profile", "effects_volume", "voice_on", "voice_profile", "voice_volume", "voice_main", "voice_sub", "tts_engine", "kokoro_voice", "narrator_provider", "narrator_model", "narrator_api_key", "narrator_style", "narrator_deep_context"}


# ── Config ──

DEFAULTS = {
    "python3_path": "/usr/bin/python3",
    "effects_on": True, "effects_profile": "default", "effects_volume": 100,
    "voice_on": False, "voice_profile": "senior", "voice_volume": 100,
    "voice_main": "Tara", "voice_sub": "Aman",
    "tts_engine": "say", "kokoro_voice": "af_heart",
    "narrator_provider": "claude_cli", "narrator_model": "", "narrator_api_key": "",
    "narrator_style": "pair_programmer",
    "narrator_deep_context": False,
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

STYLE_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def read_styles():
    """Read narrator styles. User file overrides defaults when present."""
    for path in (STYLES_FILE, STYLES_DEFAULTS):
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            log.warning("bad JSON in %s: %s", path, e)
            continue
    return {}


def read_default_styles():
    try:
        data = json.loads(STYLES_DEFAULTS.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_styles(data):
    STYLES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log.info("wrote %s", STYLES_FILE.name)


def _ensure_user_styles():
    """Seed the user styles file from defaults on first write."""
    if STYLES_FILE.exists():
        return read_styles()
    defaults = read_default_styles()
    write_styles(defaults)
    return defaults

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
        "narrator_styles": read_styles(),
        "narrator_styles_defaults": read_default_styles(),
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
            for k in ("narrator_provider", "narrator_model", "narrator_style", "narrator_deep_context", "tts_engine"):
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
                    # Verify kokoro daemon is reachable before speaking
                    kokoro_sock = SND / "kokoro.sock"
                    if kokoro_sock.exists():
                        subprocess.Popen(
                            [get_python3(), str(SND / "narrate.py"), "--speak", phrase],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        self.json_response({"ok": True})
                    else:
                        # Try to start daemon, but report if kokoro is unavailable
                        result = subprocess.run(
                            [get_python3(), str(SND / "narrate.py"), "--check-tts"],
                            capture_output=True, text=True, timeout=15,
                        )
                        tts_ok = False
                        try:
                            tts_ok = json.loads(result.stdout).get("ok", False) if result.stdout.strip() else False
                        except Exception:
                            pass
                        if tts_ok:
                            subprocess.Popen(
                                [get_python3(), str(SND / "narrate.py"), "--speak", phrase],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            self.json_response({"ok": True})
                        else:
                            log.warning("kokoro not available for /api/say, engine=%s", engine)
                            self.json_response({"ok": False, "error": "Kokoro not available", "fallback": "say"})
                else:
                    subprocess.Popen(
                        ["say", "-v", voice, "-r", str(rate), phrase],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.json_response({"ok": True})
            else:
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
            if kokoro.is_installed():
                self.json_response({"ok": True, "status": "done", "installed": True})
            elif kokoro.progress["status"] == "running":
                self.json_response({"ok": True, "status": "running", "message": kokoro.progress["message"]})
            else:
                threading.Thread(target=kokoro.install, daemon=True).start()
                self.json_response({"ok": True, "status": "running", "message": "Starting install..."})

        elif path == "/api/kokoro-status":
            self.json_response(kokoro.status())

        elif path == "/api/narrator-style":
            style_id = (body.get("id") or "").strip().lower()
            label = (body.get("label") or "").strip()
            prompt = (body.get("prompt") or "").strip()
            original = (body.get("original_id") or "").strip().lower() or style_id
            if not STYLE_ID_RE.match(style_id):
                self.json_response({"ok": False, "error": "id must be lowercase letters, digits, underscore (1-40 chars)"})
                return
            if not label or not prompt:
                self.json_response({"ok": False, "error": "label and prompt are required"})
                return
            styles = _ensure_user_styles()
            # Rename case: remove old key if id changed
            if original and original != style_id and original in styles:
                styles.pop(original, None)
            styles[style_id] = {"label": label, "prompt": prompt}
            write_styles(styles)
            # If caller is currently using a renamed style, update config pointer
            if original and original != style_id:
                config = read_config()
                if config.get("narrator_style") == original:
                    config["narrator_style"] = style_id
                    write_config(config)
            self.json_response({"ok": True, "styles": styles})

        elif path == "/api/narrator-style-delete":
            style_id = (body.get("id") or "").strip().lower()
            if not style_id:
                self.json_response({"ok": False, "error": "id required"})
                return
            styles = _ensure_user_styles()
            if style_id not in styles:
                self.json_response({"ok": False, "error": f"unknown style: {style_id}"})
                return
            if len(styles) <= 1:
                self.json_response({"ok": False, "error": "cannot delete the last style"})
                return
            styles.pop(style_id, None)
            write_styles(styles)
            # If deleted style was selected, fall back to first remaining
            config = read_config()
            if config.get("narrator_style") == style_id:
                config["narrator_style"] = next(iter(styles))
                write_config(config)
            self.json_response({"ok": True, "styles": styles})

        elif path == "/api/narrator-style-reset":
            # Restore defaults, overwriting user customizations
            defaults = read_default_styles()
            if not defaults:
                self.json_response({"ok": False, "error": "no defaults available"})
                return
            write_styles(defaults)
            self.json_response({"ok": True, "styles": defaults})

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


def main():
    # Console: INFO only (no DEBUG spam in terminal)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logging.root.addHandler(console)
    logging.root.setLevel(logging.DEBUG)
    # Initialize integration file loggers (debug.log / error.log)
    from integrations import _setup_loggers
    _setup_loggers()
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
