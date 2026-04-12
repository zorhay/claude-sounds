# Soundbar — Architecture & Implementation Guide

Comprehensive reference for the Claude Code audio feedback system. Covers all components, their connections, data flows, and planned features.

---

## 1. System Overview

Soundbar adds audio feedback to Claude Code through two independent, mixable layers:

```
Claude Code                    Soundbar
┌──────────┐    hook event    ┌──────────────────────────────────┐
│ Action   │ ──────────────→  │ play.sh <event>                  │
│ (edit,   │    stdin JSON     │                                  │
│  bash,   │                  │  ┌─────────────┐ ┌────────────┐ │
│  stop..) │                  │  │ LAYER 1     │ │ LAYER 2    │ │
└──────────┘                  │  │ Voice       │ │ Effects    │ │
                              │  │ (TTS/aiff)  │ │ (sox/mp3)  │ │
                              │  └──────┬──────┘ └─────┬──────┘ │
                              │         │ parallel      │        │
                              │         └───────┬───────┘        │
                              │                 ▼                │
                              │            speakers              │
                              └──────────────────────────────────┘
```

Both layers fire on every event, in parallel (backgrounded subshells). Each layer can be independently toggled on/off and has its own profile.

## 2. Components

### 2.1 play.sh — Sound Engine

**Purpose:** Core audio engine. Receives an event name, reads config and sound manifest, plays sounds from both layers.

**Invocation:** Called by Claude Code hooks: `~/.claude/soundbar/play.sh <event> &`

**Input:**
- `$1` — event name (one of 10 events)
- `stdin` — hook JSON from Claude Code (currently drained, reserved for future context-aware sounds)

**Config reading:** Single `jq` call extracts all config values as TSV for speed:
```
config.json → jq → EFFECTS_ON, EFFECTS_PROFILE, EFFECTS_VOL,
                    VOICE_ON, VOICE_PROFILE, VOICE_VOL,
                    VOICE, SUBVOICE
```

**Sound dispatch:** Generic `play_sound()` function reads `sounds.json` via one `jq` call per layer, dispatching by spec type:
- `file` → `afplay -v <vol> <path>`
- `files` → random pick, then `afplay`
- `sox` → `play -qn <args> vol <vol>`
- `sequence` → random pair, play in order with gap

**Volume:** Locale-safe integer math (`printf -v '%d.%02d'`). Applied as `afplay -v` for files, `vol` effect for sox, render-to-temp + `afplay -v` for TTS.

**Structure:**
```bash
# Config read (single jq call)
# Volume calculation (0-100 → 0.00-1.00, pure bash)
# play_sound() — generic manifest dispatcher
# LAYER 1: VOICE — narration (phrases.json) or play_sound()
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
        "<event>": {"sequence": [["a.aiff","b.aiff"]], "gap": 0.15}
      }
    }
  },
  "voice": { ... }
}
```

**Spec types:**

| Type | Key | Playback | Volume |
|------|-----|----------|--------|
| Single file | `file` | `afplay -v <vol> <path>` | `afplay -v` |
| Random file | `files` | Pick one, `afplay` | `afplay -v` |
| Sox generated | `sox` | `play -qn <args> vol <vol>` | `vol` effect |
| Sequence | `sequence` | Random pair, play with gap | `afplay -v` |

**Why not parse play.sh?** Previously, `server.py` regex-parsed play.sh's case blocks to discover profiles. This broke whenever command format changed (e.g., adding `-v` flags). The manifest eliminates this fragile coupling.

### 2.2 server.py — Panel HTTP Backend

**Purpose:** Serves the control panel UI and provides a REST API for config/playback.

**Lifecycle:** Started by `panel.sh`, runs in foreground, killed by Ctrl+C. Stateless — all state lives in JSON files on disk.

**API:**

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/` | — | Serve ui.html |
| GET | `/api/status` | — | Full state: config + profiles + phrases + voices |
| POST | `/api/config` | `{key: value, ...}` | Update config (whitelisted keys only) |
| POST | `/api/phrases` | `{event, phrases}` | Update phrases for one event |
| POST | `/api/play` | `{layer, profile, event}` | Play a sound directly (no play.sh) |
| POST | `/api/say` | `{voice, phrase, rate}` | Preview a TTS voice |

**Config key whitelist:** `effects_on`, `effects_profile`, `effects_volume`, `voice_on`, `voice_profile`, `voice_volume`, `voice_main`, `voice_sub`

**Profile discovery:** Reads `sounds.json` manifest. Two functions:
- `parse_effects_profiles()` — reads `effects` section of manifest
- `parse_voice_profiles()` — narration built from phrases.json; others from `voice` section

**Origin detection** (derived from spec structure):

| Origin | Rule | Icon |
|--------|------|------|
| system | file path starts with `/System/` | 🖥 |
| sampled | `.mp3` extension | 🎵 |
| recorded | `.aiff` extension | ⏺ |
| generated | has `sox` key | 🎛 |
| tts | narration profile | 🗣 |

**Playback:** `/api/play` executes audio commands directly — `afplay` for files, `play` (sox) for generated, `say` → temp file → `afplay` for TTS. No shell script in the playback path. Volume computed in Python (locale-safe).

### 2.3 ui.html — Panel Frontend

**Purpose:** Browser-based mixer UI for controlling both layers.

**Layout:**

```
┌─────────────────────────────────────────────────┐
│ SOUNDBAR  Claude Code audio mixer               │
├───────────────────────┬─────────────────────────┤
│ EFFECTS [on] [prof ▼] │ VOICE [off] [prof ▼]   │
│ VOL ────────●── 100%  │ VOL ────────●── 100%   │
│                       │ MAIN [voice▼] ▶         │
│                       │ SUB  [voice▼] ▶         │
├───────────┬───────────┴─────────────────────────┤
│ EVENT     │ EFFECTS          │ VOICE             │
├───────────┼──────────────────┼───────────────────┤
│ session   │ 🎛 synth sine ▶ │ 🗣 Hi there|... ▶│
│ edit      │ 🎛 synth sine ▶ │ 🗣 Writing co..▶ │
│ ...       │                  │                   │
└───────────┴──────────────────┴───────────────────┘
│ ► NARRATION PHRASES (collapsible)                │
└──────────────────────────────────────────────────┘
```

**Components:**
- **Channel strips** (top) — two equal-width cards, each with toggle, profile dropdown, volume slider
- **Mixer table** — fixed-layout table with 3 columns: Event, Effects sound, Voice sound. Each cell shows origin icon + label + play button
- **Phrases section** — collapsible, only shown when narration is active voice profile. Editable phrase chips with inline contenteditable, dialogue pairs for subagent events

**Data flow:**
```
load() → GET /api/status → S (state object) → render()
toggle  → POST /api/config → update S → re-render affected sections
play    → POST /api/play {layer, profile, event} → sound plays
phrase edit → debounced POST /api/phrases → save to disk
```

**State:** All UI state comes from the server on load. The `S` object is the single source of truth. Changes POST to the server first, then update `S` and re-render.

### 2.4 switch.sh — CLI Control

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

### 2.5 panel.sh — Panel Launcher

**Purpose:** Starts the server and opens the browser. Single foreground process.

**Flow:**
1. Check if already running (curl to port 8111)
2. Print URL and "Press Ctrl+C to stop"
3. Background subshell: wait for server ready, then `open` URL
4. `exec python3 server.py` — replaces shell with server process
5. Ctrl+C kills the server directly (no orphans)

### 2.6 install.sh — Installer

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

### 2.7 uninstall.sh — Uninstaller

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
  "effects_on": true,          // boolean — effects layer enabled
  "effects_profile": "default", // string — active effects profile name
  "effects_volume": 100,        // int 0-100 — effects volume
  "voice_on": false,            // boolean — voice layer enabled
  "voice_profile": "narration", // string — active voice profile name
  "voice_volume": 100,          // int 0-100 — voice volume
  "voice_main": "Tara",         // string — main TTS voice name
  "voice_sub": "Aman"           // string — subagent TTS voice name
}
```

Read by `play.sh` (single jq call), `switch.sh`, and `server.py`. Written by `switch.sh` and `server.py`.

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

Sound manifest — single source of truth for all profile→event→sound mappings. See section 2.1.1 for full spec documentation. Read by `play.sh` (hooks) and `server.py` (UI). Not user-editable in normal use; edited when adding/modifying profiles.

### 3.4 settings.json (Claude Code)

Hooks are injected into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh stop &", "timeout": 5}]}],
    "PreToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "~/.claude/soundbar/play.sh edit &", "timeout": 5}]},
      ...
    ],
    ...
  }
}
```

**Hook → event mapping:**

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

| Profile | Type | Sound source | Volume control |
|---------|------|-------------|----------------|
| narration | TTS | `say -v` (macOS) | render to temp → `afplay -v` |
| generals | pre-rendered | .aiff files | `afplay -v` |

## 5. Sound Assets

```
sounds/
├── construction/    18 MP3 files (hammer, saw, drill, walkie-talkie...)
├── generals/        28 AIFF files (pre-rendered TTS: commander + unit dialogue)
└── paper/           19 MP3 files (paper, pencil, typewriter, book...)
```

Total: 65 files. All CC0 (public domain) or generated via macOS TTS.

Sources: bigsoundbank.com (CC0), soundjay.com (royalty-free). Generals generated via `say` command.

## 6. Dependencies

| Dependency | Required | Purpose | Install |
|-----------|----------|---------|---------|
| jq | Yes | Config reading, hook injection | `brew install jq` |
| python3 | Yes | Panel server | Ships with macOS |
| sox | No | Generated effects profiles (`play` command) | `brew install sox` |
| afplay | — | macOS built-in for sampled/system playback | — |
| say | — | macOS built-in for TTS (narration profile) | — |

Linux equivalents: `espeak-ng` for TTS, `paplay`/`pw-play`/`aplay` for playback.

## 7. Connection Map

```
┌──────────────────────────────────────────────────────────┐
│                    sounds.json                            │
│                  (single source of truth)                 │
│                    ▲              ▲                       │
│                    │              │                       │
│  Claude Code       │   Panel      │                      │
│  settings.json ─hooks─→ play.sh   server.py ──→ ui.html  │
│                    │      │          │   │                │
│                    │      ▼          │   ▼                │
│                    │  config.json ◄──┘  phrases.json      │
│                    │      │                  │            │
│                    ▼      ▼                  ▼            │
│              afplay / play (sox) / say   afplay / say     │
│                                                          │
│  CLI: switch.sh ──reads/writes──→ config.json            │
│                                                          │
│  Install:                                                │
│  install.sh ──copies──→ soundbar/ → ~/.claude/soundbar/  │
│             ──merges──→ settings.json (hooks)            │
│  test-install.sh ──validates──→ 42 checks                │
└──────────────────────────────────────────────────────────┘
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

### ~~8.2 Profile Files~~ → Done (sounds.json)

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

### ~~8.4 Volume for Generated Profiles~~ → Done

Sox: `vol` effect appended to synth chain. TTS: render to temp file via `say -o`, play with `afplay -v`. All profile types now respect the volume slider.

### 8.5 Linux Support

Current state: macOS only (`afplay`, `say`). Planned:

- Audio player detection: `paplay` → `pw-play` → `aplay` → `mpv`
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
