#!/usr/bin/env python3
"""Share a short playback budget across concurrent hook processes.

The first event plays immediately. Suppressed events are dropped, never queued,
so a tool burst cannot leave a backlog of stale audio. Preview playback bypasses
this gate. The state contains hashed identifiers and monotonic timestamps only.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


IMPORTANT = {"permission", "error", "stop", "interrupt", "git_commit", "git_push"}
DEFAULTS = {"sound_spacing_on": True, "effects_cooldown_ms": 750, "voice_cooldown_ms": 3000}


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def milliseconds(value, default):
    try:
        return max(0, min(10000, float(value))) / 1000
    except (TypeError, ValueError):
        return default / 1000


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def acquire_lock(lock, timeout=.1):
    """Bound contention so a stalled gate cannot indefinitely delay a cue."""
    deadline = time.clock_gettime(time.CLOCK_MONOTONIC) + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = deadline - time.clock_gettime(time.CLOCK_MONOTONIC)
            if remaining <= 0:
                raise TimeoutError("Playback state lock is busy")
            time.sleep(min(.005, remaining))


def admit(state, *, layer, session, profile, event, sound, cooldown, now):
    """Decide and reserve playback while the caller holds the shared lock."""
    important = event in IMPORTANT
    # Reused clips across routine event categories share a cooldown. Important
    # cues have their own key so an earlier routine sound cannot mask them.
    identity = event if important else sound
    key = fingerprint([layer, session, profile, identity])
    repeat_gap = min(cooldown, .5 if layer == "effects" else 1) if important else cooldown
    previous = state.get(key)
    if isinstance(previous, (int, float)) and 0 <= now - previous < repeat_gap:
        return False
    budget = "budget:" + layer
    spacing = min(cooldown, .15 if layer == "effects" else 1.5)
    last = state.get(budget)
    if not important and isinstance(last, (int, float)) and 0 <= now - last < spacing:
        return False
    state[key] = now
    state[budget] = now
    return True


def gate(soundbar, event, layers, data, state_dir=None):
    """Return the allowed layers; missing mappings don't consume the budget."""
    settings = {**DEFAULTS, **read_json(soundbar / "config.defaults.json"), **read_json(soundbar / "config.json")}
    if settings.get("sound_spacing_on") is False:
        return {layer: enabled for layer, (enabled, _) in layers.items()}
    sounds = read_json(soundbar / "sounds.json")
    phrases = {**read_json(soundbar / "phrases.defaults.json"), **read_json(soundbar / "phrases.json")}
    candidates = {}
    for layer, (enabled, profile) in layers.items():
        if not enabled:
            continue
        if layer == "voice" and profile == "narrator":
            sound = {"narrator_event": event}
        elif layer == "voice" and profile == "senior":
            sound = phrases.get(event)
        else:
            sound = sounds.get(layer, {}).get(profile, {}).get("events", {}).get(event)
        if sound:
            candidates[layer] = (profile, sound)
    allowed = {layer: False for layer in layers}
    if not candidates:
        return allowed
    if state_dir is None:
        name = fingerprint(str(soundbar.resolve()))[:16]
        state_dir = Path(tempfile.gettempdir()) / f"soundbar-playback-{os.getuid()}-{name}"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = str(data.get("session_id") or data.get("cwd") or "manual")
    # Separate lock inode stays stable while the JSON file is atomically replaced.
    with (state_dir / "lock").open("a") as lock:
        acquire_lock(lock)
        # Python 3.9 on macOS gives time.monotonic() a process-local origin.
        # CLOCK_MONOTONIC is shared by processes, which this gate requires.
        now = time.clock_gettime(time.CLOCK_MONOTONIC)
        path = state_dir / "state.json"
        state = {k: v for k, v in read_json(path).items()
                 if isinstance(v, (int, float)) and 0 <= now - v < 60}
        for layer, (profile, sound) in candidates.items():
            cooldown = milliseconds(settings.get(layer + "_cooldown_ms"), DEFAULTS[layer + "_cooldown_ms"])
            allowed[layer] = admit(state, layer=layer, session=session, profile=profile,
                                   event=event, sound=sound, cooldown=cooldown, now=now)
        temporary = state_dir / "state.tmp"
        temporary.write_text(json.dumps(state))
        os.replace(temporary, path)
    return allowed


def main():
    soundbar, event, effects_on, effects_profile, voice_on, voice_profile = sys.argv[1:]
    original = {"effects": (effects_on == "on", effects_profile), "voice": (voice_on == "on", voice_profile)}
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            data = {}
    except (ValueError, OSError):
        data = {}
    try:
        allowed = gate(Path(soundbar), event, original, data)
    except (OSError, ValueError, TypeError, AttributeError):
        # A state-file issue must never disable audio or affect the agent loop.
        allowed = {layer: enabled for layer, (enabled, _) in original.items()}
    print(" ".join("on" if allowed[layer] else "off" for layer in ("effects", "voice")))


if __name__ == "__main__":
    main()
