# Soundbar

Audio feedback for Claude Code — three independent, mixable layers: **effects** (sound profiles), **voice** (spoken lines via TTS), and **narrator** (LLM-generated live commentary).

## Install

```bash
git clone <this-repo>
cd claude-sounds
./install.sh              # install
./install.sh --dry-run    # preview what it will do
./install.sh --dev        # dev mode: symlink, no copy
```

Copies `soundbar/` → `~/.claude/soundbar/` and injects hooks into `settings.json` (with backup + validation).

### Dependencies

- **jq** — required (`brew install jq`)
- **sox** — for generated sound profiles (`brew install sox`)
- **python3** — for control panel and narrator engine
- `afplay`, `say` — macOS built-ins
- LLM provider (narrator only) — one of Claude CLI, Anthropic/Gemini/OpenAI API key, or local Ollama

## Uninstall

```bash
./uninstall.sh              # keeps user config
./uninstall.sh --purge      # removes everything
./uninstall.sh --dry-run    # preview
```

Surgically removes only soundbar hooks from `settings.json`. All other hooks and settings are preserved.

## Usage

### Control Panel

```bash
~/.claude/soundbar/panel.sh
```

Opens a mixer UI in the browser. Server runs in the foreground — Ctrl+C stops it.

### CLI

```bash
~/.claude/soundbar/switch.sh                        # show status
~/.claude/soundbar/switch.sh effects on              # toggle
~/.claude/soundbar/switch.sh effects-profile paper   # switch profile
~/.claude/soundbar/switch.sh voice on
~/.claude/soundbar/switch.sh voice-profile generals
```

## Architecture

Three layers fire on every Claude Code event, mixed together:

```
 ┌─────────────┐   ┌─────────────┐   ┌──────────────┐
 │   Effects   │   │    Voice    │   │   Narrator   │
 │  [paper ▼]  │   │ [generals▼] │   │ [LLM + TTS]  │
 │  ON / OFF   │   │  ON / OFF   │   │  ON / OFF    │
 │  Vol: 80%   │   │  Vol: 100%  │   │  Vol: 100%   │
 └──────┬──────┘   └──────┬──────┘   └──────┬───────┘
        │                 │                  │
        └────────┬────────┴──────────────────┘
                 │
     ┌───────────┴───────────┐
     │    Event: "stop"      │
     │  🎵 book_close.mp3    │
     │  🗣 construction_complete │
     │  💬 "And with that..." │
     └───────────────────────┘
```

### Effects Profiles

| Profile | Type | Description |
|---------|------|-------------|
| default | 🖥 System | macOS system sounds |
| ambient | 🎛 Generated | Soft reverby pads, time-of-day aware |
| chiptune | 🎛 Generated | 8-bit square waves |
| organic | 🎛 Generated | Plucks and chimes |
| sci-fi | 🎛 Generated | Sweeping synths |
| minimal | 🎛 Generated | Quiet single tones |
| factory | 🎛 Generated | Industrial clanks |
| submarine | 🎛 Generated | Deep sonar tones |
| paper | 🎵 Sampled | Paper, pencil, typewriter |
| construction | 🎵 Sampled | Hammer, saw, walkie-talkie |
| attention | 🎛 Generated | Permission + stop only |
| silent | — | No sounds |

Sound specs support `"rate": [min, max]` for natural playback variation (randomizes `afplay -r` per play).

### Voice Profiles

| Profile | Type | Description |
|---------|------|-------------|
| senior | 🗣 TTS | Live phrases via macOS `say` or Kokoro neural TTS, editable in JSON |
| narrator | 💬 LLM | AI-generated commentary via `narrate.py` — see Narrator section |
| generals | ⏺ Pre-rendered | C&C Generals-style voice lines |

### Narrator

LLM-powered live commentary on the coding process. `narrate.py` receives hook event JSON, calls an LLM for a one-sentence observation, and speaks it via TTS.

- **5 providers:** Claude CLI, Anthropic API, Google Gemini, OpenAI, Ollama (local)
- **5 styles:** pair_programmer, narrator, sportscaster, noir, haiku
- All providers use raw HTTP — no SDK dependencies
- Lock file prevents overlapping narrations

### Kokoro TTS

Optional local neural TTS engine (alternative to macOS `say`). `kokoro_server.py` runs as a daemon — loads the model once, then serves TTS requests in ~100-200ms via Unix socket.

- **One-click install** from the control panel, or manual:
  ```bash
  cd ~/.claude/soundbar && python3 -m venv .venv && .venv/bin/pip install kokoro soundfile
  ```
- Auto-starts on first speak request, shuts down after 10 minutes idle
- Set `tts_engine` to `"kokoro"` in config (or toggle in the panel)

### Events

`session_start` `edit` `bash` `search` `permission` `error` `subagent_start` `subagent_stop` `compact` `stop`

## Repo Structure

```
install.sh                    # Installer (--dev, --dry-run)
uninstall.sh → soundbar/...   # Symlink to uninstaller
soundbar/                     # Installed to ~/.claude/soundbar/ (1:1 copy)
├── play.sh                   # Sound engine (hooks call this)
├── narrate.py                # Narrator engine (LLM + TTS)
├── sounds.json               # Sound manifest (single source of truth)
├── switch.sh                 # CLI control
├── panel.sh                  # Control panel launcher
├── server.py                 # Panel HTTP backend
├── kokoro_server.py          # Kokoro TTS daemon
├── ui.html                   # Panel frontend (mixer UI)
├── uninstall.sh              # Uninstaller
├── config.defaults.json      # Default settings
├── phrases.defaults.json     # Default phrases
└── sounds/
    ├── construction/         # 18 MP3 — hammer, saw, drill...
    ├── generals/             # 28 AIFF — pre-rendered TTS voice lines
    └── paper/                # 23 MP3 — paper, pencil, typewriter...
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed component documentation, data flows, and planned features.

## Configuration

`~/.claude/soundbar/config.json`:
```json
{
  "effects_on": true,
  "effects_profile": "default",
  "effects_volume": 100,
  "voice_on": false,
  "voice_profile": "senior",
  "voice_volume": 100,
  "voice_main": "Tara",
  "voice_sub": "Aman",
  "tts_engine": "say",
  "kokoro_voice": "af_heart",
  "narrator_provider": "claude_cli",
  "narrator_model": "",
  "narrator_api_key": "",
  "narrator_style": "pair_programmer"
}
```

## Development

```bash
./install.sh --dev    # symlinks repo → ~/.claude/soundbar/
```

Edits to files in `soundbar/` are immediately live. Config files live at repo root (gitignored), symlinked into `soundbar/`.

## License

Scripts: MIT. Sampled audio: CC0. Generals voices: generated via macOS TTS.
