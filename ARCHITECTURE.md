# Soundbar — Architecture & Implementation Guide

Comprehensive reference for the Claude Code audio feedback system. Covers all components, their connections, data flows, and planned features.

---

## 1. System Overview

Soundbar adds audio feedback to Claude Code through three independent, mixable layers:

```
Claude Code                    Soundbar
┌──────────┐    hook event    ┌──────────────────────────────────────────┐
│ Action   │ ──────────────→  │ play.sh <event>                          │
│ (edit,   │    stdin JSON     │                                          │
│  bash,   │                  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  stop..) │                  │  │ LAYER 1  │ │ LAYER 2  │ │ LAYER 3  │ │
│          │                  │  │ Voice    │ │ Effects  │ │ Narrator │ │
│          │                  │  │ TTS/aiff │ │ sox/mp3  │ │ LLM+TTS │ │
└──────────┘                  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ │
                              │       │ parallel    │  parallel  │       │
                              │       └──────┬──────┴────────────┘       │
                              │              ▼                           │
                              │          speakers                        │
                              └──────────────────────────────────────────┘
```

All three layers fire on every event, in parallel (backgrounded subshells). Each layer can be independently toggled on/off and has its own profile. The voice layer covers both voice profiles (senior, generals) and the narrator profile — selected by `voice_profile` config key.

## 2. Components

### 2.1 play.sh — Sound Engine

**Purpose:** Core audio engine. Receives an event name, reads config and sound manifest, plays sounds from both layers.

**Invocation:** Called by Claude Code hooks: `~/.claude/soundbar/play.sh <event>`

**Input:**
- `$1` — event name (one of 10 events)
- `stdin` — hook JSON from Claude Code. Captured immediately into `$STDIN_DATA` (~1ms). Consumed by the narrator profile (piped to `narrate.py`); drained and discarded for all other profiles.

**Config reading:** Single `jq` call extracts all config values as TSV for speed:
```
config.json → jq → EFFECTS_ON, EFFECTS_PROFILE, EFFECTS_VOL,
                    VOICE_ON, VOICE_PROFILE, VOICE_VOL,
                    VOICE, SUBVOICE, TTS_ENGINE, KOKORO_VOICE
```

**Sound dispatch:** Generic `play_sound()` function reads `sounds.json` via one `jq` call per layer, dispatching by spec type:
- `file` → `afplay -v <vol> [-r <rate>] <path>`
- `files` → random pick, then `afplay`
- `sox` → `play -qn <args> vol <vol>`
- `sequence` → random sequence from array, play in order with gap

**Rate variation:** Specs with `"rate": [min, max]` randomize `afplay -r` per play. The rate range is extracted as centisimal integers (e.g., `0.92` → `92`) and a random value within the range is computed via `$RANDOM` — pure bash, no `awk`. Not applied to sox specs.

**Volume:** Locale-safe integer math (`printf -v '%d.%02d'`). Applied as `afplay -v` for files, `vol` effect for sox, render-to-temp + `afplay -v` for TTS.

**Voice profile dispatch:** Three code paths based on `VOICE_PROFILE`:
- `narrator` → pipes `$STDIN_DATA` to `narrate.py` (LLM commentary)
- `senior` → reads `phrases.json`, speaks via `say` or `narrate.py --speak` (depending on `TTS_ENGINE`)
- anything else → `play_sound "voice" ...` (reads manifest like effects)

**Structure:**
```bash
# Capture stdin + event
# Background wrapper { ... } &
# Config read (single jq call)
# Volume calculation (0-100 → 0.00-1.00, pure bash)
# play_sound() — generic manifest dispatcher
# LAYER 1: VOICE — narrator (narrate.py) / senior (phrases.json) / play_sound()
# LAYER 2: EFFECTS — play_sound()
```

**Preview mode:** Environment variables override config for manual testing:
- `FORCE_LAYER=effects|voice` — play only one layer
- `FORCE_EFFECTS_PROFILE=<name>` — override effects profile
- `FORCE_VOICE_PROFILE=<name>` — override voice profile

### 2.1.1 sounds.json — Sound Manifest

**Purpose:** Single source of truth for all sound mappings. Read by both `play.sh` (hooks) and `server.py` (UI).

**Structure:**
```json
{
  "effects": {
    "<profile>": {
      "dir": "sounds/<name>",
      "events": {
        "<event>": {"file": "name.mp3"},
        "<event>": {"files": ["a.mp3", "b.mp3"]},
        "<event>": {"sox": "synth 0.3 sine 440 fade 0.01 0.3 0.2"},
        "<event>": {
          "sequence": [["a.aiff","b.aiff"], ["c.aiff"]],
          "gap": 0.15,
          "rate": [0.95, 1.05]
        }
      }
    }
  },
  "voice": { ... }
}
```

**Spec types:**

| Type | Key | Playback | Volume | Rate variation |
|------|-----|----------|--------|----------------|
| Single file | `file` | `afplay -v <vol> [-r <rate>] <path>` | `afplay -v` | Yes |
| Random file | `files` | Pick one, `afplay` | `afplay -v` | Yes |
| Sox generated | `sox` | `play -qn <args> vol <vol>` | `vol` effect | No |
| Sequence | `sequence` | Random sequence from array, play with gap | `afplay -v` | Yes |

**Rate variation:** Any file-based spec (file, files, sequence) can include `"rate": [min, max]`. On each play, a random rate within the range is passed as `afplay -r <rate>`. Standard game audio technique — even a narrow range like `[0.95, 1.05]` prevents the "broken record" effect of hearing the exact same sound repeatedly. Wider range = more variety. Not applied to sox specs (sox has its own pitch/speed controls).

**Sequence spec:** The `sequence` array contains sub-arrays of variable length. One sub-array is picked at random per play. Files in the sub-array are played in order with `gap` seconds between them. Examples: `[["a.mp3"]]` (single file), `[["a.mp3", "b.mp3"]]` (two files), `[["a.mp3", "b.mp3"], ["c.mp3"]]` (randomly pick between a two-file and one-file sequence).

**Why not parse play.sh?** Previously, `server.py` regex-parsed play.sh's case blocks to discover profiles. This broke whenever command format changed (e.g., adding `-v` flags). The manifest eliminates this fragile coupling.

### 2.2 narrate.py — Narrator Engine

**Purpose:** LLM-powered narrator for live coding sessions. Receives hook event JSON, calls an LLM provider for a one-sentence commentary, speaks it via TTS.

**Invocation:** Called by `play.sh` when `voice_profile` is `narrator`:
```bash
echo "$STDIN_DATA" | python3 "$SND/narrate.py"
```
Also called directly for TTS dispatch:
```bash
python3 narrate.py --speak "text to speak"
```

**Providers:** Five providers, all via raw HTTP (`urllib.request`) — no SDK dependencies:

| Provider | Auth | Default model | Timeout |
|----------|------|---------------|---------|
| `claude_cli` | Existing Claude Code auth | `haiku` | 15s |
| `anthropic` | API key | `claude-haiku-4-5-20251001` | 5s |
| `gemini` | API key | `gemini-2.5-flash` | 5s |
| `openai` | API key | `gpt-4o-mini` | 5s |
| `ollama` | None (local) | `qwen3.5:4b` | 10s |

**Styles:** Five narrator personalities, each a system prompt:
- `pair_programmer` — friendly, supportive, sometimes amused
- `sports` — enthusiastic play-by-play commentator
- `documentary` — Attenborough-style nature documentary
- `noir` — hardboiled detective narrating a noir film
- `haiku_poet` — responds only with a 5-7-5 haiku

**Context extraction:** `build_context(data)` parses hook JSON and returns a short narration-relevant string. Handles all hook event types — tool use (Edit/Write, Bash, Grep/Glob, Read, Agent), session events (start, stop, compact), sub-agent events, permission requests, errors. Extracts file paths (shortened to last 2 components), command descriptions, search patterns.

**Concurrency:** Lock file (`/tmp/soundbar_narrator.lock`) prevents overlapping narrations. If a narration is in progress, new events are silently dropped. Lock stores PID; stale locks from dead processes are cleaned up.

**TTS dispatch:** `speak(text, engine, voice, volume)` routes to the configured engine:
- `say` → `speak_say()`: render to temp `.aiff` via `say -v <voice> -r 190`, play with `afplay -v <vol>`, delete temp
- `kokoro` → `speak_kokoro()`: sends `speak` command to Kokoro daemon over Unix socket. Falls back to `say` (voice "Tara") if daemon unavailable.

**CLI modes:**
```
narrate.py                 # normal: read stdin JSON, call LLM, speak
narrate.py --check         # test provider connectivity, print JSON result
narrate.py --check-tts     # test TTS engine availability, print JSON result
narrate.py --speak "text"  # speak text via configured TTS engine
narrate.py --dry-run       # read stdin JSON, call LLM, print text (no speech)
```

### 2.3 kokoro_server.py — Kokoro TTS Daemon

**Purpose:** Keeps the Kokoro-82M model warm in memory for fast TTS (~100-200ms latency vs. 3-5s cold start). Runs as a background daemon from the Kokoro venv.

**Socket:** `~/.claude/soundbar/kokoro.sock` (Unix stream socket, mode `0600`)

**Protocol:** Newline-delimited JSON over Unix stream socket. One request-response per connection.

Requests:
```json
{"cmd": "health"}
{"cmd": "speak", "text": "Hello world", "voice": "af_heart", "volume": 100}
```

Responses:
```json
{"ok": true, "ready": true, "loaded": ["a"], "idle": 42}
{"ok": true}
{"ok": false, "error": "no text"}
```

**Model management:** `ModelManager` lazily loads and caches `KPipeline` instances per language code. American English (`"a"`) is preloaded on startup. British voices (prefix `b`) trigger loading of the British pipeline on first use.

**Threading model:** `ThreadingMixIn` accepts concurrent connections, but an `inference_lock` serializes all TTS inference — the Kokoro model is not thread-safe. Socket I/O and audio playback happen outside the lock.

**Audio pipeline:** Generate PCM via Kokoro → concatenate numpy chunks → clip to `[-1, 1]` → convert to int16 → write WAV (24kHz, mono, 16-bit) to temp file → `afplay -v <vol>` → delete temp.

**Lifecycle:**
- Auto-started by `narrate.py` on first `speak_kokoro()` call (`_ensure_kokoro_daemon()`)
- Auto-shuts down after 10 minutes idle (`IDLE_TIMEOUT = 600`)
- Idle watcher thread polls every 30 seconds
- SIGTERM handler cleans up socket file
- Stale sockets detected and removed on startup and by clients

**Setup:**
```bash
python3 -m venv ~/.claude/soundbar/.venv
~/.claude/soundbar/.venv/bin/pip install kokoro soundfile
```

**Run:** `~/.claude/soundbar/.venv/bin/python3 kokoro_server.py`

### 2.4 server.py — Panel HTTP Backend

**Purpose:** Serves the control panel UI and provides a REST API for config/playback.

**Lifecycle:** Started by `panel.sh`, runs in foreground, killed by Ctrl+C. Stateless — all state lives in JSON files on disk.

**API:**

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/` | — | Serve ui.html |
| GET | `/api/status` | — | Full state: config + profiles + phrases + voices + TTS engines + narrator providers |
| POST | `/api/config` | `{key: value, ...}` | Update config (whitelisted keys only) |
| POST | `/api/phrases` | `{event, phrases}` | Update phrases for one event |
| POST | `/api/play` | `{layer, profile, event}` | Play a sound directly (no play.sh) |
| POST | `/api/say` | `{voice, phrase, rate, engine}` | Preview a TTS voice (say or kokoro) |
| POST | `/api/tts-check` | — | Test TTS engine availability (delegates to `narrate.py --check-tts`) |
| POST | `/api/kokoro-install` | — | Start Kokoro venv + pip install (background thread), or return status if already running/done |
| POST | `/api/kokoro-status` | — | Poll Kokoro install progress: `idle` / `running` / `done` / `error` |
| POST | `/api/narrator-check` | — | Test narrator provider connectivity (delegates to `narrate.py --check`) |
| POST | `/api/narrator-test` | `{event}` | Generate and speak a test narration via `narrate.py --dry-run` + `--speak` |

**Config key whitelist (14 keys):** `effects_on`, `effects_profile`, `effects_volume`, `voice_on`, `voice_profile`, `voice_volume`, `voice_main`, `voice_sub`, `tts_engine`, `kokoro_voice`, `narrator_provider`, `narrator_model`, `narrator_api_key`, `narrator_style`

**Profile discovery:** Reads `sounds.json` manifest. Three profile sources:
- `parse_effects_profiles()` — reads `effects` section of manifest
- `parse_voice_profiles()` — three sources:
  - `senior` — built from `phrases.json` (TTS phrases, not in manifest)
  - `narrator` — synthetic entries: all events show `"LLM narration"` with `api` origin
  - Others (e.g. `generals`) — from `voice` section of manifest

**Origin detection** (derived from spec structure):

| Origin | Rule | Icon |
|--------|------|------|
| system | file path starts with `/System/` | system |
| sampled | `.mp3` extension | sampled |
| recorded | `.aiff` extension | recorded |
| generated | has `sox` key | generated |
| tts | senior profile | tts |
| api | narrator profile | api |

**Playback:** `/api/play` executes audio commands directly — `afplay` for files, `play` (sox) for generated, TTS for senior profile (say or kokoro depending on `tts_engine`), rate variation applied when spec has `rate` range. No shell script in the playback path. Volume computed in Python (locale-safe).

**Kokoro install:** `/api/kokoro-install` creates a `.venv`, runs `pip install kokoro soundfile` in a background thread. Progress tracked in `_kokoro_install` dict (in-memory). `/api/kokoro-status` polls progress. On success, writes `kokoro_installed: true` to `integrations.json`.

### 2.5 ui.html — Panel Frontend

**Purpose:** Browser-based mixer UI for controlling all three layers.

**Layout:**

```
┌─────────────────────────────────────────────────────┐
│ SOUNDBAR  Claude Code audio mixer                   │
├───────────────────────┬─────────────────────────────┤
│ EFFECTS [on] [prof ▼] │ VOICE [off] [prof ▼]       │
│ VOL ────────●── 100%  │ VOL ────────●── 100%       │
│                       │ MAIN [voice▼] ▶             │
│                       │ SUB  [voice▼] ▶             │
│                       │ TTS ENGINE [say ▼]          │
├───────────┬───────────┴─────────────────────────────┤
│ EVENT     │ EFFECTS          │ VOICE                 │
├───────────┼──────────────────┼───────────────────────┤
│ session   │ synth sine    ▶  │ Hi there|...       ▶  │
│ edit      │ synth sine    ▶  │ Writing co..       ▶  │
│ ...       │                  │                       │
└───────────┴──────────────────┴───────────────────────┘
│ ► SENIOR PHRASES (collapsible)                       │
├──────────────────────────────────────────────────────┤
│ ► NARRATOR SETTINGS (collapsible)                    │
│  ┌────────────────────┬──┬───────────────────────┐   │
│  │ LLM Settings       │  │ Style & Voice         │   │
│  │ Provider [claude▼] │  │ Style [pair_prog ▼]   │   │
│  │ Model    [haiku ▼] │  │ TTS engine [say ▼]    │   │
│  │ API Key  [****   ] │  │ Kokoro voice [heart▼] │   │
│  │ Status   ● conn.   │  │ [Test Narration]      │   │
│  └────────────────────┘  └───────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

**Components:**
- **Channel strips** (top) — two equal-width cards, each with toggle, profile dropdown, volume slider. TTS engine selector shown when senior or narrator is the active voice profile.
- **Mixer table** — fixed-layout table with 3 columns: Event, Effects sound, Voice sound. Each cell shows origin + label + play button.
- **Phrases section** — collapsible, only shown when senior is the active voice profile. Editable phrase chips with inline contenteditable, dialogue pairs for subagent events.
- **Narrator section** — collapsible, two-column layout. Left: LLM settings (provider, model, API key, connection status). Right: style & voice (narrator style, TTS engine, Kokoro voice, test narration button).

**Data flow:**
```
load() → GET /api/status → S (state object) → render()
toggle  → POST /api/config → update S → re-render affected sections
play    → POST /api/play {layer, profile, event} → sound plays
phrase edit → debounced POST /api/phrases → save to disk
narrator config → POST /api/config → update S → re-check status
```

**State:** All UI state comes from the server on load. The `S` object is the single source of truth. Changes POST to the server first, then update `S` and re-render.

### 2.6 switch.sh — CLI Control

**Purpose:** Command-line interface for toggling layers and switching profiles without the panel.

**Commands:**
```
switch.sh                        # show status
switch.sh effects [on|off]       # toggle effects
switch.sh effects-profile <name> # switch effects profile
switch.sh voice [on|off]         # toggle voice
switch.sh voice-profile <name>   # switch voice profile
switch.sh main-voice <name>      # change main TTS voice
switch.sh sub-voice <name>       # change subagent TTS voice
switch.sh <profile-name>         # shorthand for effects-profile
```

**Config safety:** Validates jq output before writing to config.json. Empty/invalid output won't truncate the file.

### 2.7 panel.sh — Panel Launcher

**Purpose:** Starts the server and opens the browser. Single foreground process.

**Flow:**
1. Check if already running (curl to port 8111)
2. Print URL and "Press Ctrl+C to stop"
3. Background subshell: wait for server ready, then `open` URL
4. `exec python3 server.py` — replaces shell with server process
5. Ctrl+C kills the server directly (no orphans)

### 2.8 install.sh — Installer

**Purpose:** Copy `soundbar/` to `~/.claude/soundbar/`, inject hooks into `settings.json`.

**Modes:**
- `./install.sh` — production install (copy files)
- `./install.sh --dev` — development install (symlink + config at repo root)
- `./install.sh --dry-run` — preview only

**DRY pattern:** Operations are registered in an `OPS` array. The same array is iterated for both preview and execution — zero mismatch.

**Phases:**
1. Requirements — check jq (required), python3 (required), sox (optional)
2. Copy files — `rsync` or `ln -s` (dev mode)
3. User configuration — create config.json and phrases.json from defaults (skip in dev mode; dev uses symlinks to repo root)
4. Hook injection — backup settings.json, merge hooks via jq, validate JSON
5. Verify — check all expected files exist

**Dev mode specifics:**
- `~/.claude/soundbar` → symlink to `repo/soundbar/`
- `repo/soundbar/config.json` → symlink to `repo/config.json`
- `repo/soundbar/phrases.json` → symlink to `repo/phrases.json`
- Config files at repo root are gitignored
- Every code edit is immediately live — no reinstall needed

**Hook injection safety:**
- Backs up `settings.json` before modifying
- Validates JSON before and after merge
- Restores backup if merge produces invalid JSON
- Idempotent — skips if hooks already present
- Only adds hooks, never modifies existing ones

### 2.9 uninstall.sh — Uninstaller

**Purpose:** Remove hooks and files. Lives inside `soundbar/` (available after install), symlinked at repo root.

**Modes:**
- `./uninstall.sh` — keep user config (config.json, phrases.json)
- `./uninstall.sh --purge` — remove everything
- `./uninstall.sh --dry-run` — preview only

**Dev mode safety:** Detects if `~/.claude/soundbar` is a symlink. Only removes the symlink, never deletes files in the source repo.

**Hook removal:** Filters out entries containing `soundbar/play.sh` from all hook event arrays. Validates JSON before writing. Removes empty hook events. Removes the `hooks` key if it becomes empty.

## 3. Data Files

### 3.1 config.json

```json
{
  "effects_on": true,              // boolean — effects layer enabled
  "effects_profile": "default",    // string — active effects profile name
  "effects_volume": 100,           // int 0-100 — effects volume
  "voice_on": false,               // boolean — voice layer enabled
  "voice_profile": "senior",       // string — active voice profile name
  "voice_volume": 100,             // int 0-100 — voice volume
  "voice_main": "Tara",            // string — main TTS voice name (macOS say)
  "voice_sub": "Aman",             // string — subagent TTS voice name (macOS say)
  "tts_engine": "say",             // string — "say" or "kokoro"
  "kokoro_voice": "af_heart",      // string — Kokoro voice ID
  "narrator_provider": "claude_cli", // string — LLM provider for narrator
  "narrator_model": "",            // string — model override (empty = provider default)
  "narrator_api_key": "",          // string — API key for keyed providers
  "narrator_style": "pair_programmer" // string — narrator personality style
}
```

14 keys total. Read by `play.sh` (single jq call), `narrate.py`, `switch.sh`, and `server.py`. Written by `switch.sh` and `server.py`.

### 3.2 phrases.json

```json
{
  "stop": ["Done", "Finished", "All set"],
  "edit": ["Writing code", "Implementing"],
  "subagent_start": [["Look into this", "On it"], ["Hey, check this out", "I'm on it"]],
  ...
}
```

Each event maps to an array. Simple events: array of strings (random choice). Dialogue events (`subagent_start`, `subagent_stop`): array of `[speaker1, speaker2]` pairs.

### 3.3 sounds.json

Sound manifest — single source of truth for all profile-event-sound mappings. See section 2.1.1 for full spec documentation. Read by `play.sh` (hooks) and `server.py` (UI). Not user-editable in normal use; edited when adding/modifying profiles.

### 3.4 integrations.json

```json
{
  "kokoro_installed": true
}
```

Stores integration state flags. Currently tracks whether the Kokoro TTS package has been installed in the `.venv`. Read and written by `server.py` (`read_integrations()` / `write_integration()`). Created automatically on first Kokoro install. Not user-editable.

### 3.5 settings.json (Claude Code)

Hooks are injected into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh stop", "timeout": 5}]}],
    "PreToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh edit", "timeout": 5}]},
      ...
    ],
    ...
  }
}
```

**Hook -> event mapping:**

| Hook | Event |
|------|-------|
| Stop | stop |
| StopFailure | error |
| PermissionRequest | permission |
| SessionStart | session_start |
| PostCompact | compact |
| SubagentStart | subagent_start |
| SubagentStop | subagent_stop |
| PostToolUseFailure | error |
| PreToolUse (Edit\|Write) | edit |
| PreToolUse (Bash) | bash |
| PreToolUse (Grep\|Glob) | search |

## 4. Profiles

### 4.1 Effects Profiles

| Profile | Type | Sound source | Volume control |
|---------|------|-------------|----------------|
| ambient | generated | sox synth | `vol` effect |
| attention | generated | sox synth | `vol` effect |
| chiptune | generated | sox synth | `vol` effect |
| construction | sampled | mp3 files | `afplay -v` |
| default | system | system .aiff | `afplay -v` |
| factory | generated | sox synth | `vol` effect |
| minimal | generated | sox synth | `vol` effect |
| organic | generated | sox synth | `vol` effect |
| paper | sampled | mp3 files | `afplay -v` |
| sci-fi | generated | sox synth | `vol` effect |
| submarine | generated | sox synth | `vol` effect |
| silent | — | No sounds | — |

### 4.2 Voice Profiles

| Profile | Type | Sound source | Volume control | TTS engine |
|---------|------|-------------|----------------|------------|
| senior | TTS | `say -v` or Kokoro daemon | render to temp -> `afplay -v` | say / kokoro |
| narrator | LLM + TTS | LLM provider -> `say -v` or Kokoro daemon | render to temp -> `afplay -v` | say / kokoro |
| generals | pre-rendered | .aiff files | `afplay -v` | — |

### 4.3 TTS Engines

Both `senior` and `narrator` profiles respect the `tts_engine` config key:

| Engine | Backend | Latency | Setup |
|--------|---------|---------|-------|
| `say` | macOS built-in TTS | ~200-500ms | None (built-in) |
| `kokoro` | Kokoro-82M via `kokoro_server.py` daemon | ~100-200ms (warm) / ~3-5s (cold) | venv + pip install |

When `tts_engine` is `kokoro`:
- `play.sh` dispatches senior phrases via `narrate.py --speak` instead of `say`
- `narrate.py` sends `speak` commands to the Kokoro daemon over Unix socket
- Falls back to macOS `say` (voice "Tara") if daemon is unavailable

## 5. Sound Assets

```
sounds/
├── construction/    18 MP3 files (hammer, saw, drill, walkie-talkie...)
├── generals/        29 AIFF files (pre-rendered TTS: commander + unit dialogue)
└── paper/           23 MP3 files (paper, pencil, typewriter, book, bell...)
```

Total: 70 files. All CC0 (public domain) or generated via macOS TTS.

Sources: bigsoundbank.com (CC0), soundjay.com (royalty-free). Generals generated via `say` command.

## 6. Dependencies

| Dependency | Required | Purpose | Install |
|-----------|----------|---------|---------|
| jq | Yes | Config reading, hook injection | `brew install jq` |
| python3 | Yes | Panel server, narrator engine | Ships with macOS |
| sox | No | Generated effects profiles (`play` command) | `brew install sox` |
| afplay | — | macOS built-in for sampled/system playback | — |
| say | — | macOS built-in for TTS (senior profile, fallback) | — |
| kokoro + soundfile | No | Kokoro neural TTS engine | `pip install kokoro soundfile` (in `.venv`) |
| LLM provider | No | Narrator profile only | One of: Claude CLI, Anthropic/Gemini/OpenAI key, local Ollama |

Linux equivalents: `espeak-ng` for TTS, `paplay`/`pw-play`/`aplay` for playback.

## 7. Connection Map

```
┌──────────────────────────────────────────────────────────────────┐
│                    sounds.json                                    │
│                  (single source of truth)                         │
│                    ^              ^                               │
│                    |              |                               │
│  Claude Code       |   Panel      |                              │
│  settings.json -hooks-> play.sh   server.py --> ui.html          │
│                    |      |  |       |   |                       │
│                    |      |  |       |   v                       │
│                    |      |  |  config.json <--+                 │
│                    |      |  |       |         |                 │
│                    |      v  |       v         |                 │
│                    |  phrases.json   integrations.json            │
│                    |      |                                       │
│                    |      +-- narrate.py --+-- kokoro_server.py   │
│                    |      |   (narrator)   |   (TTS daemon)      │
│                    |      |               |                      │
│                    |      |  kokoro.sock <-+                     │
│                    |      |                                       │
│                    v      v                                       │
│              afplay / play (sox) / say / kokoro                   │
│                                                                  │
│  CLI: switch.sh --reads/writes--> config.json                    │
│                                                                  │
│  Install:                                                        │
│  install.sh --copies--> soundbar/ -> ~/.claude/soundbar/         │
│             --merges--> settings.json (hooks)                    │
│  test-install.sh --validates--> checks                           │
│                                                                  │
│  Kokoro setup:                                                   │
│  .venv/bin/python3 <-- kokoro_server.py (daemon)                 │
│  kokoro.sock       <-- Unix socket (auto-created)                │
│  integrations.json <-- kokoro_installed flag                     │
└──────────────────────────────────────────────────────────────────┘
```

## 8. Plans — Not Yet Implemented

### 8.1 Designer Mode (UI)

Second mode in the panel alongside Live mode. For building custom profiles.

**Planned features:**
- Event assignment table — select audio file per event via file browser
- Audio recorder — record sounds directly in browser (MediaRecorder API), with preview/keep/cancel
- Sound trimming — set start/end points, fade in/out
- Profile creation wizard — name, type (sampled/generated), assign sounds
- Import from URL — download and assign sounds from web

**Implementation direction:**
- Tab-based UI: Live | Designer
- Designer reads/writes profile case blocks in play.sh (or a structured profile format)
- Recorder uses `navigator.mediaDevices.getUserMedia()` for capture, sends WAV to server for storage

### ~~8.2 Profile Files~~ -> Done (sounds.json)

Profiles are now defined in `sounds.json` (structured JSON manifest). `play.sh` is a thin dispatcher that reads the manifest via `jq`. Adding/editing profiles is a JSON edit — no shell code to touch.

### 8.3 MCP Server

Claude Code MCP integration for idiomatic settings interaction.

**Planned resources:**
- `soundbar://config` — current settings
- `soundbar://profiles` — available profiles and their events

**Planned tools:**
- `switch_profile(layer, name)` — switch effects/voice profile
- `toggle(layer, on/off)` — enable/disable layer
- `test_event(event)` — play preview
- `create_profile(name, type)` — create new profile

**Benefits:** Claude can interact with soundbar through native tool use instead of shell commands. The `/sounds` skill would become an MCP client.

### ~~8.4 Volume for Generated Profiles~~ -> Done

Sox: `vol` effect appended to synth chain. TTS: render to temp file via `say -o`, play with `afplay -v`. All profile types now respect the volume slider.

### 8.5 Linux Support

Current state: macOS only (`afplay`, `say`). Planned:

- Audio player detection: `paplay` -> `pw-play` -> `aplay` -> `mpv`
- TTS: `espeak-ng` instead of `say`
- System sounds: find Linux equivalents or skip `default` profile
- Install script: detect platform, adjust dependency checks

### 8.6 VS Code Extension

Webview panel in VS Code sidebar. Reuses ui.html with minor adjustments. No separate server needed — the extension can read/write config files directly.

### 8.7 `soundbar` Command in PATH

Install script creates a symlink for easy access:
```bash
ln -sf ~/.claude/soundbar/panel.sh /usr/local/bin/soundbar
```
User types `soundbar` from any terminal. Needs sudo or `~/.local/bin` alternative.
