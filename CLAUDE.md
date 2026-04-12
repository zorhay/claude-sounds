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
├── sounds.json                    →  Sound manifest (single source of truth)
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

**Event flow:** Claude Code hook → `soundbar/play.sh <event>` → reads `sounds.json` → Layer 1 (voice) + Layer 2 (effects) in parallel.

**Sound manifest:** `sounds.json` is the single source of truth for all sound mappings. Both `play.sh` (hooks) and `server.py` (UI) read it. Three spec types: `file`/`files` (sampled), `sox` (generated), `sequence` (multi-file). Narration voice profile reads `phrases.json` separately (TTS-specific).

**Config:** Single `config.json` (booleans + strings) read by one `jq` call.

**Panel:** `server.py` plays sounds directly (no shell script) — resolves manifest specs and runs `afplay`/`play`/`say` via subprocess. Volume is locale-safe (pure integer math).

**Panel lifecycle:** `panel.sh` runs `server.py` in foreground, opens browser. Ctrl+C stops it.

**Install/Uninstall:** DRY operations list — same list drives `--dry-run` preview and actual execution.
- `install.sh` — checks deps, copies files, creates user config, injects hooks (backup + validate)
- `uninstall.sh` — stops server, removes hooks surgically, removes files (preserves user config unless `--purge`)
- `test-install.sh` — validates an installation (files, JSON, manifest, assets, hooks, smoke tests)

## Key conventions

- `sounds.json` is the single source of truth for sound mappings. Both `play.sh` and `server.py` read it.
- `play.sh` is a generic dispatcher — reads manifest via `jq`, plays via `afplay`/`play`/`say`. Narration is the only special case (reads `phrases.json`).
- All 10 events: `stop`, `edit`, `bash`, `search`, `permission`, `error`, `subagent_start`, `subagent_stop`, `session_start`, `compact`.
- Hook tag: any hook containing `soundbar/play.sh` is ours.
- `server.py` uses unified `/api/config` POST — send any subset of keys.
- `/api/play` takes `{layer, profile, event}` and plays sounds directly (no shell script).
- Volume: `afplay -v` for file-based, `vol` effect for sox, render-to-temp for TTS.

## Adding a new effects profile

1. Add profile entry in `sounds.json` under `effects` with an `events` object
2. For sampled: set `"dir": "sounds/<name>"`, create directory, add audio files, use `{"file": "name.mp3"}` specs
3. For sox-generated: use `{"sox": "synth args..."}` specs
4. Add the profile name to `EFFECTS_PROFILES` in `switch.sh`
5. Profile auto-appears in the web UI

## Adding a new voice profile

1. Add profile entry in `sounds.json` under `voice`
2. Add the profile name to `VOICE_PROFILES` in `server.py`

## Testing

```bash
~/.claude/soundbar/play.sh stop        # play a single event
~/.claude/soundbar/switch.sh           # show current status
~/.claude/soundbar/panel.sh            # open control panel
./test-install.sh                      # verify installation (42 checks)
```

## Dependencies

- `jq` — required (config reading, hook injection)
- `sox` — for generated effects profiles (`play` command)
- `afplay` / `say` — macOS built-ins for sampled playback and TTS
- `python3` — for web control panel
