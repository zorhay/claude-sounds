# Soundbar

Audio feedback for Claude Code and Codex — three independent, mixable layers: **effects** (sound profiles), **voice** (spoken lines via TTS), and **narrator** (LLM-generated live commentary).

## Install

```bash
git clone <this-repo>
cd claude-sounds
./install.sh              # install
./install.sh --dry-run    # preview what it will do
./install.sh --dev        # dev mode: symlink, no copy
```

Copies `soundbar/` → `~/.claude/soundbar/` and injects hooks into `settings.json` (with backup + validation).

### Connect Codex (desktop app and CLI)

After installing Soundbar, connect the same sounds and mixer settings to local
Codex desktop and CLI sessions. The base installer also installs Claude Code
hooks; the Codex installer adds a separate integration:

```bash
bash ./install-codex.sh --dry-run  # preview
bash ./install-codex.sh            # install Codex hooks
bash ./install-codex.sh --uninstall # remove only Soundbar's Codex hooks
```

After upgrading, rerun `bash ./install-codex.sh`, review changed/new hooks in
`/hooks`, and start a new chat. Regenerate Generals clips with the command below
when upgrading an existing installation.

The installer merges into `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`),
backs up an existing file, and preserves other hooks and `config.toml`, including
any existing `notify` command. Reinstalling does not duplicate hooks.

Open `/hooks` in the Codex CLI to review and trust the Soundbar commands, then
start a new chat. Codex skips untrusted hooks; the installer does not alter hook
trust. See the [official Codex hook documentation](https://learn.chatgpt.com/docs/hooks).

All 12 Codex hook events are connected. The mixer supports these categories:

| Codex activity | Soundbar event |
|----------------|----------------|
| Session startup/resume/clear and close | `session_start`, `session_end` |
| User prompt, approval request, turn completion, interruption | `user_prompt`, `permission`, `stop`, `interrupt` |
| Subagent starts and returns | `subagent_start`, `subagent_stop` |
| Before and after context compaction | `pre_compact`, `compact` |
| Patch or file edit, file/context inspection, search | `edit`, `read`, `search` |
| Plan updates and user-input tools | `plan` |
| Tests, builds, Git operations, other shell commands | `test`, `build`, `git`, `bash` |
| Other local and MCP tools | `tool` |
| Explicit shell, patch, or MCP failure | `error` |

Tool names are classified by their operation (for example `read_page`,
`write_file`, and `search`); unknown local/MCP operations use the generic tool
sound. Successful non-shell tools sound before execution; explicit failures
sound afterward. Shell categories sound when the command completes.

Codex command forms such as `cd repo && rg ... | head`, `env ... bash -lc ...`,
`uv run pytest`, `python3 -m unittest`, `pnpm run test:unit`, `npm run build`,
and `git -C repo diff` are recognized without executing the command in the
adapter. Mixed commands prioritize tests, builds, Git, search, then reads.
`rg`/`grep` exit 1 means no matches and keeps the search sound; exit 2 is an error.
Pending unified-exec sessions wait for their completion hook. Polling and agent
coordination tools stay quiet to avoid duplicate audio.

Hosted web searches do not emit local tool hooks. Failures without an explicit
error indicator or exit code cannot be reliably detected. Codex has no separate
`StopFailure` or `PostToolUseFailure` event. `Stop` means the current turn ended;
it does not assert the entire task is complete. Narration uses Codex-specific
context for these distinctions.

Both apps share `~/.claude/soundbar/config.json` and the existing control panel.
Remove the Codex hooks before uninstalling Soundbar itself. This integration
requires local macOS playback and a Codex version with lifecycle hook support.

#### Generals voice profile

Generals works with Codex through the same sound manifest and 38 local AIFF
clips used by Claude Code. Select the profile **and enable the Voice layer**:

```bash
~/.claude/soundbar/switch.sh voice-profile generals
~/.claude/soundbar/switch.sh voice on
~/.claude/soundbar/panel.sh
```

The control panel runs at [http://localhost:8111](http://localhost:8111).
Its profile, volume, and layer controls apply to both agents. Selecting Generals
alone does not turn Voice on. Generals includes dedicated lines for orders,
reads, tools, plans, tests, builds, Git operations, compaction, session close,
and interruption.

Test a completion sound through the Codex adapter without changing saved settings:

```bash
printf '%s\n' '{"hook_event_name":"Stop"}' | \
  FORCE_LAYER=voice FORCE_VOICE_PROFILE=generals \
  python3 ~/.claude/soundbar/codex.py
```

If this plays but real Codex events are silent, check `/hooks` for enabled,
trusted Soundbar hooks and start a new chat. If playback is silent too, check
Voice volume, macOS output volume, and the selected output device. Missing
Generals clips can be regenerated with
`bash ~/.claude/soundbar/sounds/generals/generate.sh`.

### Dependencies

- **jq** — required (`brew install jq`)
- **sox** — for generated sound profiles (`brew install sox`)
- **python3** — for control panel and narrator engine
- `afplay`, `say` — macOS built-ins
- LLM provider (narrator only) — one of Claude CLI, Anthropic/Gemini/OpenAI API key, or local Ollama

## Uninstall

```bash
bash ./install-codex.sh --uninstall # remove Codex hooks first, if installed
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

### Reduce repeated sounds

The mixer enables **Reduce repeated sounds** by default. A shared playback gate
coordinates hooks from both agents, so simultaneous events cannot independently
launch the same cue. The first cue plays immediately; suppressed events are
dropped instead of queued for later.

- Effects repeat gap: **0.75 seconds**; voice repeat gap: **3 seconds**, adjustable
  from 0–10 seconds in the mixer.
- Routine clips reused by different event categories share their repeat window.
  Different routine cues are also spaced by up to 150 ms for effects and 1.5 s
  for voice, reducing chatter when event types alternate.
- Approval, error, turn completion, and interruption bypass the routine spacing
  budget. Identical important cues are still deduplicated for up to 0.5 s for
  effects and 1 s for voice.
- Manual previews always play. Disable the toggle to restore every-event playback.

Repeat windows are measured from the last accepted cue, so a long burst can
produce occasional feedback rather than indefinite silence. Per-session repeat
tracking and a shared per-layer spacing budget keep parallel chats manageable.
This reduces overlap; it does not wait for every clip or spoken line to finish.

Settings: `sound_spacing_on`, `effects_cooldown_ms`, `voice_cooldown_ms` in
`config.json`. Existing installations pick up defaults without changing user
settings. Runtime timestamps are stored in a locked temporary state file.

### CLI

```bash
~/.claude/soundbar/switch.sh                        # show status
~/.claude/soundbar/switch.sh effects on              # toggle
~/.claude/soundbar/switch.sh effects-profile paper   # switch profile
~/.claude/soundbar/switch.sh voice on
~/.claude/soundbar/switch.sh voice-profile generals
```

## Architecture

Enabled layers respond to supported Claude Code or Codex events, mixed together.
Profiles play only the events defined in their sound mappings:

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

`session_start` `session_end` `user_prompt` `edit` `read` `search` `bash` `tool` `plan` `test` `build` `git` `permission` `error` `subagent_start` `subagent_stop` `pre_compact` `compact` `stop` `interrupt`

## Repo Structure

```
install.sh                    # Installer (--dev, --dry-run)
install-codex.sh              # Connect/remove Codex hooks (--dry-run, --uninstall)
uninstall.sh → soundbar/...   # Symlink to uninstaller
soundbar/                     # Installed to ~/.claude/soundbar/ (1:1 copy)
├── play.sh                   # Sound engine (hooks call this)
├── playback_gate.py          # Concurrent hook deduplication and sound spacing
├── codex.py                  # Codex hook installer and event adapter
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
    ├── generals/             # 38 AIFF — pre-rendered TTS voice lines
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
bash ./install-codex.sh # add Codex hooks to the dev installation
python3 -m unittest tests.test_codex # adapter and installer regression tests
```

Edits to files in `soundbar/` are immediately live. Config files live at repo root (gitignored), symlinked into `soundbar/`.

## License

Scripts: MIT. Sampled audio: CC0. Generals voices: generated via macOS TTS.
