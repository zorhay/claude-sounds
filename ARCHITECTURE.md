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

**Purpose:** Core audio engine. Receives an event name, reads config, plays sounds from both layers.

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

**Structure:**
```bash
# Config read (single jq call)
# Preview overrides (FORCE_LAYER, FORCE_EFFECTS_PROFILE, FORCE_VOICE_PROFILE)
# Volume calculation (0-100 → 0.00-1.00)
# LAYER 1: VOICE — outer case: profile, inner case: event
# LAYER 2: EFFECTS — outer case: profile, inner case: event
```

**Preview mode:** Environment variables override config for panel playback:
- `FORCE_LAYER=effects|voice` — play only one layer
- `FORCE_EFFECTS_PROFILE=<name>` — override effects profile
- `FORCE_VOICE_PROFILE=<name>` — override voice profile

These allow the panel to play any profile's sound for any event, regardless of what's currently active.

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
| POST | `/api/play` | `{layer, profile, event}` | Play a specific sound via play.sh |
| POST | `/api/say` | `{voice, phrase, rate}` | Preview a TTS voice |

**Config key whitelist:** `effects_on`, `effects_profile`, `effects_volume`, `voice_on`, `voice_profile`, `voice_volume`, `voice_main`, `voice_sub`

**Profile discovery:** Regex-parses `play.sh` to extract profile names, event commands, and detect sound origin type. Two parse functions:
- `parse_effects_profiles()` — parses LAYER 2 section
- `parse_voice_profiles()` — narration built from phrases.json; others parsed from LAYER 1

**Origin detection** (from command string):

| Origin | Detection rule | Icon |
|--------|---------------|------|
| system | `afplay /System/` | 🖥 |
| sampled | `afplay` + `.mp3` | 🎵 |
| recorded | `afplay` + `.aiff` (non-system) | ⏺ |
| generated | `play -qn` | 🎛 |
| tts | `say -v` | 🗣 |
| complex | fallback | ⚙ |

**Playback:** `/api/play` runs `play.sh` with environment overrides (`FORCE_LAYER`, `FORCE_*_PROFILE`), not by extracting and replaying commands. This ensures bash arrays, `$RANDOM`, nested case blocks, and local variables all work correctly.

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

### 3.3 settings.json (Claude Code)

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
| ambient | generated | `play -qn synth` (sox) | Not yet (sox vol flag needed) |
| attention | generated | `play -qn synth` (sox) | Not yet |
| chiptune | generated | `play -qn synth` (sox) | Not yet |
| construction | sampled | `afplay -v` mp3 files | `afplay -v $EFX_VOL` |
| default | system | `afplay -v` system .aiff | `afplay -v $EFX_VOL` |
| factory | generated | `play -qn synth` (sox) | Not yet |
| minimal | generated | `play -qn synth` (sox) | Not yet |
| organic | generated | `play -qn synth` (sox) | Not yet |
| paper | sampled | `afplay -v` mp3 files | `afplay -v $EFX_VOL` |
| sci-fi | generated | `play -qn synth` (sox) | Not yet |
| submarine | generated | `play -qn synth` (sox) | Not yet |
| silent | — | No sounds | — |

### 4.2 Voice Profiles

| Profile | Type | Sound source | Volume control |
|---------|------|-------------|----------------|
| narration | TTS | `say -v` (macOS) | Not yet (say has no vol flag) |
| generals | pre-rendered | `afplay -v` .aiff files | `afplay -v $VOX_VOL` |

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
┌─────────────────────────────────────────────────────────┐
│ Claude Code                                             │
│  settings.json ──hooks──→ play.sh ──reads──→ config.json│
│                                    ──reads──→ phrases.json
│                                    ──plays──→ sounds/*   │
│                                    ──calls──→ say, afplay, play (sox)
│                                                         │
│  Panel:                                                 │
│  panel.sh ──exec──→ server.py ──serves──→ ui.html       │
│                     ──reads/writes──→ config.json       │
│                     ──reads/writes──→ phrases.json      │
│                     ──parses──→ play.sh (profile discovery)
│                     ──calls──→ play.sh (FORCE_* env vars)
│                                                         │
│  CLI:                                                   │
│  switch.sh ──reads/writes──→ config.json               │
│                                                         │
│  Install:                                               │
│  install.sh ──copies──→ soundbar/ → ~/.claude/soundbar/ │
│             ──merges──→ settings.json (hooks)           │
│  uninstall.sh ──removes──→ hooks from settings.json    │
│               ──removes──→ ~/.claude/soundbar/          │
└─────────────────────────────────────────────────────────┘
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

### 8.2 Profile Files (repo structure)

Currently all profiles are inline case blocks in play.sh. Planned: each profile as a separate file.

```
soundbar/
├── profiles/
│   ├── effects/
│   │   ├── ambient.sh
│   │   ├── paper.sh
│   │   └── ...
│   └── voices/
│       ├── narration.sh
│       └── generals.sh
```

**Benefits:** Visible in repo, independently editable, designer mode can create new files without touching play.sh.

**play.sh becomes:** Thin dispatcher that sources `profiles/effects/$EFFECTS_PROFILE.sh` and `profiles/voices/$VOICE_PROFILE.sh`.

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

### 8.4 Volume for Generated Profiles

Sox-generated profiles (`play -qn synth`) don't currently use `$EFX_VOL`. Needs `vol $EFX_VOL` appended to each sox command. Similarly, `say` (narration) has no direct volume flag — would need piping through sox or writing to temp file and playing with `afplay -v`.

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
