#!/usr/bin/env python3
"""Soundbar — Control panel HTTP server for Claude Code sound system.

API:
  GET  /              → ui.html
  GET  /api/status    → full state (config + profiles + phrases + voices)
  POST /api/config    → update config (any subset of keys)
  POST /api/phrases   → update phrases for one event
  POST /api/play      → play a specific profile+event sound directly
  POST /api/say       → preview a TTS voice
"""

import json
import os
import re
import subprocess
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

SND = Path(__file__).parent
PLAY_SOUND = SND / "play.sh"
UI_FILE = SND / "ui.html"
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"
PHRASES_FILE = SND / "phrases.json"
PHRASES_DEFAULTS = SND / "phrases.defaults.json"

EVENTS = [
    "session_start", "edit", "bash", "search",
    "permission", "error", "subagent_start",
    "subagent_stop", "compact", "stop",
]

DIALOGUE_EVENTS = {"subagent_start", "subagent_stop"}
VOICE_PROFILES = ["narration", "generals"]

CONFIG_KEYS = {"effects_on", "effects_profile", "effects_volume", "voice_on", "voice_profile", "voice_volume", "voice_main", "voice_sub"}


# ── Config ──

DEFAULTS = {
    "effects_on": True, "effects_profile": "default", "effects_volume": 100,
    "voice_on": False, "voice_profile": "narration", "voice_volume": 100,
    "voice_main": "Tara", "voice_sub": "Aman",
}


def read_config():
    config = dict(DEFAULTS)
    for path in (CONFIG_FILE, CONFIG_DEFAULTS):
        try:
            config.update(json.loads(path.read_text()))
            return config
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return config


def write_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_phrases():
    for path in (PHRASES_FILE, PHRASES_DEFAULTS):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def write_phrases(data):
    PHRASES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── Origin detection ──

def detect_origin(cmd):
    """Detect the origin type of a sound command."""
    if not cmd or cmd == "(complex)":
        return "complex"
    if "afplay /System/" in cmd:
        return "system"
    if "afplay" in cmd and ".mp3" in cmd:
        return "sampled"
    if "afplay" in cmd and ".aiff" in cmd:
        return "recorded"
    if "play -qn" in cmd or "play -q -n" in cmd:
        return "generated"
    if "say -v" in cmd:
        return "tts"
    return "complex"


# ── Profile parsing ──

def _parse_section(script):
    profiles = {}
    profile_pattern = re.compile(
        r'^\s{4}(\w[\w-]*)\)\s*\n(.*?)^\s{6};;',
        re.MULTILINE | re.DOTALL,
    )

    for m in profile_pattern.finditer(script):
        name = m.group(1)
        block = m.group(2)
        if name in ("*",):
            continue

        events = {}

        event_pattern = re.compile(
            r'^\s+(\w+)\)\s*\n?(.*?)(?=^\s+\w+\)|^\s+esac)',
            re.MULTILINE | re.DOTALL,
        )
        for em in event_pattern.finditer(block):
            ename = em.group(1)
            ebody = em.group(2).strip()
            if ename == "esac" or ename not in EVENTS:
                continue
            lines = []
            for line in ebody.split("\n"):
                line = line.strip().rstrip(";").strip()
                if line and not line.startswith("#"):
                    lines.append(line)
            cmd = " ".join(lines) if lines else "(complex)"
            events[ename] = {"cmd": cmd, "origin": detect_origin(cmd)}

        single_pattern = re.compile(r'^\s+(\w+)\)\s+(.+?)\s*;;', re.MULTILINE)
        for sm in single_pattern.finditer(block):
            ename = sm.group(1)
            if ename not in events and ename in EVENTS:
                cmd = sm.group(2).strip()
                events[ename] = {"cmd": cmd, "origin": detect_origin(cmd)}

        profiles[name] = events

    return profiles


def parse_effects_profiles():
    try:
        full = PLAY_SOUND.read_text()
    except FileNotFoundError:
        return {}
    marker = "# LAYER 2: EFFECTS"
    idx = full.find(marker)
    return _parse_section(full[idx:] if idx >= 0 else full)


def parse_voice_profiles():
    profiles = {}

    # Narration: built from phrases JSON, not from play.sh
    phrases = read_phrases()
    narr_events = {}
    for ev in EVENTS:
        items = phrases.get(ev, [])
        if not items:
            continue
        if ev in DIALOGUE_EVENTS:
            # Dialogue: show first pair as example
            if items and isinstance(items[0], list):
                narr_events[ev] = {
                    "cmd": f'say "{items[0][0]}" → say "{items[0][1]}"',
                    "origin": "tts",
                }
        else:
            # Simple: show phrases
            preview = " | ".join(items[:3])
            if len(items) > 3:
                preview += " ..."
            narr_events[ev] = {"cmd": preview, "origin": "tts"}
    profiles["narration"] = narr_events

    # Other voice profiles: parse from play.sh
    try:
        full = PLAY_SOUND.read_text()
    except FileNotFoundError:
        return profiles
    start = full.find("# LAYER 1: VOICE")
    end = full.find("# LAYER 2: EFFECTS")
    if start < 0:
        return profiles
    section = full[start:end] if end > start else full[start:]
    parsed = _parse_section(section)
    for name, events in parsed.items():
        if name != "narration":
            profiles[name] = events

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
    except Exception:
        return []


def get_status():
    config = read_config()
    return {
        **config,
        "effects_profiles": parse_effects_profiles(),
        "voice_profiles": parse_voice_profiles(),
        "voice_profile_names": VOICE_PROFILES,
        "phrases": read_phrases(),
        "events": EVENTS,
        "dialogue_events": list(DIALOGUE_EVENTS),
        "voices": get_voices(),
    }


def play_profile_event(layer, profile, event):
    """Play a specific profile's sound by running play.sh with overrides."""
    env = os.environ.copy()
    env["FORCE_LAYER"] = layer
    if layer == "effects":
        env["FORCE_EFFECTS_PROFILE"] = profile
    else:
        env["FORCE_VOICE_PROFILE"] = profile
    subprocess.Popen(
        [str(PLAY_SOUND), event],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


# ── HTTP Server ──

class ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True


class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/ui"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(UI_FILE.read_bytes())

        elif path == "/api/status":
            self.json_response(get_status())

        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()

        if path == "/api/config":
            config = read_config()
            config.update({k: v for k, v in body.items() if k in CONFIG_KEYS})
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
            if voice and phrase:
                subprocess.Popen(
                    ["say", "-v", voice, "-r", str(rate), phrase],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self.json_response({"ok": True})

        else:
            self.send_error(404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
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
