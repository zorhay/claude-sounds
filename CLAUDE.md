# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Audio feedback plugin for Claude Code. Three independent, mixable layers:
- **Effects layer** — sound profiles triggered by hook events (12 profiles)
- **Voice layer** — spoken lines via TTS or pre-rendered audio (2 profiles)
- **Narrator layer** — LLM-generated live commentary on the coding process (5 providers, 5 styles)

## Repo → Install mapping

`soundbar/` maps 1:1 to `~/.claude/soundbar/`. Install = copy. No restructuring.

```
soundbar/                          →  ~/.claude/soundbar/
├── play.sh                        →  Sound engine (hooks call this)
├── narrate.py                     →  Narrator engine (LLM + TTS, called by play.sh)
├── sounds.json                    →  Sound manifest (single source of truth)
├── switch.sh                      →  CLI control
├── panel.sh                       →  Control panel (runs server, opens browser)
├── server.py                      →  Panel HTTP backend (port 8111)
├── integrations.py                →  Modular venv integration system (Kokoro install, Python detection)
├── ui.html                        →  Panel frontend (mixer layout)
├── kokoro_server.py                →  Kokoro TTS daemon (runs from .venv)
├── uninstall.sh                   →  Uninstaller (also symlinked at repo root)
├── config.defaults.json           →  Default settings (shipped)
├── phrases.defaults.json          →  Default narration phrases (shipped)
├── sounds/{paper,construction,generals}/  →  Audio assets
└── .venv/                         →  Kokoro venv (user-created, gitignored)
```

User files created on install (never overwritten): `config.json`, `phrases.json`.

## Architecture

**Event flow:** Claude Code hook → `soundbar/play.sh <event>` → captures stdin JSON, backgrounds all work → Layer 1 (voice) + Layer 2 (effects) in parallel.

**Backgrounding:** `play.sh` captures hook stdin (~1ms), then wraps all work in `{ ... } &`. Script exits in ~2ms; config reading, sound playback, and narration all run in the background subshell. Hook commands do NOT use `&` — play.sh handles its own backgrounding.

**Sound manifest:** `sounds.json` is the single source of truth for all sound mappings. Both `play.sh` (hooks) and `server.py` (UI) read it. Three spec types: `file`/`files` (sampled), `sox` (generated), `sequence` (multi-file, variable length). Optional `"rate": [min, max]` on any file-based spec randomizes `afplay -r` playback rate per play — standard game audio technique for natural variation. Narration voice profile reads `phrases.json` separately (TTS-specific).

**Narrator:** `narrate.py` receives hook event JSON on stdin, extracts context (file paths, commands, patterns), calls an LLM for a one-sentence commentary, speaks it via TTS. Supports 5 providers (claude_cli, anthropic, gemini, openai, ollama) — all via raw HTTP, no SDK dependencies. Lock file prevents overlapping narrations. CLI modes: normal (hook playback), `--check` (test provider), `--check-tts` (test TTS engine), `--speak "text"` (speak via configured engine), `--dry-run` (text only).

**TTS engines:** Two TTS backends: `say` (macOS built-in, default) and `kokoro` (local neural TTS). Config key `tts_engine` selects which. Both narrator and senior profiles respect this setting. `play.sh` dispatches narration phrases to `narrate.py --speak` when kokoro is selected.

**Kokoro daemon:** `kokoro_server.py` runs from `.venv/bin/python3`, listens on Unix socket (`kokoro.sock`). Loads `hexgrad/Kokoro-82M` once on startup (~3-5s), then serves TTS in ~100-200ms. Auto-starts on first speak request (via `narrate.py`), auto-shuts down after 10 minutes idle. Protocol: newline-delimited JSON over Unix stream socket (`health`, `speak` commands). Setup: `python3 -m venv .venv && .venv/bin/pip install kokoro soundfile`.

**Integrations:** `integrations.py` provides `VenvIntegration` — a reusable base class for on-demand Python venv-based tools. Handles Python version detection (direct/uv/pyenv/conda), venv creation with fallbacks, package installation, pyvenv.cfg repair, and state persistence via `integrations.json`. Pre-configured `kokoro` instance is imported by `server.py`. Future integrations (different TTS engines, local models) plug into the same pattern.

**Config:** Single `config.json` (booleans + strings) read by one `jq` call in `play.sh`, or `json.load` in `narrate.py`/`server.py`. Key `python3_path` stores the absolute path to python3 (detected at install time); all scripts use it instead of bare `python3` to avoid PATH issues in hook environments.

**Panel:** `server.py` plays sounds directly (no shell script) — resolves manifest specs and runs `afplay`/`play`/`say` via subprocess. Volume is locale-safe (pure integer math). Narrator settings panel shows provider/model/key/style configuration with live connection status.

**Panel lifecycle:** `panel.sh` runs `server.py` in foreground, opens browser. Ctrl+C stops it.

**Install/Uninstall:** DRY operations list — same list drives `--dry-run` preview and actual execution.
- `install.sh` — checks deps, copies files, creates user config, injects hooks (backup + validate)
- `uninstall.sh` — stops server, removes hooks surgically, removes files (preserves user config unless `--purge`)
- `test-install.sh` — validates an installation (files, JSON, manifest, assets, hooks, smoke tests)

## Key conventions

- `sounds.json` is the single source of truth for sound mappings. Both `play.sh` and `server.py` read it.
- `play.sh` captures stdin then backgrounds all work via `{ ... } &`. Hook commands must NOT use trailing `&`.
- `play.sh` is a generic dispatcher — reads manifest via `jq`, plays via `afplay`/`play`/`say`. Two special cases: senior (reads `phrases.json`), narrator (pipes stdin to `narrate.py`).
- All 10 events: `stop`, `edit`, `bash`, `search`, `permission`, `error`, `subagent_start`, `subagent_stop`, `session_start`, `compact`.
- Hook tag: any hook containing `soundbar/play.sh` is ours.
- `server.py` uses unified `/api/config` POST — send any subset of keys.
- `/api/play` takes `{layer, profile, event}` and plays sounds directly (no shell script).
- `/api/narrator-check` tests provider connectivity, `/api/narrator-test` generates and speaks a test narration.
- Volume: `afplay -v` for file-based, `vol` effect for sox, render-to-temp for TTS.
- Rate variation: `"rate": [0.92, 1.08]` on file-based specs randomizes `afplay -r` per play. Wider range = more variety. Not applied to sox specs.
- Python path: `python3_path` stores the absolute path to python3 (detected at install). All scripts resolve it with fallback chain: config value → `which python3` → `/usr/bin/python3`.
- TTS config keys: `tts_engine` ("say" or "kokoro"), `kokoro_voice` (voice ID like "af_heart").
- Narrator config keys: `narrator_provider`, `narrator_model`, `narrator_api_key`, `narrator_style`.
- API key is stored in `config.json`, masked in status API responses.
- `/api/tts-check` tests TTS engine availability, `/api/say` accepts optional `engine` param for Kokoro preview.

## Adding a new effects profile

1. Add profile entry in `sounds.json` under `effects` with an `events` object
2. For sampled: set `"dir": "sounds/<name>"`, create directory, add audio files, use `{"file": "name.mp3"}` specs
3. For sox-generated: use `{"sox": "synth args..."}` specs
4. Add the profile name to `EFFECTS_PROFILES` in `switch.sh`
5. Profile auto-appears in the web UI

## Adding a new voice profile

1. Add profile entry in `sounds.json` under `voice`
2. Add the profile name to `VOICE_PROFILES` in `switch.sh` and `server.py`

## Adding a new narrator provider

1. Add provider entry in `PROVIDERS` dict in `narrate.py` (name, description, needs_key, default_model)
2. Add `_call_<provider>()` function implementing the HTTP call
3. Add provider case in `call_provider()` and `check_provider()`
4. Add matching entry in `NARRATOR_PROVIDERS` in `server.py`

## Testing

```bash
~/.claude/soundbar/play.sh stop        # play a single event
~/.claude/soundbar/switch.sh           # show current status
~/.claude/soundbar/panel.sh            # open control panel
./test-install.sh                      # verify installation

# Narrator testing
python3 ~/.claude/soundbar/narrate.py --check                    # test provider connection
python3 ~/.claude/soundbar/narrate.py --check-tts                # test TTS engine availability
python3 ~/.claude/soundbar/narrate.py --speak "Hello world"      # speak using configured TTS engine
echo '{"hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"src/app.ts"}}' \
  | python3 ~/.claude/soundbar/narrate.py --dry-run              # generate narration text
```

## Dependencies

- `jq` — required (config reading, hook injection)
- `sox` — for generated effects profiles (`play` command)
- `afplay` / `say` — macOS built-ins for sampled playback and TTS
- `python3` — for web control panel and narrator engine
- LLM provider (narrator only): one of Claude CLI, Anthropic API key, Google Gemini key, OpenAI key, or local Ollama
- `kokoro` + `soundfile` — optional, for Kokoro neural TTS engine (`pip install kokoro soundfile`)
