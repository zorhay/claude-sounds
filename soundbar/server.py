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
import logging
import os
import re
import subprocess
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("soundbar")

SND = Path(__file__).parent
UI_FILE = SND / "ui.html"
CONFIG_FILE = SND / "config.json"
CONFIG_DEFAULTS = SND / "config.defaults.json"
PHRASES_FILE = SND / "phrases.json"
PHRASES_DEFAULTS = SND / "phrases.defaults.json"
SOUNDS_FILE = SND / "sounds.json"

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
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            log.warning("bad JSON in %s: %s", path, e)
            continue
    return config


def write_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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
        pair = spec["sequence"][0]
        return f"{pair[0]} + {pair[1]}"
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
    profiles["narration"] = narr_events

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


def _play_narration(event, config, vol):
    """Play narration voice profile (TTS from phrases.json)."""
    phrases = read_phrases()
    items = phrases.get(event, [])
    if not items:
        return
    idx = int(time.time() * 1000) % len(items)
    main_v = config.get("voice_main", "Tara")
    sub_v = config.get("voice_sub", "Aman")

    if event in DIALOGUE_EVENTS and isinstance(items[idx], list):
        pair = items[idx]
        if event == "subagent_start":
            first_v, second_v, first_p, second_p = main_v, sub_v, pair[0], pair[1]
        else:
            first_v, second_v, first_p, second_p = sub_v, main_v, pair[0], pair[1]
        cmd = (_say_vol_cmd(first_v, 200, first_p, vol)
               + " && sleep 0.2 && "
               + _say_vol_cmd(second_v, 190, second_p, vol))
        subprocess.Popen(["bash", "-c", cmd],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        phrase = items[idx] if not isinstance(items[idx], list) else items[idx][0]
        subprocess.Popen(["bash", "-c", _say_vol_cmd(main_v, 200, phrase, vol)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def play_profile_event(layer, profile, event):
    """Play a sound directly from the manifest — no shell script."""
    config = read_config()
    vol_key = "effects_volume" if layer == "effects" else "voice_volume"
    vol = _vol_str(config.get(vol_key, 100))

    # Narration: TTS from phrases.json, not in manifest
    if layer == "voice" and profile == "narration":
        _play_narration(event, config, vol)
        return

    sounds = read_sounds()
    profile_data = sounds.get(layer, {}).get(profile, {})
    spec = profile_data.get("events", {}).get(event)
    if not spec:
        log.warning("no sound spec for %s/%s/%s", layer, profile, event)
        return

    if "file" in spec or "files" in spec:
        f = _resolve_file(spec, profile_data)
        if f:
            subprocess.Popen(["afplay", "-v", vol, f],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    elif "sequence" in spec:
        pairs = spec["sequence"]
        pair = pairs[int(time.time() * 1000) % len(pairs)]
        gap = spec.get("gap", 0.15)
        d = profile_data.get("dir", "")
        files = [str(SND / d / f) for f in pair]
        cmd = f'afplay -v {vol} "{files[0]}" && sleep {gap} && afplay -v {vol} "{files[1]}"'
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
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)
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
