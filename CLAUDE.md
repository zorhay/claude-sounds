# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Audio feedback plugin for Claude Code. Two independent, mixable layers:
- **Effects layer** — sound profiles triggered by hook events (12 profiles)
- **Voice layer** — spoken lines via TTS or pre-rendered audio (2 profiles)

## Repo → Install mapping

`soundbar/` maps 1:1 to `~/.claude/soundbar/`. Install = copy. No restructuring.

```
soundbar/                          →  ~/.claude/soundbar/
├── play.sh                        →  Sound engine (hooks call this)
├── switch.sh                      →  CLI control
├── panel.sh                       →  Control panel (runs server, opens browser)
├── server.py                      →  Panel HTTP backend (port 8111)
├── ui.html                        →  Panel frontend (mixer layout)
├── uninstall.sh                   →  Uninstaller (also symlinked at repo root)
├── config.defaults.json           →  Default settings (shipped)
├── phrases.defaults.json          →  Default narration phrases (shipped)
└── sounds/{paper,construction,generals}/  →  Audio assets
```

User files created on install (never overwritten): `config.json`, `phrases.json`.

## Architecture

**Event flow:** Claude Code hook → `soundbar/play.sh <event>` → Layer 1 (voice) + Layer 2 (effects) in parallel.

**Config:** Single `config.json` (booleans + strings) read by one `jq` call.

**Panel lifecycle:** `panel.sh` runs `server.py` in foreground, opens browser. Ctrl+C stops it. Heartbeat watchdog auto-shuts down if browser tab closes. No dangling processes.

**Install/Uninstall:** DRY operations list — same list drives `--dry-run` preview and actual execution.
- `install.sh` — checks deps, copies files, creates user config, injects hooks (backup + validate)
- `uninstall.sh` — stops server, removes hooks surgically, removes files (preserves user config unless `--purge`)

## Key conventions

- `play.sh` has two marked sections: `LAYER 1: VOICE` and `LAYER 2: EFFECTS`. Each is nested case (outer = profile, inner = event).
- Effects profiles alphabetically ordered.
- All 10 events: `stop`, `edit`, `bash`, `search`, `permission`, `error`, `subagent_start`, `subagent_stop`, `session_start`, `compact`.
- Web UI auto-discovers profiles by regex-parsing `play.sh` (via `parse_effects_profiles()` in `server.py`).
- Hook tag: any hook containing `soundbar/play.sh` is ours.
- `server.py` uses unified `/api/config` POST — send any subset of keys.
- `/api/play` takes `{layer, profile, event}` and plays that specific profile's sound directly.

## Adding a new effects profile

1. Add a case block in `LAYER 2: EFFECTS` section of `play.sh` (alphabetical order)
2. For sampled: create `sounds/<profile>/`, use `afplay`
3. For sox-generated: use `play -qn synth ...`
4. Add the profile name to `EFFECTS_PROFILES` in `switch.sh`
5. Profile auto-appears in the web UI

## Adding a new voice profile

1. Add a case block in `LAYER 1: VOICE` section of `play.sh`
2. Add the profile name to `VOICE_PROFILES` in `server.py`

## Testing

```bash
~/.claude/soundbar/play.sh stop        # play a single event
~/.claude/soundbar/switch.sh           # show current status
~/.claude/soundbar/panel.sh            # open control panel
```

## Dependencies

- `jq` — required (config reading, hook injection)
- `sox` — for generated effects profiles (`play` command)
- `afplay` / `say` — macOS built-ins for sampled playback and TTS
- `python3` — for web control panel
