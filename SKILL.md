---
name: sounds
description: Manage Claude Code sound system — create profiles, find and download sounds, customize phrases
argument-hint: [create <profile> | find <query> | add <profile> <event> <url>]
---

# /sounds — Sound Profile Designer

Creative tasks for the Claude Code sound system. For simple controls (toggle, switch profile), use the CLI directly: `~/.claude/soundbar/switch.sh`.

## System layout

```
~/.claude/soundbar/
├── play.sh                   # Sound engine (hooks call this)
├── switch.sh                 # CLI control
├── panel.sh                  # Control panel (browser UI)
├── server.py                 # Panel HTTP backend
├── ui.html                   # Panel frontend
├── config.json               # User config
├── phrases.json              # Narration phrases
└── sounds/{paper,construction,generals}/
```

## Events

`session_start` `edit` `bash` `search` `permission` `error` `subagent_start` `subagent_stop` `compact` `stop`

## Commands

### `create <profile>`
Create a new effects profile:
1. Ask the user what vibe/theme they want
2. Search for matching sounds using `find`
3. Create `~/.claude/soundbar/sounds/<profile>/`
4. Download and trim sounds for each event
5. Add the case block inside LAYER 2 (effects) of play.sh (alphabetical order)
6. Add profile name to `EFFECTS_PROFILES` in switch.sh
7. Test the full profile

For sampled profiles, use `afplay`:
```bash
    <profile>)
      S="$HOME/.claude/soundbar/sounds/<profile>"
      case "$EVENT" in
        stop)   afplay "$S/stop.mp3" ;;
        # ... all 10 events
      esac
      ;;
```

For generated profiles, use `play -qn synth ...` with sox.

### `find <query>`
Search for free/CC0 sound effects:
- bigsoundbank.com — direct MP3 at `https://bigsoundbank.com/UPLOAD/mp3/<id>.mp3`
- soundjay.com, pixabay.com, freesound.org, mixkit.co

When downloading:
1. `curl -sL -o <file> <url>`
2. Verify with `file <file>`
3. Trim if needed: `sox input.mp3 output.mp3 trim <start> <dur> fade 0.02 <dur> <fadeout>`
4. Store in `~/.claude/soundbar/sounds/<profile>/`
5. Test with `afplay <file>`

### `add <profile> <event> <url>`
Download a sound from URL and assign to an event in an existing profile.

## After any changes

- Read play.sh before editing — two-layer structure
- Effects profiles: alphabetical in LAYER 2
- Voice profiles: in LAYER 1
- Test sounds after changes
