---
name: sounds
description: Manage Code Gossip audio feedback — create profiles, find and download sounds, customize phrases
argument-hint: [create <profile> | find <query> | add <profile> <event> <url>]
---

# /sounds — Sound Profile Designer

Creative tasks for Code Gossip, the audio feedback system for coding agents. Soundbar is its web UI for sound configuration. For simple controls (toggle, switch profile), use the CLI directly: `~/.code-gossip/switch.sh`.

## System layout

```
~/.code-gossip/
├── hooks/                    # Agent integrations
├── engine/                   # Playback and narration
├── soundbar/                 # Mixer UI and server
├── configs/                  # User settings and defaults
├── data/sounds.json          # Shipped sound mappings
├── data/sounds/              # Audio assets
├── state/                    # Runtime files and optional speech environment
└── switch.sh                 # CLI controls
```

## Events

`session_start` `edit` `bash` `search` `permission` `error` `subagent_start` `subagent_stop` `compact` `stop`

## Commands

### `create <profile>`
Create a new effects profile:
1. Ask the user what vibe/theme they want
2. Search for matching sounds using `find`
3. Create `~/.code-gossip/data/sounds/<profile>/`
4. Download and trim sounds for each event
5. If `configs/sounds.json` does not exist, copy `data/sounds.json` there. Add the profile under its `effects` key; set `dir` to `sounds/<profile>` and define event file mappings.
6. For a shipped profile, edit `data/sounds.json` in the repository and add its name to `EFFECTS_PROFILES` in `switch.sh`. Custom profiles are selectable in Soundbar.
7. Test the full profile from the mixer.

For generated profiles, use the manifest's `sox` field with synthesis arguments.

### `find <query>`
Search for free/CC0 sound effects:
- bigsoundbank.com — direct MP3 at `https://bigsoundbank.com/UPLOAD/mp3/<id>.mp3`
- soundjay.com, pixabay.com, freesound.org, mixkit.co

When downloading:
1. `curl -sL -o <file> <url>`
2. Verify with `file <file>`
3. Trim if needed: `sox input.mp3 output.mp3 trim <start> <dur> fade 0.02 <dur> <fadeout>`
4. Store in `~/.code-gossip/data/sounds/<profile>/`
5. Test with `afplay <file>`

### `add <profile> <event> <url>`
Download a sound from URL and assign to an event in an existing profile.

## After any changes

- Keep sound mappings in the manifest, not in shell case blocks
- Store user profiles in `configs/sounds.json` and shipped profiles in `data/sounds.json`
- Keep asset paths relative to `data/`
- Test sounds after changes
