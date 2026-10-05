# Changelog

All notable changes to Soundbar are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Manual mixer toggle for Codex approval sounds, enabled by default; disabling it silences approval effects, voice, and narration without changing Codex permissions.
- Shared playback gate for concurrent hook bursts, per-session sound cooldowns, separate effects/voice spacing, and responsive important cues.
- Mixer controls for repetition reduction and adjustable cooldowns, with preview bypass and concurrency/playback regression tests.
- Complete Codex lifecycle coverage, broad local/MCP tool routing, compound command categories (test/build/Git/read/search), and normal no-match search handling.
- Ten Codex-oriented Generals clips, new mixer events, senior phrase fallbacks, Codex-aware narration, and end-to-end event playback tests.
- Codex desktop/CLI integration: `install-codex.sh` and `soundbar/codex.py` connect lifecycle hooks to the shared sound engine, including Generals playback and narrator payload normalization.
- Codex hook installation with dry-run preview, backups, idempotent merging, and surgical uninstall that preserves unrelated handlers and settings.
- Codex setup, hook trust, Generals troubleshooting, event mapping documentation, and adapter/installer regression tests.
- UI polish: accent brand header, play button pulse animation, toggle/slider glow effects, zebra-striped mixer rows, channel hover borders
- Narrator settings two-column layout (LLM | Style & Voice) with vertical divider

### Changed
- Narrator pane sub-titles brighter for scannability
- Kokoro info box uses distinct callout background
- Status dots glow green when connected

## [0.3.0] - 2026-04-15

### Added
- **Kokoro TTS daemon** (`kokoro_server.py`): Unix socket server keeps Kokoro-82M model warm in memory. ~100-200ms per phrase instead of 3-5s cold starts. Auto-starts on first use, auto-shuts down after 10 minutes idle.
- **One-click Kokoro install** from the web panel with progress tracking
- **`integrations.json`** for persistent install state (kokoro_installed flag)
- **Rate variation** for sound specs: `"rate": [min, max]` randomizes `afplay -r` playback rate per play. Standard game audio technique for natural-sounding variety.
- **Soft pencil variants**: 4 new audio files processed from source material (low-pass filter + reverb)
- Variable-length sequence support in sound manifest (was hardcoded to 2 files)
- Server logging: config changes, playback events, persistence writes, errors, Kokoro install progress
- Senior profile connected to TTS engine system (Kokoro + say)

### Changed
- Voice profile renamed: `narration` → `senior`
- Paper profile overhauled: richer combinations for all events, rate variation on file-based specs
- `narrate.py` speaks via Kokoro daemon instead of importing PyTorch directly
- `_play_narration()` in server.py routes through TTS engine (was hardcoded to macOS say)

## [0.2.0] - 2026-04-12

### Added
- Sound manifest (`sounds.json`): single source of truth for all sound mappings, shared by hooks and UI
- Three spec types: `file`/`files` (sampled), `sox` (generated), `sequence` (multi-file)
- 12 effects profiles: ambient, attention, chiptune, construction, default, factory, minimal, organic, paper, sci-fi, submarine, silent
- Voice profiles from manifest (generals with command/response sequences)

### Fixed
- Locale-dependent volume: replaced `awk` floating-point math with pure bash integer arithmetic

## [0.1.0] - 2026-04-09

### Added
- Initial release: audio feedback plugin for Claude Code
- Three independent, mixable layers: effects, voice, narrator
- Hook-based event system (10 events)
- Web control panel (`server.py` + `ui.html`) with mixer layout
- CLI control (`switch.sh`)
- Narrator engine (`narrate.py`): 5 LLM providers, 5 narration styles
- TTS support: macOS say + Kokoro neural TTS
- Install/uninstall scripts with dry-run preview
- Installation validator (`test-install.sh`)
