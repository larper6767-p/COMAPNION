# Changelog and historical build notes

> This file preserves the original feature-by-feature development log. It is
> historical, not the current setup guide. For current installation, features,
> and commands, see [README.md](README.md). For the module map, see
> [ARCHITECTURE.md](ARCHITECTURE.md).

# WIFE - AI Voice Companion (historical)

A desktop AI companion with voice input, cloud LLM, TTS, persistent memory, and a desktop pet GUI.

---

## Quick Start

| Mode | Command | Launcher |
|------|---------|----------|
| GUI desktop pet (400 px) | `python src/app.py` | `run_gui.bat` |
| GUI desktop pet (500 px) | `python src/app.py --size 500` | `run_gui_large.bat` |
| CLI terminal | `python src/she.py` | `run_cli.bat` |

---

## Setup

### 1 — Install dependencies

```bash
pip install -r requirements.txt
```

For Windows audio ducking (lowers other app volumes while she speaks):
```bash
pip install pycaw
```

For neural always-on wake word (optional):
```bash
pip install openwakeword
```

### 2 — API keys

Edit `.env`:
```
FISH_API_KEY=your_fish_audio_key
CEREBRAS_API_KEY=your_cerebras_key
```

- Fish Audio: https://fish.audio/
- Cerebras: https://cloud.cerebras.ai/

### 3 — Customise the character

Edit `config/personality.json` — all settings are documented in the file.
Hot-reload at runtime with `/reload` (CLI) or right-click → Reload personality (GUI).

---

## Project Structure

```
WIFE/
├── src/
│   ├── app.py              GUI desktop pet
│   ├── she.py              CLI companion
│   └── core/
│       ├── config.py       Configuration + validation + logging
│       ├── model.py        Cerebras / Ollama LLM interface
│       ├── audio.py        TTS synthesis, caching, ducking
│       ├── recording.py    VAD recording, interrupt monitor, wake word
│       └── memory.py       Episodic log, relationship tracker, facts
├── config/
│   ├── personality.json    Character definition (edit this)
│   ├── memory.json         Flat memory facts (auto-managed)
│   ├── relationship.json   Closeness score (auto-managed)
│   └── episodes/           Daily conversation logs (auto-created)
│       ├── 2026-10-09.json
│       └── summaries.json
├── characters/
│   └── marin/              Character art (PNG / GIF)
│       ├── idle.png
│       ├── joyful.png      (add any mood name from personality.json)
│       └── ...
├── logs/
│   └── wife.log            Rotating log file (auto-created)
├── .env                    API keys
└── requirements.txt
```

---

## Features

### Voice Input
- Auto VAD (voice activity detection) — starts and stops automatically
- Per-turn noise recalibration — adapts to changing ambient noise
- Interrupt detection — she stops mid-sentence when you start talking
- Wake word — say "Hey Marin" to activate (set `wake_word_enabled: true`)
  - Uses `openwakeword` if installed, falls back to Whisper keyword matching

### LLM
- **Cerebras Cloud** (default) — extremely fast inference, `gpt-oss-120b`
- **Ollama** fallback — local self-hosted models
- Streaming with incremental TTS — first sentence plays before the rest is generated
- Context summarisation — old history is compressed to bullet points, not dropped
- Graceful error messages — API failures return an in-character response

### TTS & Audio
- Fish Audio with voice cloning
- Emotion-aware TTS — mood tag from LLM applies tone to synthesis
- Voice speed control — `/speed 0.75` to `2.0`
- Audio ducking — lowers other Windows app volumes while she speaks (requires `pycaw`)
- LRU cache — repeated phrases play from cache instantly

### Memory
- **Flat facts** — `/remember` to store, `/forget` to clear
- **Episodic log** — every turn saved to `config/episodes/YYYY-MM-DD.json`
- **Daily summaries** — old episodes compressed and injected into system prompt
- **Relationship progression** — closeness score increments each turn; unlocks new behaviour tiers defined in `personality.json`

### GUI
- Animated GIF / PNG character art with automatic fallback
- Mood crossfade — smooth transition between emotion sprites
- Typing indicator — pulsing `...` animation while Cerebras generates
- Right-click context menu — volume, speed, reload personality, stats
- Speech bubble flips — moves below the pet if she's near the top of the screen
- System tray — show/hide, mute, quit
- Tray notifications — balloon when she sends a proactive message
- Proactive messages — she talks to you after N minutes of silence

---

## CLI Commands

| Command | Description |
|---------|-------------|
| Enter | Record voice |
| text + Enter | Send text |
| `/remember <fact>` | Save a fact to memory |
| `/forget` | Clear all memory facts |
| `/mute` | Toggle voice on/off |
| `/volume 0.7` | Set playback volume (0.0–1.0) |
| `/speed 1.0` | Set TTS speed (0.5–2.0) |
| `/reload` | Hot-reload `personality.json` |
| `/stats` | Show token, memory, relationship stats |
| `/t 20` | Change max recording duration (seconds) |
| `/quit` | Exit |

---

## personality.json Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Character name |
| `system_prompt` | string or list | LLM instructions |
| `greeting` | string | Startup message |
| `temperature` | float 0–2 | LLM creativity |
| `max_reply_length` | int | Max characters per reply |
| `max_tokens` | int | Max LLM output tokens |
| `repeat_penalty` | float | Reduces repetition (Ollama only) |
| `stop` | list | Stop sequences |
| `mood_tags` | bool | `true` = LLM prefixes `[mood]` |
| `moods` | list | Available mood names |
| `emotion_template` | string or null | Format: `"{mood}: {text}"` |
| `voice_speed` | float 0.5–2.0 | TTS playback speed |
| `audio_ducking_enabled` | bool | Lower other app volumes |
| `audio_ducking_volume` | float 0–1 | Target duck level |
| `wake_word_enabled` | bool | Enable always-on listening |
| `wake_word` | string | Phrase to listen for |
| `closeness_enabled` | bool | Enable relationship progression |
| `closeness_tiers` | object | `{"0": "hint", "30": "hint", ...}` |
| `summarize_after` | int | Compress history after N messages |
| `proactive_enabled` | bool | Enable idle messages |
| `proactive_idle_minutes` | int | Minutes of silence before firing |
| `proactive_messages` | list | Pool of messages to pick from |
| `proactive_generate` | bool | `true` = LLM builds context-aware proactive messages |
| `auto_extract_facts` | bool | `true` = auto-learn durable facts about the user |
| `max_memory_facts` | int | Max stored facts injected into each prompt |
| `gentle_missed` | bool | `true` = speak a sorry phrase when input is missed |

---

## Character Art

Place PNG or GIF files in `characters/<name>/`:

```
idle.png          (or idle.gif — animated idle recommended!)
joyful.png
sad.png
excited.png
embarrassed.png
angry.png
surprised.png
curious.png
relaxed.png
joyful_open.png   (optional: mouth-open variant for lip sync)
```

Missing moods fall back to `idle`. Missing `idle` falls back to a drawn placeholder.

---

## Whisper Presets

See `WHISPER_PRESETS.py` for fast / balanced / quality / GPU configurations.
Edit the `whisper_*` fields in `src/core/config.py` to change them.

| Preset | Model | Beam | Speed | Accuracy |
|--------|-------|------|-------|----------|
| Fast | base | 1 | ~0.5s | 85% |
| Balanced (default) | small | 5 | ~1.5s | 92% |
| Quality | medium | 7 | ~3s | 95% |
| Maximum | large | 10 | ~6s | 98% |

---

## Troubleshooting

**`CEREBRAS_API_KEY not found`** — add your key to `.env`

**`Voice failed`** — check `FISH_API_KEY` in `.env` and your internet connection

**`Recording failed`** — check microphone permissions:
```python
import sounddevice as sd; print(sd.query_devices())
```

**`No audio ducking`** — install pycaw: `pip install pycaw`

**Switching to local Ollama** — in `src/core/config.py`:
```python
self.use_cerebras = False
self.local_model  = "your-model:latest"
```
Then run `ollama serve` before launching.

**Wake word not triggering** — install openwakeword: `pip install openwakeword`
Or leave it uninstalled for Whisper-based keyword matching (slightly slower).

---

## Vector Memory (Feature 2)

Marin can remember things **semantically**: facts about you and older conversation
summaries are embedded and stored locally, and the most relevant ones are pulled
back into the prompt each turn -- so she can recall the *right* thing without the
fact list growing unbounded.

**Disabled by default.** With it off, nothing is imported, written, or injected,
and the companion behaves exactly as before.

### Configuration -- `config/memory.yaml`

```yaml
enabled: false              # master switch
path: "memory_store"        # on-disk store, relative to the project root
collection: "marin_memories"
embedding: "default"        # "default" = ONNX all-MiniLM-L6-v2 (downloads ~80 MB once)
                            # "hash"    = offline hashed bag-of-words (no download)
top_k: 4                    # how many relevant memories to inject per turn
max_distance: null          # optional: drop hits farther than this (e.g. 1.0)
telemetry: false            # chromadb anonymised telemetry -- keep off
```

### Setup
1. Install the dependency: `.venv\Scripts\pip install chromadb`
2. Set `enabled: true` in `config/memory.yaml`.
3. Seed the store from your existing facts:

   ```
   .venv\Scripts\python.exe src\memory\migrate.py
   ```

   (idempotent -- safe to re-run; it will not duplicate anything.)

New facts (auto-extracted each turn, or `/remember`) and daily summaries are
added to the store automatically as they happen.

### How it works
- **Injection point:** `MemoryManager.build_context_block(query=...)` appends a
  `Relevant context from past conversations:` block. Memories already shown in
  the flat fact list are not repeated.
- **Backends:** `default` uses chromadb's bundled ONNX MiniLM (semantic, small,
  one-time download cached at `~/.cache/chroma`); `hash` is a dependency-free,
  fully-offline hashed bag-of-words (lower quality, no download).
- **Failure-safe:** if chromadb is missing or the store cannot initialise, the
  feature silently degrades to a no-op -- the companion still runs.

### Notes / limitations
- chromadb is a fairly heavy dependency (grpcio, kubernetes, opentelemetry,
  uvicorn...). It is only imported when enabled.
- The store lives in `memory_store/` (git-ignored). Back it up to keep her memories.
- Switching `embedding` backends should be followed by a re-migration.

---

## Emotion Expressions (Feature 3)

Marin's mood already drives a Live2D expression (e.g. `joyful` -> blush, `love`
-> heart-eyes) through the renderer's `SetExpression` path. **Feature 3 moves that
mapping out of hard-coded Python and into a config file** so you can tune it
without editing code.

> This is not a second expression system -- the renderer still owns and calls
> `SetExpression()` / `ResetExpression()`. The config only supplies the mapping.

### Configuration -- `config/emotions.yaml`

```yaml
enabled: true               # false = no expressions at all (mood params still apply)

# .exp3.json files to register: "filename" -> lookup key
expressions:
  "脸红.exp3.json":   blush
  "星星眼.exp3.json": star_eyes
  # ... (see file for the full list)

# mood -> expression key, or null for none
mood_expressions:
  joyful:      blush
  excited:     star_eyes
  embarrassed: blush
  sad:         tears
  angry:       angry
  love:        heart_eyes
  aroused:     blush
  idle:        null
  surprised:   null
  curious:     null
  relaxed:     null
```

- **Merged over built-in defaults:** list only what you want to change. A mood set
  to `null` means "no expression"; a file entry set to `null` is unregistered.
- The shipped values **reproduce the built-in behaviour exactly**, so nothing
  changes until you edit the file.
- Files that are absent from `characters/<name>/live2d/` are skipped silently.
- Moods still change the face through `config/live2d_moods.json` (parameter maps);
  `enabled: false` only turns off the `.exp3.json` expression layer.

### Applying changes
Edit `config/emotions.yaml`, then **right-click Marin -> Reload** to re-read it
without restarting.

---

## On-Demand Screen Vision (Feature 4)

Press a hotkey and Marin takes a single look at your screen, so you can ask her
about what is in front of you.

> **Off by default.** Enable it with `enabled: true` in `config/vision.yaml`.
> Nothing is ever captured unless you press the hotkey.

### How it works
1. Press the hotkey (default **`ctrl+alt+s`**). Marin grabs **one** frame.
2. The frame is held **in memory only** and expires after `ttl_seconds`.
3. Your next message carries the picture — she looks then (no surprise LLM call
   on the keypress itself).
4. A status message flashes and **one line is logged per capture**, so you always
   know when she looked.

### Scope and privacy
- Captures the **work area (taskbar excluded) of the monitor containing the
  active window** — never the merged multi-monitor desktop.
- `mode: monitor` grabs that whole monitor; `mode: active_window` grabs just the
  active window, clipped to that monitor.
- **No screenshots are written to disk, ever** — encoding happens in RAM and the
  buffer is dropped when it expires or is used.
- Capture is **on demand only** — there is no continuous or background capture.

### Configuration — `config/vision.yaml`
```yaml
enabled: false
hotkey: "ctrl+alt+s"       # not ctrl+shift+s (that is scene1_hotkey)
mode: "monitor"            # "monitor" | "active_window"
ttl_seconds: 30            # how long a capture stays armed
vision_model: ""           # "" = reuse the normal chat model
max_width: 1280            # downscale wide captures (0 = off)
indicator: true            # flash a status message + log per capture
```

### Model requirement
Screen vision needs a **multimodal** model. The default backends
(`cerebras_model` / `local_model` in `src/core/config.py`) are text-only, so set
`vision_model` to a model that accepts images (for example an Ollama vision
model, or a Cerebras vision model) — otherwise the provider will reject the
request and the turn falls back to Marin's usual error message.

### Notes
- The hotkey is checked at startup against every other configured hotkey
  (`--hotkey`, `scene1_hotkey`, this one); a conflict is logged and shown in the
  status bubble.
- `full_screen` is accepted as an alias for `monitor` (older docs).
- Requires `mss` and `pillow` (already in `requirements.txt`; `mss` is pure
  Python).

---

## Text Input (Feature 5 / 8B)

Type to her whenever you don't feel like talking - without giving up voice.

### How to use it (inline bubble input - the default)
- **The speech bubble IS the text box.** While she is silent the bubble shows an
  input with a placeholder; tap it, type, press **Enter** to send. `Esc` clears
  the line.
- The moment she starts speaking the input disappears and the same slot becomes
  her speech bubble; the input comes back only once her TTS has actually
  finished — plus a short grace period (`return_delay`, default 2 seconds) —
  never while she's still talking.
- Tapping her visible bubble swaps straight back to the input - but never while
  she is mid-sentence (her words are never wiped).
- Your typed message echoes in the bubble (`...`) and then becomes her reply:
  the bubble is the conversation.
- **Right-click menu / tray entry / hotkey** (default `ctrl+t`) focus the same
  inline input. There is no separate window any more.
- `mode: window` restores the original separate dialog, exactly as it shipped.

### Mixing typing and talking
A typed message runs through the **same pipeline as a voice turn**: commands
(`open spotify`, ...), conversation history, long-term memory, mood/expression
and the spoken reply all behave identically. Typed and spoken turns share one
conversation, so you can freely alternate between them.

- Voice is **unchanged** - the talk hotkey (`--hotkey`, default `F9`), click-to-
  talk and the wake word all keep working.
- A typed turn is ignored while she's mid-turn (you'll see a short note) so
  replies never interleave.
- If a screen capture is armed (Feature 4), a typed message can carry it too.

### Configuration - `config/text_input.yaml`
```yaml
enabled: true                 # false removes the input AND the menu entries
mode: inline                  # inline = in-bubble input | window = old dialog
menu_label: "..."             # right-click / tray entry label
placeholder: "..."            # separate-dialog prompt (mode: window only)
inline_placeholder: "Type here... Enter to send"   # inside the bubble
return_delay: 2.0              # input waits this long after her TTS ends
hotkey: "ctrl+t"              # focuses the inline input (also collision-checked)
```
Input is whitespace-normalised and capped at 1000 characters before it is sent.

Limits: single-line input; in inline mode the bubble no longer auto-hides (it
rests as the input); drag the pet by the face, not by the input; `mode`,
`hotkey` and `return_delay` are read at startup, so restart after editing them.

---

## Emotion Style Detection (Feature 6)

Reads the user's **last message** and appends a short scene-mood block to the
system prompt so the character can calibrate her delivery: sarcasm, joy, sadness,
anger, fear, confusion, disgust, calm, interest, boredom and sympathy, plus
hybrids (melancholy, anxious, bittersweet, disappointment, aggravation,
ambivalent).

- **Safety first:** a boundary phrase ("please stop", "leave me alone",
  "that's too far", ...) wins over every other match and tells her to halt the
  thread, acknowledge plainly, and give space.
- **Negation-aware:** "I'm not happy" does not read as joy; "I couldn't be
  happier" does.
- **Word-boundary matching:** "I made a cake" is not anger; "saddle" is not sad.
- Framed as **the user's tone** ("calibrate, don't mirror") so it never overrides
  the character's identity.
- At most 3 styles; direct antonyms are suppressed.

### Configuration - `config/emotion_styles.json`
```json
{
  "enabled": true,
  "max_styles": 3,
  "append_neutral": false,
  "safety": [],
  "hybrids": [],
  "emotions": [],
  "antonyms": {},
  "idioms": []
}
```

Set `enabled: false` to disable it. `append_neutral: true` adds a "Neutral" line
on every turn (off by default to save prompt tokens). The file hot-reloads on
change; no restart needed.

Limits: keyword-based (not an LLM classifier), English-only, and it only looks at
the message you just sent - not the whole scene.

---

## Sound Scripts (Feature 7)

When a message reads as intimate, a **sound-guidance block** is injected into the
system prompt so replies mix dialogue with onomatopoeia (breath, pleasure,
involuntary sound) instead of narration. Delivered as spoken text - the TTS says
`nnh`, `haahh~`, `mmh~`.

- **Triggered, not permanent:** hard patterns (explicit anatomy, acts, moaning,
  orgasm) arm it immediately; soft/ambiguous words (`kiss`, `wet`, `on the bed`,
  `harder`) need `soft_threshold` of them. It then **lingers `linger_turns`**
  ordinary turns and expires silently.
- **A boundary clears it at once** (Feature 6's `Boundary` match wins), so the
  sound script never survives the user pulling back.
- **Voice-first rewrite:** no narration, no asterisks, no stage directions - only
  words she says with sounds woven in. The four notation symbols (`~` `♥` `♡`
  `♪`) are allowed in the bubble but are **stripped from the spoken text** by
  `clean_for_speech`, so the engine never reads "heart" aloud.
- Not injected when the user's message is neutral, or when the feature is off.

### Configuration - `config/sound_scripts.json`
```json
{
  "enabled": true,
  "linger_turns": 4,
  "soft_threshold": 2,
  "trigger_patterns": [],
  "context_patterns": [],
  "state_line": "Currently engaged in intimate activity.",
  "system_note": "[SYSTEM NOTE: ...]"
}
```

Set `enabled: false` to disable it (any armed window is cleared too). The file
hot-reloads on change.

Limits: keyword/regex based, English-only, per-message - it does not track scene
continuity, and a word like `harder` still needs a second soft word to count.

---

## Mood-Driven Delivery (Feature 8)

The reply mood used to reach only the face (`live2d_moods.json`) and the
expression layer (Feature 3). Now it also shapes **how she speaks and moves**:

- **Prosody** - each mood scales TTS speed on top of the base `voice_speed`
  (`excited` 1.10x, `joyful` 1.05x, `sad` 0.90x, `aroused` 0.90x ...), clamped
  to the existing 0.5-2.0 range. Applied **per sentence**, so a mid-reply mood
  change slows only that stretch, and the TTS cache key includes the effective
  speed (two speeds of one sentence never share a cached clip).
- **Interjections** - a short spoken lead-in (`"Ha!"`, `"Nn...!"`, `"Oh?"`)
  prepended to the **first spoken line** of a reply, gated by `cooldown_turns`
  (default: one every 3 replies) and `chance`. Injected at speak time, so the
  bubble and the voice hear it while history/memory keep the model's original
  text.
- **Gesture fallback** - opt-in. Set `gesture` on a mood and, when the reply
  itself emitted no gesture/wink tag, one is appended (max 1 per reply, never
  past the parser's 4-tag budget). Shipped `null` for every mood so the
  model's own `[gesture:...]` tags keep priority.

### Configuration - `config/mood_delivery.json`
```json
{
  "enabled": true,
  "cooldown_turns": 3,
  "chance": 1.0,
  "moods": {
    "excited": { "speed": 1.10, "interjections": ["Yes!", "Oh,"], "gesture": null },
    "sad":     { "speed": 0.90, "interjections": ["...Mm.", "Oh,"], "gesture": null }
  }
}
```

`gesture` accepts an enabled name from `config/body_actions.json`
(`nod` / `shake` / `tilt`; `wink` is rejected there and needs a side). Set
`enabled: false` and the speed table goes empty and nothing is injected -
exactly the pre-Feature-8 behaviour. The file hot-reloads on change (no
restart); the speed table is re-read per sentence via an mtime-cached config.

Limits: keyed to the 10 canonical moods (extra keys work but ship unlisted);
interjections apply to streamed replies only - greetings, proactive lines and
fallbacks carry no mood tag and are untouched.


## Real SFX Audio (Feature 12)

Turns the Feature 7 sound-script symbols (`~ ♥ ♡ ♪`) and onomatopoeia
(`mwah`, `slurp`, `gasp`...) from silent bubble-only decoration into real
sound clips: when a sentence's TTS audio starts, one matching clip plays with
it - in sync with the bubble reveal, stopped on interruption.

### How to use it
1. Drop short clips into `assets/sfx/`, named `<stem><digits>.<ext>`
   (`breath_1.wav`, `breath2.ogg`, `heart_01.mp3`; wav/ogg/mp3/flac).
2. Nothing else - `config/sfx.yaml` ships with a symbol mapping that matches
   the Feature 7 prompt semantics:
   `♥` -> heart, `♡` -> tender, `♪` -> giggle, `~` -> breath;
   plus onomatopoeia regexes -> `kiss` / `suck` / `gasp`.
3. Multiple stems per category = one is picked at random. One clip per
   sentence at most, same category not repeated inside `cooldown` (2 s), any
   two clips separated by at least `gap` (0.4 s).

### Safety rails
- **No clips = no behaviour change.** Missing folder, bad file name, disabled
  config or a headless run silently degrade to the exact pre-SFX experience -
  the TTS pipeline is never touched (playback failures are logged and skipped).
- `enabled: false` in `config/sfx.yaml` removes everything instantly.
- The config hot-reloads on change; the clip folder list is scanned once per
  launch (add clips and restart once).
- Clips are user-provided, never shipped - keep them local, legal and
  consistent with the persona (adult, consensual - Eve is 34).

### Configuration - `config/sfx.yaml`
```yaml
enabled: true              # false = the feature is completely inert
folder: assets/sfx         # where to look for <stem><digits>.<ext> clips
volume: 0.5                # clip volume 0.0-1.0 (TTS volume is separate)
cooldown: 2.0              # seconds between two plays of the same category
gap: 0.4                   # min seconds between ANY two clips
max_per_sentence: 4        # cap on distinct triggers matched per sentence
triggers:                  # marker -> candidate clip stems
  "♥": [heart]
  "♡": [tender]
  "♪": [giggle]
  "~": [breath]
words:                     # onomatopoeia regex -> stems
  "mwah|mmwah|smooch": [kiss]
  "slurp|schlick|schlrp": [suck]
```

Limits: detection is textual (the same symbols the bubble shows) - there is no
semantic mood classifier; one clip max per sentence means you will not hear
every marker of a dense reply; the folder listing needs a restart after you
add/rename clips.




## Touch Reactions (Feature 13)

Poking and petting. Her Live2D body now has three touch zones - head, face
(cheeks), shoulders - and ONLY those three by design (no chest, nothing below
the waist). Zones live in model space, so they follow face resize (Ctrl+wheel),
model zoom (Alt+wheel) and pan (Alt/Shift+drag).

### Inputs
- **Quick click** - unchanged: hits her face via the Live2D hit-test and starts
  a talk turn (`click_action: talk`).
- **Ctrl+click on any zone** - a poke.
- **Press held still on the head zone for 0.6 s** - a pat. Move more than
  `cancel_px` (6 px) and the press becomes the ordinary window drag instead.
- Alt/Shift+drag pan and every existing modifier behaviour are untouched; a
  disabled feature never touches the input stack at all.
- Hovering the head shows an open-hand cursor.

### Reactions (escalating)
- Pokes: surprised (quick eye widen + head pull-back) -> playful protest
  (shake) -> shrug off (tilt). The poke counter decays with a ~15 s half-life.
- Pats: soft smile + contentment (eyes slowly soften toward closed + a small
  lean into it) -> blush -> a playful "okay, that's enough" followed by a short
  lockout. Pat credit accumulates (~30 s half-life) with how long she is held.
- Behaviour state matters: a sad valence makes pats warm her up sooner; a
  tired/low-arousal state makes her push back on pokes sooner.
- While she is SPEAKING a poke may only produce a small startle - her audio is
  never interrupted. While the mic records, all touch reactions are ignored.
  Proactive DND disables the feature entirely.

### Spoken reactions
Reactions can also be spoken (	ouch_voice: true): line pools in
config/touch_reactions.json by reaction and closeness tier (cold/warm/close,
warm_at 60 / close_at 120), PG-13, playful, never suggestive, never
reproachful. Rate-limited: at least 10 s between spoken reactions, hourly cap.
Lines go through Speaker.say() (the TTSCache keeps repeats cheap) with a
per-reaction mood tag. There is **no LLM call** - 	ouch_llm_reaction stays
false and is not wired to anything in this build.

### Debug + calibration
- Right-click menu on the pet: "Show touch zones (debug)" draws the zones over
  the model (they follow zoom/pan live). Zone starting points are guesses -
  calibrate them here first. If zones drift opposite to the model while you
  Alt+wheel, flip zoom_invert in the config (the app's pan formula and the
  preview tool's disagree on zoom direction).
- 	ools/live2d_preview.py has the same overlay plus editing: Z toggles,
  wheel selects, drag moves, [/] width, ,/. height, X saves the zones
  back into config/touch_reactions.json (only zones is written; the rest
  of the file is preserved).

### Files
- config/touch_reactions.json - everything: zones, thresholds, escalation,
  spoken pools, all toggles (enabled, 	ouch_voice, 	ouch_llm_reaction).
- src/core/touch.py - pure zone transform + input classifier (fake clock).
- src/touch_reactions.py - pure gating, escalation state machine, spoken
  pools + voice rate limiter.
- src/behavior_director.py - the two new procedural gestures (content,
  startle), written only into whitelisted director params (no physics
  params, no ParamSwitch10/11/21 ever).
- body_log: every press, classification, reaction and spoken line is logged
  with the [touch] prefix when ody_log: true in config/live2d.json.

---

## Drag Physics (Feature 14)

The model ships a full Cubism physics rig (physics3.json - 196 outputs for
hair, fringe, clothes, skirt, ears, chest). But its INPUTS are model
*parameters* (`ParamFaceAngleX`, `ParamChestAngleX`,
`ParamBodyAngleZ0`, `ParamBreath`...), never her screen position - so
until now, dragging her (Alt/Shift+drag pans the model, plain drag moves the
pet) left the hair perfectly still.

This adds an **inertia overlay** on top of the SDK's own physics:
- the *acceleration* of her body on screen (window drag + model pan) drives
  a spring-damper per parameter,
- the resulting offset is ADDED to the engine's value after `model.Update()`
  - the SDK's breathing and head-sway physics stay the base,
- steady movement does not swing anything; starts, stops and shakes do,
- on release the hair overshoots and settles back to rest.

### Configuration - config/drag_physics.json
```
{
  "enabled": true,              // false = the whole feature is inert
  "window_drag_gain": 0.8,      // how strongly dragging the pet drives it
  "model_drag_gain": 1.0,       // how strongly panning the model drives it
  "accel_smoothing": 0.6,       // smoothing of the measured acceleration
  "max_accel": 800.0,           // px/s^2 clamp
  "deadzone_px": 0.75,          // sub-pixel jitter never moves anything
  "stiffness": 14.0,            // spring constant (1/s^2) - lower = slower
  "damping": 5.0,               // damping (1/s) - 1.0 ratio = no overshoot
  "max_offset": 4.0,            // per-parameter clamp (param units)
  "sign": -1.0,                 // -1 = the hair lags behind the motion
  "gain_scale": 0.5,            // global strength multiplier (all groups)
  "groups": { "hair_side": {"axis": "x", "gain": 0.008, "params": [...]}, ... }
}
```
Groups: `hair_side` / `hair_vert` (side + vertical swing), `fringe`,
`clothes`, `skirt`, `ears`, `body` (a light chest deformation). Each
group has its own axis (`x`/`y`) and gain; a gain of `0` disables one
group. The parameter names are the rig's own outputs - swap the model and
they have to match the new rig.

The shipped gains are deliberately split: the big hair mass is cut down hard
(`hair_side` 0.008, `hair_vert` 0.006) while the smaller parts keep a little
life (`fringe` 0.025, `clothes` 0.020, `skirt` 0.018, `ears` 0.013,
`body` 0.008) - so the fringe currently moves the most. `gain_scale` (0.5)
halves every group at once: set it to 1.0 for twice the current strength,
2.0 for four times.

**Why the gains are small:** the spring's equilibrium is `drive / stiffness`,
so a gain large enough to slam the offset into `max_offset` inside a single
frame is what makes the hair *twitch* instead of sway. The shipped values
give a lazy, heavy swing: ~0.3 s to build up, ~1 s to settle, one soft
overshoot. Raise `gain_scale` (or a single group's gain) for more swing,
lower `stiffness` for a lazier one.

### Notes
- The overlay only ADDS to the soft outputs; it never writes the rig's input
  parameters (no `ParamFaceAngleX`/`ParamChestAngleX`/`ParamBodyAngle*`).
- The write is additive, so it cannot double-count and the SDK's physics
  engine keeps full ownership of those parameters.
- Right-click menu: "🧣 Drag physics: on/off" is a runtime switch; the config
  file stays the default.
- Tune the feel with `stiffness` (snappier = higher), `damping` (lower =
  more swing) and `gain` per group.

---

## Model Frame & Free Window Size (Feature 15)

Until now the model's on-screen size was derived from the window: the app
guessed px_per_unit = face_size / (2 * model_scale) while the preview tool
used width / 2 * scale - two opposite conventions for the same thing.  So
panning was slippery, resizing the window silently re-framed the model, and
the touch zones needed an nchor + zoom_invert calibration hack.

### The frame
A **frame** is a rectangle in MODEL space (the same space the touch zones and
the physics rig live in) that the face square shows:

`json
"face": { "size": 400, "frame": { "x": 12.3, "y": 40.1, "w": 24.0, "h": 24.0 } }
`

* **window size** -> only changes how many pixels the frame occupies,
* **model zoom** -> the frame's size,
* **model pan** -> the frame's position,
* the frame **survives a window resize** - the same model region stays
  visible, it just gets more (or fewer) pixels.

The frame is not guessed: the widget measures the runtime
(GetCanvasSize + GetCanvasSizePixel) for the exact pixels-per-model-unit,
and converts the frame into the scale/offset the SDK needs.  Panning now
follows the cursor exactly, and the app and the preview tool finally agree.

### Free window size
Four ways, all continuous (any pixel size, no 5% jumps):
* **drag the margin** - the empty strip beside the model square is an
  invisible resize handle (like a normal window border): grab it and drag.
  The cursor turns into a horizontal resize arrow when you are on it.
* **Ctrl+wheel** - 2% per notch, any size inside the limits.
* **"Face size" -> "Set size..."** - type the exact pixel size.
* **the presets** in the "Face size" menu, plus a **pet size slider** in the
  frame editor (preview tool).

### A tall window (vertical crop)
The window is a **rectangle**, not a square: the width is the size you pick
("face size"), and the **height follows the model frame's aspect**.  A taller
frame therefore means a taller window - and a taller window crops her body
vertically instead of showing more of her.  She is never stretched: the
frame's width always fills the window's width and the excess height is
cropped around the centre.

To change the shape, edit the frame's height in the frame editor (drag the
corner handle) - the running pet picks the change up live (it polls
`config/live2d.json`) and its window becomes taller/shorter immediately.

The size is a per-machine override (config/pet_state.json), the
frame is global (config/live2d.json).

### The frame editor
* Right-click the pet -> "Face size" -> **"Model frame..."** (it launches
  `tools/live2d_preview.py`).
* In the preview: **F** toggles frame mode - the canvas then shows exactly
  what the pet's window shows (same aspect, so a tall frame really is a
  tall window).  Drag = move the frame, the bottom-right corner = change its
  height (the vertical crop), wheel = zoom, "Save frame" writes
  config/live2d.json.
* The same panel has a **pet width slider** (writes config/pet_state.json).
* "Reset model frame" (pet menu) restores the default framing.

### The preview tool (tools/live2d_preview.py)
Three modes, one canvas, shown in the status bar:
* **free look** (default) - the sliders and the mouse drive the model
  directly (scale / offset = the "default framing" the pet resets to),
* **zones** [Z] - drag the touch zones on the model (they are stored in
  model units, so they stick to her body),
* **frame** [F] - drag the model frame.

The two editors are mutually exclusive (they share the canvas), and the
legacy framing sliders are disabled in frame mode because the frame is what
the pet actually uses.  The right panel is scrollable - there are seven
groups, and they do not fit into a 600 px tall window.

### Touch zones follow
The zones are now **frame-relative** by default (fractions of the visible
frame), so they are correct without any calibration.  Once you place them on
the model in the preview (zones mode), they are stored as absolute model
units (zones_relative: false) and stick to her body when the model is
panned or re-framed.  The old nchor key is obsolete and ignored.

---
---






# COMPANION-
#   C O M A P A N I O N - 
 
 
# Features 16-19: time-awareness, her own life, threads, interruption

## Feature 16 - Time-awareness (config/circadian.yaml)
She now knows the hour and what it means *for her*. Two hooks:
- a slow bias on her valence/arousal/engagement (applied from the pet's
  tick every ~5 s; half-life decay means it settles in over minutes), and
- a line in the system prompt so her words carry it too (a few short
  tiers: late night / dawn / daytime / evening, weekday and weekend
  variants; subject to `weekend_days` in the YAML).
`enabled: false` restores today's behaviour exactly (zero bias). Tiers
wrap past midnight (start 23, end 5). The same read replaces the
hand-rolled "time_hint" in generated proactive messages.

## Feature 17 - Her own life (config/agenda.yaml)
Evelyn has a day: weekday writing/editing blocks, meals, a wind-down,
looser weekend blocks. Two hooks:
- the system prompt always says what she is (or isn't) in the middle of
  ("...about 90 min left on 'deep writing'"), plus her free-time flavour,
- when the idle timer fires she sometimes opens with a small excuse
  about the running block ("Give me a sec - I'm right in the middle of
  ...") instead of a lonely line. Rate-limited: only busy kinds, at most
  N excuses per block, never twice within `min_excuse_gap_s`.
`enabled: false` and her day is permanently open.

## Feature 18 - Dangling topics (config/threads.yaml)
The "people follow up" feature. After some turns her companion quietly
asks the model whether the user mentioned anything with a definite
outcome LATER (appointments, deadlines, interviews...). The topic goes
to `config/threads_state.json`; at most ONE check-in per day, only once
each thread is older than `min_age_hours` (default 6 h) and younger
than `ttl_hours` (96 h); oldest first; never repeated.
**OFF by default** (`enabled: false`) because extraction costs one small
LLM call per user turn; flip `enabled: true` in threads.yaml to turn her
into the person who asks "how did the interview go?".

## Feature 19 - Interruption manners (config/interruption.yaml)
Barge-in by voice already existed. New:
- a RECOVERY line: when you cut her off while she was really mid-reply,
  she hands you the floor ("Hm? Go ahead, I'm listening."). Gated:
  min reply length, min gap (20 s), max 2 in a row (then a silent stop
  until ~10 min pass), so interrupts never become a "yes?" loop.
- keyboard SHUSH: ctrl+alt+x stops her voice mid-sentence (no turn
  launched, purely stopping audio). Configurable/disablable via `hotkey`.
- typing cuts in: sending a message while a turn is still running trips
  her interrupt monitor and waits up to 4 s for the turn to end,
  replacing the old "(still finishing the last turn)" bounce.

All four are independent toggles; tests: tests/test_circadian.py,
tests/test_agenda.py, tests/test_threads.py, tests/test_interrupt.py.

# Features 20: simple web search (config/websearch.yaml)

A real person does not know last night's score or today's weather -
she looks it up.

**Off by default** (`enabled: false`): enabling costs one tiny LLM
classification call per user turn, so you opt in deliberately.

## How it works

* On each user turn she quietly asks the model: "would the web help
  right now (news, weather, prices, current events)?" - reply is strict
  JSON `{"search": false}` or `{"search": true, "query": "..."}`.
* If yes: ONE DuckDuckGo query via the `ddgs` package (no API key) and
  the top ~5 snippets land in her SYSTEM context for that reply only.
* The same query inside `cache_hours` (3 h) is answered from the small
  on-disk cache `config/websearch_cache.json` - no re-hit of the
  provider. Failures are never cached; a failed lookup degrades to
  "couldn't look it up" guidance, never a broken turn.
* Search-triggered turns take ~2-4 s longer; all other turns are
  untouched. Only the chosen search words leave your machine - never
  your chat.

## Keys (`config/websearch.yaml`)

| key | meaning |
|---|---|
| `enabled` | turn the feature on/off (default off) |
| `max_results` | snippets injected (default 5) |
| `timeout_s` | per-search timeout in seconds (default 8) |
| `cache_hours` | TTL of the per-query cache (default 3) |
| `max_entries` | rows kept in the cache file (default 200) |

The engine is pure and testable (core/websearch.py; the ddgs import is
lazy so importing the app or running the suite needs neither the
package nor any network). Tests: tests/test_websearch.py.

All toggles are independent; to use it: flip `enabled: true` in
config/websearch.yaml and restart.

# Features 21+22: patient turn-taking + her listening noises
(config/listening.yaml)

A person doesn't answer the second you stop for breath, and doesn't
sit mute while you rattle on, either.

**Off by default** (`enabled: false`) - this changes her listening
manner; the old fixed capture (stop on 2.0 s of silence) is exactly
what runs while this is off.

## Feature 21 - patient turn-taking (endpointing)

* Every ~0.6 s of pause, a cheap partial Whisper call reads what you
  have said SO FAR (last `tail_window_s` seconds).
  - A tail that promises a continuation ("...and then we went to
    the", a hanging comma/dash, or `open_words`) buys her
    `max_grace_s` of extra patience (default 3.0 s) on top of
    `end_silence_s` (1.6 s).
  - A complete phrase (a closing mark, or a last word that isn't
    open-ended) ends the turn once `end_silence_s` is held.
  - Barely anything heard yet -> `min_words_grace_s` extra patience
    (you said one word, maybe about to continue).
* Config knobs: `end_silence_s` (1.6), `max_grace_s` (3.0),
  `min_words` (2), `open_words` (editable list).
* Safety nets: the outer `max_seconds` and an extreme-silence stop
  (`end_silence_s + max_grace_s + 2 s`) can never hang.

## Feature 22 - listening noises (backchannels)

* Mid-monologue, in the silence window (`backchannel_from_s` 0.9 s to
  `backchannel_to_s` 1.4 s), after `backchannel_min_speech_s` (6 s)
  of you talking and no more often than `backchannel_gap_s` (9 s),
  she plays one tiny line ("Mhm.", "Right, right...", "No way.").
  You keep the floor - these are non-turn-taking.
* The recorder drops the echoed mic tail (`mute_s`, 0.7 s) right
  after her clip so her voice never pollutes your transcript.
* The line bank lives in the same YAML; EMPTY it and the noises stop.

Both run inside the standard capture (core/recording.py gained the
OPTIONAL `pause_judge` hook and a configurable preroll; with no judge
the recorder is byte-identical to before). The engine is pure:
core/turn_taking.py (policy only, explicit inputs). Tests:
tests/test_turn_taking.py (+ the re-routed capture patch in
tests/test_text_input.py).

To use: flip `enabled: true` in config/listening.yaml and restart.
Tune patience live with `/reload`-less config restarts, then tune
`backchannel_gap_s` to taste.

# Feature 23: her ears - Whisper large-v3-turbo
(src/core/config.py -> resolve_whisper_settings)

Her auto-pick on an NVIDIA GPU was large-v3; it is now **large-v3-turbo**:
the same encoder, 4 decoder layers instead of 32 - several times faster
transcription at about half the VRAM (~1.6 GB float16 vs ~3.1 GB), which
matters next to Live2D on a 4 GB card. English accuracy is within noise
of large-v3. The no-GPU fallback (small / cpu / int8) is unchanged.

* The turbo weights download ONCE from Hugging Face on first launch
  (~1.6 GB), then run fully local and offline from the HF cache.
* Also new: personality.json may pin the ears explicitly - add
  `"whisper_model": "large-v3"` (and optionally `whisper_device`,
  `whisper_compute_type`) at top level and restart to roll back or
  experiment. Any error there logs and keeps the old behavior.
* Bonus: Feature 21's mid-pause partial checks decode visibly faster.

Tests: tests/test_whisper_turbo.py (6).

# Bug fix (Feature 23b): one stalled TTS call can no longer mute her
(src/core/audio.py Speaker._synth_guarded; src/she.py; config tts_synth_timeout_s)

On 2026-10-31 03:14 the Fish Audio API returned HTTP headers for one
sentence and then stalled mid-stream. Her ONLY synthesis thread sat
blocked inside that read for the provider's whole 240-second read
timeout - every reply queued behind it went silent while STT worked
fine. New behaviour:

* Every synth attempt runs on a throwaway daemon thread with a hard
  wall-clock cap (`tts_synth_timeout_s`, default 30 s) and is retried
  once; a still-stuck sentence is dropped and the queue moves on.
  Normal syntheses (~1-3 s) behave exactly as before.
* The Fish HTTP client itself is built with the same cap, so stuck
  body reads die at 30 s instead of 240 s.
* Tunable via personality.json (`tts_synth_timeout_s`, floor 10 s); a
  /reload applies it live (watchdog field + client rebuild).

Tests: tests/test_tts_timeout.py (9).

# Feature 24: long mode - she unpacks when you actually ask
(config/verbosity.yaml; src/core/verbosity.py; wiring in she.py + app.py)

Her persona keeps replies short (two-to-four spoken sentences) on purpose
- that is what a voice companion should sound like. When you really want
the full story, you can now ASK for it:

* `/long` in the text box (or console) = sticky long mode for the
  session; `/short` puts it back. Before each turn the right override
  paragraph is appended LAST so it beats the persona's length rules.
* Phrase triggers stretch a SINGLE turn only (never sticky): "tell me
  everything", "go on", "keep going", "don't stop", "the long
  story", "unpack it", "walk me through it" - word-boundary,
  configurable in the YAML.
* The override also cancels her end-on-stacked-questions habit (at
  most one short question, or a statement to land).
* Not a conversation turn: the toggle says one confirm line and takes
  no history/memory/LLM call. Feedback/commands are feature-owned
  lines in the YAML; personality.json stays untouched.
* Drawbacks worth knowing: spoken long turns are minutes of listening
  (~150 wpm) and chain many TTS synth calls (per-sentence failure
  risk is one sentence thanks to Feature 23b) - so the mode is
  ask-scoped by design, not always-on.
* OFF by default (`enabled: false`): while off, /long is an unknown
  command and the phrases are just words.

Tests: tests/test_verbosity.py (16).

# Feature 25: post-flow grace - she keeps listening after deciding
(recording.py post_grace_s; config/listening.yaml; tests/test_post_grace.py)

The complaint this fixes: she could STILL cut you off the moment she
decided you were done (silence threshold met, or the Feature-21 judge
saying 'done') - and anything you added right after was lost.

* Once she decides you're done, she keeps listening `post_grace_s`
  extra seconds (yaml ships 3.0).  Speak again INSIDE the window and
  she folds it into the SAME recording - the cutoff is reversed and
  the whole listen/think loop continues (Feature 21 keeps judging the
  next pause).  Stay quiet and the stop fires just that many seconds
  later.
* Costs latency when you DO finish: every turn's reply now starts up
  to `post_grace_s` later.  Lower it / set 0 (old behavior) to taste.
* Needs the master `enabled: true` in config/listening.yaml.

# Bug fix (found while building this): silent config-file fallbacks
Seven core modules resolved their config dir ONE level short
(src/core -> src instead of the project root), so their YAML never
loaded and every runtime config sat on code defaults without any
error: turn_taking (Features 21+22 - your listening.yaml flip would
have been IGNORED), verbosity (Feature 24), websearch (Feature 20),
plus circadian/agenda/threads/interruption (Features 16-18).  Now all
use the standard three-levels-up path like config.py.  A canary
phrase in config/verbosity.yaml locks the regression (its absence
from the loaded config fails a test).

# Feature 26a: brain swap by .env + conversation temperature fix
(core/config.py CEREBRAS_MODEL override; config/personality.json temperature 1.5 -> 1.0; tests/test_model_choice.py)

The A/B that mattered: Cerebras rotated its lineup to only two minds
- gpt-oss-120b (the old default) and qwen-3.8-27b.  Same persona, same
prompts, same temperature, side by side: gpt-oss performed a REsume of
Eve ('a carousel of viewings, a couple of high-stakes negotiations')
while qwen answered like a PERSON ('Did you check for whiplash? ... I
sold nothing today ... you look like you need someone to be a little
useless for once.').  Aliveness lives in the model, so:

* CEREBRAS_MODEL=qwen-3.8-27b is now set in .env (her live brain).
* Flip that one line to gpt-oss-120b + restart to compare ears-on.
* Temperature dropped from 1.5 to 1.0: 1.5 was cooking the 120B into
  wobble; 1.0 is the sweet spot for both.
* Watching for: qwen is smaller (27B) - in long sessions check she
  still remembers established facts and keeps the body-tag format out
  of spoken text.  If she drifts or narrates aloud in brackets, fall
  back to the old line and say so.

# Feature 12b: SFX clips in HER OWN voice (tools/gen_sfx.py)
assets/sfx shipped empty - no stock pack matches her Fish voice.  The
new tool synthesises 2 clips per stem with the SAME client/voice/model
the app speaks with, decodes, trims leading/trailing silence,
peak-normalises and writes assets/sfx/<stem>_<n>.wav.  A 50 s 'gasp'
hallucination from the free tier is why MAX_CLIP_S=4.0 discards
anything longer.  Run:
    .venv\Scripts\python.exe tools\gen_sfx.py [--dry-run|--force|--only X]
Restart once after generating (the clip folder is scanned at startup).
14 clips shipped: heart/tender/giggle/breath/kiss/suck/gasp x2.

# Config/test reconciliation (user enabled features at runtime)
listening.yaml, verbosity.yaml, emotions.yaml and body_actions.json now
carry the user's enabled:true choices - kept as-is.  Stale tests that
asserted 'shipped off' were reworked to validate STRUCTURE instead of
state: emotions (mood -> registered expression, all moods present),
verbosity (types + yaml canary), hand gestures (listing agrees with the
enabled flag), SFX no-op tests (hermetic empty folder).  Code fallback
DEFAULTS in turn_taking.py/verbosity.py are back to False so a missing
yaml never forces features on; the yaml files stay enabled.
One real config bug fixed: emotions.yaml mapped idle/relaxed to the
'relaxed' expression, which is not registered (no such .exp3.json) -
both are null now (no expression), matching the built-in defaults.

# Feature 12c: words become real sounds at the exact spot (splice)
The old path spoke "mwah" AND fired kiss_1.wav (also her voice saying
"mwah") at the sentence start - a double-spoken word, misplaced.  Now
config/sfx.yaml `splice: true` splits every sentence at its triggers
(core/sound_splice.py): the spoken fragments synth individually and a
cue part (no audio) plays between them, so the word itself is never read
aloud and the sound lands exactly where it was.  A 0.25 s beat
(cue_beat_s) lets the cue land before the next words.  The bubble still
shows the word - only TTS loses it.  Missing clip = the word stays
spoken (nothing silently vanishes); splice: false restores the old
behaviour.  Foley: TTS can only articulate, so tools/gen_sfx_foley.py
synthesises kiss/breath/suck/gasp with numpy (deterministic, offline,
royalty-free) and overwrites those 8 voice files; heart/tender/giggle
stay her voice because they ARE vocalisations.  Run:
    .venv\Scripts\python.exe tools\gen_sfx_foley.py [--dry-run|--force|--only X]
Tests: test_sound_splice.py (splitter/config), test_sfx_foley.py
(durations/levels/determinism/naming); wiring tripwires updated.
Gate 737 passed / 1 known proactive failure.

# Feature 12d: the rest of the sound-script vocabulary
Seven new cues, mapped to the sounds the prompt already asks her to write.
Her voice (tools/gen_sfx.py): moan (mmm/mmh/mmph), whimper (hnnng/nngh/
nnh), sigh (sigh/haaah/aahh), laugh (hehehe/haha/teehee) and purr
(prrr/mrrr/mrrp).  Procedural foley (tools/gen_sfx_foley.py): heartbeat
(ba-dum/thump-thump), sniff (sniff/snff) and gulp (gulp/glp).  Foley now
ships 3 takes per stem; the voice stems gained a third take where Fish
cooperated (tender_3/giggle_3 needed one retry - the two 47.6 s
hallucinations were discarded by MAX_CLIP_S as designed).  41 wavs
total; every mapped stem resolves.
Ownership fix: gen_sfx.py no longer lists kiss/suck/gasp (already foley
since 12c) or breath - a new tripwire test
(test_voice_and_foley_stems_never_overlap) proved breath existed in BOTH
generators, so a --force run of the voice tool could have overwritten its
foley with a spoken word.  Voice tool = vocalisations only; foley tool =
noise transients.
config/sound_scripts.json gained a playful line (hehe, haha, teehee,
prrr, mrrp, ba-dum, thump-thump, sniff, gulp) so the model actually
emits the new tokens.  Tests: expanded detection + no-false-positive +
yaml/defaults sync, splice coverage for all 8 new words, foley
durations/levels/3 variants.  Gate 742 passed / 1 known proactive
failure.

# Test hygiene: proactive config test follows the user's tuning
test_config_loads_proactive_fields used to pin 30/3/false from an older
personality.json, so the user's tuned 20/20/true turned it into a
permanent failure.  It now reads config/personality.json and asserts the
loader agrees with the file, plus structural invariants (positive
interval, 1+ daily cap, 0-23 quiet hours, pools present, idle excluded
from moods).  Numeric/bool knobs stay user-owned; persona text is
untouched.  Gate: 743 passed / 0 failed.

# Fix: model flicker on hover / when the speech bubble appeared
Revealing the speech panel used to resize the translucent pet window,
move it, and only then reposition the OpenGL face - so the model sat a
panel-width away from its parked spot for one frame (the flicker) on
every hover or new sentence.  The side column's width is now reserved
permanently: the window never resizes or moves on reveal.  The panel
just appears inside the reserved column, and a window MASK expands over
it.  While the panel is hidden the mask is the old compact frame, so
the empty column neither paints nor swallows desktop clicks/hover.
Parked position still stores the compact frame (restore shifts the real
window left by the reserved width), so a restart puts her back on the
same spot.  Tests: test_reveal_never_resizes_or_moves_the_window pins
the window geometry and the mask; test_parked_position_roundtrips_with_
the_reserved_column pins the save/restore offset.  Gate: 745 passed.
# Canon: Evelyn, Marin-era memories retired
2026-10-31. Evelyn is the canon character. The two old `summaries.json`
entries that injected Marin ("cosplay enthusiast and part-time model",
"no internet access") into every turn are archived to
`config/archive/marin_summaries.json` and the live file is now empty.
Pre-Evelyn episode logs moved to `config/archive/episodes/`. The vector
collection was renamed `marin_memories` -> `evelyn_memories` so old
semantic memories are never queried again (the old collection stays on
disk untouched). `config/memory.json` was rebuilt from ~45 facts to 11
durable third-person user facts (music, Visual Kei, lives in Guwahati,
lives alone, games, draws, the Moon Morning set); everything ephemeral
("I have chicken", "It's almost 2 a.m.") or meta ("search the internet
before answering") is archived.

# Feature 27: aliveness layer + realtor life + honest memory intake
Fixes the flatness bugs found in the personality audit without touching
persona text:

**New `config/aliveness.yaml` (feature-owned, `refresh /reload`-safe):**
- `turn_shape` — end on statements, never two consecutive
  questions, max one question per reply. The interview cadence
  (52% of replies ended in "?") traced to the persona's "End on a
  hook: a question" — this block is appended after the persona and
  overrides it without editing the card.
- `listening` — pick up one specific thing the user said; no
  therapist boilerplate ("that sounds hard").
- `in_character_fallback` — never mention being offline, access,
  tools, options lists, or "anything else?"; anti-assistant leak.
- `opinions` + `example exchanges` — fewer generic reactions.
- `closeness_tiers_extra` (150, 200) — at score 200 she now gets the
  settled-hint instead of the honeymoon/sexual tier; the fixed
  persona tiers always win if the user defines the same score.
- `proactive_instruction` — the proactive path now generates with her
  FULL system prompt (persona + memory + agenda + circadian + last
  exchange) and this instruction. The generic "You are {name}, a
  companion..." card is gone. A due dangling thread is asked BEFORE
  generating. Mood tags from generated lines are parsed, applied to
  the face and stripped before TTS/bubble.
- `daily_summary` — on shutdown, the current session is summarized
  even when it never hit the old 40-message trigger, so "Recent
  conversation history" is Evelyn's from yesterday, not frozen
  Marin text from Oct 10.
- `question_guard` — a hard one-turn append: if the previous assistant
  reply ended with a question, the next one gets an explicit
  "this one must not" line at the very end of the system prompt.

**RealTOR life:** `config/agenda.yaml` + `core/agenda.py` defaults
rewritten from the leftover writer schedule (chapter draft, editing
deadline) to Evelyn's real one: 5:40 espresso/skincare, 8:00 calls and
early showings, 10:30 listing work, showings/paperwork until six, wine
and no phone at 20:00; weekends are farmers market and a gallery
opening she leaves after twenty minutes. `config/circadian.yaml`
wording: tea -> espresso, flat -> apartment.

**Honest memory intake:** new pure `core/memory_hygiene.py`
(`clean_fact` / `is_durable_fact` / `parse_facts`). `_extract_facts`
now asks for third-person JSON and stores only durable user facts —
ephemeral state, meta/tool instructions, assistant lines, questions
and vague fragments are dropped at the filter, not after they landed
in memory. `/reload` now also pushes merged closeness tiers into the
live RelationshipTracker.

`config/personality.json` was NOT modified.  Tests: +31
(`test_aliveness.py`, `test_memory_hygiene.py`, agenda canon guard,
agenda label updates).  Gate: 776 passed / 0 failed.
# Feature 27b: same-day restart continuity
2026-10-31. The rolling conversation window lives in RAM, so every
run.bat launch started with amnesia even though today's transcript was
already on disk: restart at 3pm and breakfast-she didn't exist anymore.
On init, VoiceCompanion now replays the recent tail of TODAY'S episode
log into history (config/aliveness.yaml 'restart_history', shipped as
40) and seeds last_exchange from the log's closing pair, so the first
turn after a restart opens with continuity and 'Last exchange' filled.
The replay is capped at the same ceiling _trim_history enforces
(max(max_history, summarize_after * 2)), so a restored window can never
trip the in-session summariser on turn one. Midnight is a natural
cutoff (yesterday is not replayed). Pure helpers select_replay /
last_exchange_pair live in core/aliveness.py; the pool knob is
feature-owned, personality.json untouched. Tests: +13
(test_restart_continuity.py - selection, loader, real episode-store
path, launch wiring, ceiling). Gate: 789 passed / 0 failed.

# Feature 30: cadence measurement tool (Tier 1 #3)
tools/measure_cadence.py reads config/episodes/<date>.json and prints
per-day cadence stats: assistant messages, % ending with a question,
% containing one, avg length, plus all-day weighted totals
(--json for machine output).  This is the before/after ruler for the
aliveness layer: baseline Oct 31 = 54.1% end-in-question on 133
messages (before the layer shipped); Oct 30 = 32.4%.  Run it after a
session with the new build to see whether the turn-shape block moved
her off the interview cadence.  Pure analyze()/load_days() are tested:
tests/test_measure_cadence.py +5.  Gate: 794 passed.

# Feature 28: silent narration (Tier 1 #2)
She can act, not only talk: prompt block (feature-owned, in
config/aliveness.yaml) teaches one short **stage beat** per reply;
core/narration.py is a pure splitter; every speaking path (body-control
_speak, legacy and CLI sentence splits, proactive) now queues only the
voice words and diverts narration spans to the bubble display instead.
Stray asterisks can never be voiced - stripping is unconditional and
defensive.  Narration stays in history/episodes (it teaches the format
and keeps continuity); body tags attached to narration-only frames are
dropped (tag-free beats are what the prompt teaches).  +14 tests (pure
splitter + tripwires)

# Feature 29: self-written memory (Tier 1 #4)
She decides what to remember: at the end of a turn she may write one
private {{remember: ...}} note (curly braces so the body/mood tag
parser leaves it alone).  It is never spoken and never shown in the
bubble; after the turn completes it is harvested through the same
durable-fact hygiene as auto-extraction (clean/durable/dedupe), gated
by config/aliveness.yaml self_notes (true) with max_notes_per_day 5,
and stored to memory.json + the vector store by VoiceCompanion.
_harvest_memory_notes runs BEFORE passive extraction so equivalents never
duplicate.  CLI and GUI turn paths share the harvest.  +15 tests incl.
a functional harvest through the real Companion class object and the
gate/cap behavior.  Tests total +34; gate: 823 passed / 0 failed.

# Phase 1 close-out: startup hygiene, voice-failure UX, backup + doctor
2026-11-15.  The remaining Phase 1 polish landed together; all changes
are feature-owned and the persona file was not touched.

- Logs: `core.config.setup_logging` now writes INFO by default instead
  of DEBUG, with 5 MB x 3 rotation (the old 10 MB x 5 was mostly HTTP
  noise).  `EVELYN_LOG_LEVEL=DEBUG` restores verbose logging;
  `EVELYN_LOG_MAX_MB` and `EVELYN_LOG_BACKUPS` override the bounds.
  Noisy client libraries go to WARNING in normal mode.
- Cerebras: the card's five stop sequences are expected to exceed the
  provider cap.  `core.model.cap_stop_sequences` trims/dedupes to four
  once, logs the trim at INFO, and no longer warns every launch.
- Whisper: `core.whisper_cache.load_cached` tries `local_files_only=True`
  first, so warm starts skip the HuggingFace revision check; a true
  cache miss falls back to the normal download.  Startup logs whether
  the model came from cache or download.
- Voice failures: after the existing 30 s cap + one retry, Speaker
  invokes `on_tts_failure`; the app emits `Bus.voice_issue` and the pet
  appends a one-time "(Voice unavailable - the reply is in the bubble.)"
  note.  The text bubble stays visible, the note is display-only (never
  spoken), and the bubble/model layout is unchanged.  Notices are
  rate-limited to 20 s.
- Backups: `tools/backup_data.py` zips `config/memory.json`,
  relationship/runtime state, `config/episodes/`, `config/archive/`, and
  `memory_store/`.  Restore refuses to overwrite unless `--force`, and a
  forced restore writes a `pre-restore-...zip` first; archive members
  are checked for traversal and symlinks.
- Memory doctor: `tools/memory_doctor.py` compares facts and current
  summaries with vector IDs/documents (read-only), reports missing,
  stale, and changed items, and exits 1 on drift.  Live check:
  15 facts + 3 summaries = 18 vectors, all matching.
- Cadence ruler: `tools/measure_cadence.py` now strips silent
  `**narration**` and `{{remember: ...}}` notes before counting
  questions, and reports beats/notes.  After-layer sample (Nov 5-8,
  n=17): 1 end-question (5.9%), 3 contain-question (17.6%), 5 narration
  messages, 2 one-word labels, 0 self-notes.
- Prompt quality: only the feature-owned narration block changed - it
  now forbids bare body labels (`**tilt**`, `**wink**`) and asks for a
  full subject+action beat (`**she tilts her head**`).  Self-notes were
  not tuned; the sample had no durable user reveal worth a note.
- Episodes: 2026-10-09 through 2026-10-18 were moved (bytes verified,
  never deleted) from `config/episodes/` to
  `config/archive/episodes/`; Oct 30 onward stays active.

Tests: +35 (`test_startup_polish`, TTS UX, cadence narration/notes,
backup/restore, memory doctor).  Gate: 875 passed / 0 failed.


