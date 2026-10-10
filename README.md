# Code Gossip

**Hear what your coding agents are up to.**

Code Gossip is an open-source audio feedback tool for coding agents on **macOS**. Hear edits, searches, tests, approval requests, and completed turns through sound effects, spoken updates, or live AI commentary—without watching every tool call.

Give your sessions the rustle of paper, the beeps of an 8-bit game, or a running commentary from a sports announcer or noir detective. Use quiet cues when you just want to know that your agent needs you.

**Soundbar** is the web UI for configuring Code Gossip. It brings effects, voices, volumes, previews, and narrator settings into one mixer.

## Coding agents

| Agent | Connection |
|-------|------------|
| **Claude Code** | Lifecycle hooks configured by the [unified installer](#quick-start) |
| **Codex desktop and CLI** | Local lifecycle hooks configured by the [unified installer](#quick-start) |
| **Cursor** | Native user hooks configured by the [unified installer](#quick-start) |

## What you hear

Mix two independently controlled channels:

| Channel | What you hear |
|---------|---------------|
| **Effects** | System sounds, paper rustles, chiptunes, sonar, and more |
| **Voice** | Short spoken phrases, Generals-style voice lines, or AI-generated narration |

Use either channel on its own or combine them. A fresh install starts with macOS system effects enabled and Voice off. Basic effects and spoken cues need no API key.

[Quick start](#quick-start) · [Connect Codex](#connect-codex) · [Using Cursor](#using-cursor) · [Soundbar](#soundbar) · [Choose your sounds](#choose-your-sounds) · [AI narration](#ai-narration-optional) · [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall)

## Quick start

### 1. Check requirements

Code Gossip uses macOS's built-in `afplay` and `say` for audio. You also need **jq** and **Python 3**. **SoX** is optional, but required for generated effects such as chiptune, ambient, and minimal.

If you use Homebrew:

```bash
brew install jq python
brew install sox          # optional: enables generated effects
```

### 2. Install Code Gossip

```bash
git clone https://github.com/zorhay/code-gossip.git
cd code-gossip
bash ./install.sh --dry-run  # optional: preview the changes
bash ./install.sh
```

The installer detects Codex, Cursor, and Claude from their commands, configuration directories, and macOS applications. Choose **all**, or enter multiple agent names or menu numbers (for example, `codex,cursor` or `2 3`). **All** installs for detected agents. You can explicitly select an agent if detection misses it.

For scripted installation, skip the prompt:

```bash
bash ./install.sh --agents codex,cursor
bash ./install.sh --all  # all detected agents
```

Code Gossip is stored once in the agent-independent `~/.code-gossip/`. Only selected agents receive hooks: Claude in `~/.claude/settings.json`, Cursor in `~/.cursor/hooks.json`, and Codex in `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`). Existing hook files are backed up, unrelated hooks are preserved, and repeated installation does not duplicate hooks. Codex-only and Cursor-only installs do not require Claude Code.

### 3. Open Soundbar and try a sound

```bash
~/.code-gossip/soundbar/panel.sh
```

Soundbar opens at [localhost:8111](http://localhost:8111). Choose an Effects profile, adjust the volume, and click a play button to preview an event. Start a new session in your selected agent to hear sounds as you work.

Keep the terminal open while using the mixer; **Ctrl+C** stops its server. Hook sounds continue working when the mixer is closed.

## Connect Codex

Code Gossip can use the same profiles and volumes for local Codex desktop and CLI sessions. You need a Codex version that supports lifecycle hooks.

Select Codex in the unified installer, or run this from the repository folder:

```bash
bash ./install.sh --agents codex --dry-run  # optional: preview the changes
bash ./install.sh --agents codex
```

Then open **`/hooks` in the Codex CLI**, review and trust the Code Gossip commands, and **start a new chat**. Untrusted hooks will not play sounds.

The installer merges Code Gossip hooks into `$CODEX_HOME/hooks.json` (default: `~/.codex/hooks.json`) and backs up an existing file. It preserves other hooks, `config.toml`, and any existing `notify` command. Reinstalling does not duplicate Code Gossip hooks or change hook trust.

If you use a custom `CODEX_HOME`, keep that same environment setting when updating or uninstalling so the scripts find those hooks.

All connected agents share Soundbar and `~/.code-gossip/configs/config.json`; changes apply to all of them.

### Quiet approval sounds

Turn off **Play Codex approval sounds** in the mixer to silence approval cues across all Codex chats. This takes effect on the next approval hook without a restart. Other events, Claude Code approval sounds, and manual previews still work.

This is a manual audio preference: it does not detect or change Codex's approval mode, including “Approve for me.”

<details>
<summary>Codex event coverage and limitations</summary>

The adapter registers 12 lifecycle hook events and maps them to these Code Gossip categories:

| Codex activity | Code Gossip event |
|----------------|----------------|
| Session startup/resume/clear and close | `session_start`, `session_end` |
| User prompt, approval request, turn completion, interruption | `user_prompt`, `permission`, `stop`, `interrupt` |
| Subagent starts and returns | `subagent_start`, `subagent_stop` |
| Before and after context compaction | `pre_compact`, `compact` |
| Patch or file edit, file/context inspection, search | `edit`, `read`, `search` |
| Plan updates and user-input tools | `plan` |
| Tests, builds, other Git operations, shell commands | `test`, `build`, `git`, `bash` |
| Git status, commit history, commit creation, push | `git_status`, `git_history`, `git_commit`, `git_push` |
| Other local and MCP tools | `tool` |
| Explicit shell, patch, or MCP failure | `error` |

Non-shell tool cues generally play before execution; explicit failure cues play afterward. Shell cues play when the command completes. Mixed shell commands prioritize pushes, commits, tests, builds, other Git operations, history, status, search, then reads. Git global options such as `-C` and `-c` are recognized. Commit and push cues require an explicit successful exit status; dry runs use the neutral Git cue. For `rg` and `grep`, exit code 1 means no matches and keeps the search cue; exit code 2 is an error.

Pending shell sessions wait for completion. Polling and agent coordination tools stay quiet to avoid duplicate audio. Hosted web searches do not emit local tool hooks, and failures without an explicit error indicator or exit code cannot be reliably detected.

A `stop` cue means the current turn ended, not that the entire task is complete. Profiles only play categories that have a sound mapping.

</details>

## Using Cursor

Select Cursor in the unified installer, or run `bash ./install.sh --agents cursor`. It configures native [Cursor user hooks](https://cursor.com/docs/hooks) in `~/.cursor/hooks.json` and shares the same sound profiles and mixer settings.

The adapter plays cues for session start/end, file edits, completed shell/MCP tools, tool failures, subagent completion, context compaction, and turn completion. Error and aborted turns map to error and interruption cues. It only observes events; it does not make permission decisions or submit follow-up prompts. These hooks target local Cursor sessions.

Narration identifies Cursor lifecycle and failure events. Shell narration includes the command and output without inferring an exit code that Cursor did not provide.

## Soundbar

Soundbar is Code Gossip’s web UI for sound configuration. Open it with:

```bash
~/.code-gossip/soundbar/panel.sh
```

The panel shows **Code Gossip** at the top and **Soundbar · Sound configuration** underneath. Use it to enable Effects and Voice independently, select profiles, set volumes, preview event cues, edit spoken phrases, and configure AI narration.

Settings are shared across connected agents. Closing Soundbar stops the configuration server; installed hooks continue to play audio using your saved settings.

## Choose your sounds

Use Soundbar to change profiles, enable channels, adjust volumes, and preview sounds. Selecting a profile does **not** enable its channel: turn on **Voice** to hear spoken cues or narration.

### Effects profiles

| Profile | Sound | Requires SoX? |
|---------|-------|---------------|
| `default` | macOS system sounds | No |
| `paper` | Paper, pencil, and typewriter | No |
| `construction` | Hammer, saw, and walkie-talkie | No |
| `ambient` | Soft, reverberant pads that vary by time of day | Yes |
| `chiptune` | 8-bit square waves | Yes |
| `organic` | Plucks and chimes | Yes |
| `sci-fi` | Sweeping synths | Yes |
| `minimal` | Quiet single tones | Yes |
| `factory` | Industrial clanks | Yes |
| `submarine` | Deep sonar tones | Yes |
| `attention` | Approval and turn-completion cues only | Yes |
| `silent` | No effects | No |

### Voice profiles

| Profile | What it does | Setup |
|---------|--------------|-------|
| `senior` | Speaks short, editable phrases | Built-in macOS speech or optional Kokoro |
| `generals` | Plays C&C Generals-style voice lines | Clips are generated during installation |
| `narrator` | Generates live commentary about your coding session | An LLM provider plus a speech engine |

For example, combine paper effects with Generals voice lines:

```bash
~/.code-gossip/switch.sh effects-profile paper
~/.code-gossip/switch.sh effects on
~/.code-gossip/switch.sh voice-profile generals
~/.code-gossip/switch.sh voice on
```

Generals includes 42 local AIFF clips covering all 24 mixer events, including tests, builds, Git operations, approvals, and interruptions.

### Reduce repeated sounds

**Reduce repeated sounds** is on by default. The first cue plays immediately; rapid repeats are skipped instead of queued. Default repeat gaps are **0.75 seconds for effects** and **3 seconds for voice**, adjustable from 0–10 seconds in the mixer.

Approval, error, turn-completion, interruption, Git commit, and Git push cues stay responsive, while near-simultaneous duplicates are still filtered. Manual previews always play. Turn the setting off if you want a cue for every supported event.

This helps with busy or parallel chats, but does not wait for each clip or spoken line to finish.

### Command-line controls

```bash
~/.code-gossip/switch.sh                       # show status and available commands
~/.code-gossip/switch.sh effects-profile minimal
~/.code-gossip/switch.sh effects off           # mute effects
~/.code-gossip/switch.sh voice off             # mute spoken cues and narration
```

To mute Code Gossip completely, turn off both Effects and Voice.

## AI narration (optional)

The `narrator` Voice profile turns coding events into short spoken observations. It can play alongside Effects and replaces the other Voice profiles while selected.

1. In the mixer, choose **Voice → narrator** and enable Voice.
2. Select a provider and model in **Narrator settings**. For API providers, enter your key and click **Save**.
3. Choose a style and voice.
4. Click **Check connection**, then **Test narration**.

| Provider | What you need |
|----------|---------------|
| Claude CLI | Claude Code installed and authenticated |
| Anthropic | An Anthropic API key |
| Google Gemini | A Gemini API key |
| OpenAI | An OpenAI API key |
| Ollama | Ollama running locally with the selected model available |

Narration sends event context, such as file paths and commands, to the selected provider. API providers may charge for usage. Effects, `senior`, and `generals` do not use an LLM.

There are **12 built-in styles**, including Pair Programmer, Sports Commentator, Nature Documentary, Noir Detective, and Haiku Poet. Use the pencil button next to **Style** to create or edit styles. Enable **Deep context** if you want narration to remember earlier moments in the session.

### Optional local speech with Kokoro

Both `senior` and `narrator` can use **Kokoro**, a local neural speech engine, instead of macOS `say`.

Select Kokoro in the mixer's speech engine controls and click **Install Kokoro** if prompted. The installer finds a compatible Python version and sets up its environment. The first spoken request loads the model; the background process shuts down after 10 idle minutes.

Kokoro changes the speaking voice. The narrator still uses your selected LLM provider to generate its words.

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| No sound at all | Preview an event in the mixer. Check the channel toggle, its volume, macOS volume, and the selected output device. |
| Previews work, but Codex is silent | Review and trust Code Gossip hooks in the Codex CLI's `/hooks`, then start a new chat. |
| Previews work, but Claude Code is silent | Rerun `bash ./install.sh` from the repository folder and start a new session. |
| A generated Effects profile is silent | Install SoX with `brew install sox`, or try `default` or `paper`. |
| Generals is silent | Enable Voice as well as selecting `generals`. If clips are missing, regenerate them using the command below. |
| Spoken phrases are silent | Select an installed macOS voice in the mixer, or check the Kokoro installation if using that engine. |
| Narrator is silent | Enable Voice, select `narrator`, and use **Check connection** and **Test narration** to check the provider and speech engine. |
| Only some events play | Check whether the selected profile maps those events. Rapid repeats are also skipped when **Reduce repeated sounds** is enabled. |
| The panel says it is already running | Open [localhost:8111](http://localhost:8111), or stop the existing panel with Ctrl+C in its terminal. |

Regenerate missing Generals clips:

```bash
bash ~/.code-gossip/data/sounds/generals/generate.sh
```

Test a Generals completion cue through the Codex adapter without changing saved settings:

```bash
printf '%s\n' '{"hook_event_name":"Stop"}' | \
  FORCE_LAYER=voice FORCE_VOICE_PROFILE=generals \
  python3 ~/.code-gossip/hooks/codex.py
```

For narrator diagnostics:

```bash
python3 ~/.code-gossip/engine/narrate.py --check      # provider connection
python3 ~/.code-gossip/engine/narrate.py --check-tts  # speech engine
```

## Update Code Gossip

If you cloned the project under its former repository name, `claude-sounds`, update your remote from inside that checkout:

```bash
git remote set-url origin https://github.com/zorhay/code-gossip.git
```

Your local folder can keep its old name. In particular, keep it in place if a development installation links to it. Existing settings and hook paths remain valid.

From your repository folder, pull the latest changes and rerun the installer:

```bash
git pull
bash ./install.sh
```

For Codex, review changed or new hooks in `/hooks` and start a new chat. The installer also runs the Generals clip generator. If you use a [development installation](#development), use `bash ./install.sh --dev` instead.

## Package layout

```text
~/.code-gossip/
├── hooks/       # Claude, Codex, and Cursor integrations
├── engine/      # Shared playback, narration, and speech engines
├── soundbar/    # Mixer UI, server, and panel launcher
├── data/        # Sound manifest and audio assets
├── configs/     # Defaults and your saved preferences
└── state/       # Logs, caches, sockets, and optional Python environment
```

All runtime files use this layout. The optional Kokoro environment is created in `state/.venv`. Development installations link `~/.code-gossip/` directly to the checkout’s `code-gossip/` directory; rerun `bash ./install.sh --dev` to update hooks while preserving settings.

Soundbar remains the name of the mixer UI. Open it with `~/.code-gossip/soundbar/panel.sh`.

## Configuration files

The mixer saves settings automatically. For manual customization, your files live in `~/.code-gossip/configs/`:

| File | Customize |
|------|-----------|
| `config.json` | Enabled channels, profiles, volumes, speech engine, and narrator provider |
| `phrases.json` | Spoken phrases for the `senior` Voice profile |
| `narrator_styles.json` | Narrator style names and prompts |

See [config.defaults.json](code-gossip/configs/config.defaults.json) for the shipped settings. Edit the user files above to change your installation. Narrator API keys entered in the mixer are stored in `config.json`.

The repeat controls use `sound_spacing_on`, `effects_cooldown_ms`, and `voice_cooldown_ms`. Codex approval audio uses `codex_permission_sound_on`.

## Uninstall

Run the uninstaller and choose **all** or one or more agents, just as during installation:

```bash
bash ~/.code-gossip/uninstall.sh --dry-run  # preview selected removals
bash ~/.code-gossip/uninstall.sh            # select integrations to remove
bash ./uninstall.sh --agents codex,cursor       # remove specific integrations without prompting
bash ./uninstall.sh --all                       # remove all integrations without any questions
bash ./uninstall.sh --all --purge               # also delete saved settings
```

`--all` checks all supported integrations, even if an agent executable or the shared installation is already gone. Partial removal preserves the shared files and running processes while another integration still uses them. Removing the last integration stops the panel and Kokoro processes and removes the shared installation. By default, it keeps your `configs/` user settings, including a custom `sounds.json` override. Existing hook files are backed up before modification. Cached hook commands become silent after removal, so an active chat can continue. Development uninstalls remove only the installation symlink and leave the source repository and its settings intact, even with `--purge`.

You can use the repository's `uninstall.sh` after the installed script has been removed, including to purge retained settings.

Malformed hook files must be repaired before uninstalling: the uninstaller checks all integrations before deciding whether the shared runtime is still needed. Installation validates only the agents you select.

## Development

Use a development install to make changes in `code-gossip/` immediately available to hooks:

```bash
bash ./install.sh --dev
```

If `~/.code-gossip/` is already a regular installation directory, uninstall it before switching to `--dev`. Back up any settings you want to bring into the development setup, then remove the leftover installation directory. Development settings live in `code-gossip/configs/` and runtime files in `code-gossip/state/`; both are gitignored. The `~/.code-gossip/` symlink makes edits immediately available.

Run the Codex adapter regression tests without third-party dependencies:

```bash
python3 -m unittest tests.test_codex
```

For the full suite, install `pytest` in your development environment and run `python3 -m pytest`. To check an installed copy, run `bash ./test-install.sh`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for components and data flows, and [CHANGELOG.md](CHANGELOG.md) for release history. The sound mappings are defined in [code-gossip/data/sounds.json](code-gossip/data/sounds.json).

## License

Scripts: MIT. Sampled audio: CC0. Generals voices: generated via macOS TTS.
