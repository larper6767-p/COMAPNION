# Evelyn

Evelyn is a local-first desktop AI companion: you speak or type, a cloud or local
LLM answers, her voice is synthesized, and a Live2D (or sprite) pet reacts in a
frameless desktop window. She keeps a small, inspectable memory of facts,
conversations, and daily summaries on your machine.

This is the current entry point for the project. The original build log lives in
[CHANGELOG.md](CHANGELOG.md); the module map is in
[ARCHITECTURE.md](ARCHITECTURE.md); the body/animation design is in
[BEHAVIOR_DIRECTOR.md](BEHAVIOR_DIRECTOR.md).

---

## Quick start (Windows)

1. Install **Python 3.11-3.14** from <https://www.python.org/downloads/> and tick
   **Add Python to PATH**.
2. Double-click **`setup.bat`**. It creates `.venv` and installs the exact tested
   versions from `requirements.lock`.
3. Double-click **`run.bat`**. On the first launch the app asks for your two API
   keys and writes them to `.env`.

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.lock
Copy-Item .env.example .env
.\.venv\Scripts\python.exe src\app.py
```

Run the terminal companion instead with:

```powershell
.\.venv\Scripts\python.exe src\she.py
```

---

## Keys and services

| Service | Used for | Where to get a key |
|---|---|---|
| **Cerebras** | Cloud LLM replies (default) | <https://cloud.cerebras.ai> |
| **Fish Audio** | Text-to-speech | <https://fish.audio> |

Put keys in `.env`:

```dotenv
FISH_API_KEY=your_fish_audio_key
CEREBRAS_API_KEY=your_cerebras_key
```

Ollama can be used as a local fallback; see `personality.json`. Whisper
speech-to-text runs locally. Web search uses DuckDuckGo through `ddgs` and needs
no key.

> Never commit or share `.env`. `.env.example` contains placeholders only.

---

## What she can do

- **Voice and typing** - voice activity detection, interrupt handling, patient
  turn-taking with listening noises, a wake word (`hello eve` by default), and an
  inline bubble input via `Ctrl+T`.
- **LLM conversation** - streaming replies, a Cerebras backend with an Ollama
  fallback, in-character error handling, and a turn shape that avoids ending
  every reply with a question.
- **Voice output** - Fish Audio TTS, mood-aware delivery, LRU phrase cache,
  optional Windows audio ducking, and a visible fallback note if TTS fails.
- **Persistent memory** - durable facts, dated episode transcripts, daily
  summaries, and optional local ChromaDB vectors (`evelyn_memories`). Startup
  synchronizes JSON facts and summaries into the vector store.
- **Aliveness** - feature-owned prompt blocks for turn shape, listening,
  narration, opinions, and self-written memory notes. Prompt changes are measured
  with `tools/measure_cadence.py` before they are kept.
- **Prompt budget** - per-turn size estimates by section, optional rolling
  history cap, provider usage reporting, `/stats`, and
  `tools/measure_prompt.py`; see [PROMPT_BUDGET.md](PROMPT_BUDGET.md).
- **Desktop pet** - Live2D renderer with sprite fallback, mood crossfades, idle
  movement, gaze, drag physics, touch reactions, speech bubble, tray menu, and
  proactive idle messages.
- **Tool-like senses** - on-demand screen vision (disabled by default), web
  search (enabled), and a circadian/agenda/thread layer that gives her a
  consistent sense of time and her own day.

Evelyn is the canon character. Marin-era data is archived, not deleted; the app
never reads the old `marin_memories` vector collection.

---

## Daily use

| Action | How |
|---|---|
| Talk | Press `F9` (default) or left-click the pet |
| Type | `Ctrl+T`, or right-click -> Type a message |
| Wake word | Say `hello eve` when enabled |
| Menu | Right-click the pet; tray icon has show/hide, mute, quit |
| Screen vision | `Ctrl+Alt+S` when enabled in `config/vision.yaml` |
| Resize / frame | `Ctrl+wheel` resizes; `Alt+wheel` zooms; `Alt`/`Shift` drag pans |
| CLI commands | `/remember`, `/forget`, `/mute`, `/volume`, `/speed`, `/reload`, `/stats`, `/t`, `/quit` |

The speech bubble stays on the left of the model, with a 2 px gap. It never
moves or resizes the model window.

---

## Project layout

```text
.
├── src/
│   ├── app.py                 GUI launcher, controller, and Pet window
│   ├── gui/                   Qt widgets, signal bus, art loading, background helpers
│   ├── she.py                 VoiceCompanion: CLI and shared turn pipeline
│   ├── core/                  config, model, audio, recording, memory, feature logic
│   ├── memory/                JSON + ChromaDB vector-store adapters
│   ├── emotion/               expressions, mood delivery, sound scripts
│   ├── vision/                on-demand screen capture
│   ├── live2d_face.py         Live2D renderer
│   ├── behavior_state.py      pure internal-state model
│   └── behavior_director.py   motion/gaze/blink director
├── config/                    personality, feature YAML/JSON, episodes, archive
├── characters/                character art and Live2D model assets
├── tools/                     backup, memory doctor, cadence, preview, diagnostics
├── tests/                     automated suite
├── READMES/                   setup guide, architecture, behavior notes, changelog
├── logs/                      rotating runtime logs (created at runtime)
├── memory_store/              local vector database (created when enabled)
├── .env                       private API keys (never commit)
├── setup.bat / run.bat        Windows setup and launcher
└── diagnose.bat               writes diagnose_out.txt
```

The `characters/marin/` directory name is retained for compatibility with the
installed Live2D assets. The character and all active data are Evelyn.

---

## Configuration map

`config/personality.json` is the character card: identity, voice, mood list,
wake word, proactive limits, closeness tiers, and the system prompt. Edit it only
when you intend to change the character.

Feature behavior lives in separate, feature-owned files:

| File | Purpose |
|---|---|
| `config/aliveness.yaml` | Turn shape, listening, narration, self-notes, opinions |
| `config/memory.yaml` | Vector memory, collection, embedding, `top_k` |
| `config/live2d.json` | Renderer, face size/frame, body control, motion budget |
| `config/behavior_state.json` | Behavior-state tuning |
| `config/body_actions.json` | Mood/body-action vocabulary |
| `config/touch_reactions.json` | Touch zones and reactions |
| `config/drag_physics.json` | Drag inertia and limits |
| `config/emotions.yaml` | Expression mapping |
| `config/emotion_styles.json` | Emotion style rules |
| `config/mood_delivery.json` | Mood-to-delivery mapping |
| `config/sound_scripts.json` | Sound-script vocabulary |
| `config/sfx.yaml` | Real sound-effect clips |
| `config/vision.yaml` | Optional screen capture |
| `config/text_input.yaml` | Inline/window text input |
| `config/listening.yaml` | Endpointing and listening noises |
| `config/circadian.yaml` | Time-of-day behavior |
| `config/agenda.yaml` | Simulated daily agenda |
| `config/threads.yaml` | Dangling-topic follow-ups |
| `config/interruption.yaml` | Interruption recovery |
| `config/websearch.yaml` | Web search limits and cache |
| `config/verbosity.yaml` | Reply length policy |
| `config/prompt_budget.yaml` | Prompt size accounting and optional history budget |

Runtime state is auto-managed in `config/memory.json`,
`config/relationship.json`, `config/pet_state.json`, `config/episodes/`, and
`config/threads_state.json`. Do not hand-edit runtime state while the app is
running.

### Logging

Logs default to `INFO`, rotated at 5 MB x 3. For a verbose local run:

```powershell
$env:EVELYN_LOG_LEVEL = "DEBUG"
.\.venv\Scripts\python.exe src\app.py
```

`EVELYN_LOG_MAX_MB` and `EVELYN_LOG_BACKUPS` override the rotation bounds.
Logs are written to `logs/evelyn.log`.

---

## Memory, backup, and restore

Back up before changing memory or restoring anything. Close the companion before
restoring the vector store.

```powershell
# Create backups/evelyn-backup-YYYYMMDD-HHMMSS.zip
.\.venv\Scripts\python.exe tools\backup_data.py

# Choose a different destination
.\.venv\Scripts\python.exe tools\backup_data.py --out D:\EvelynBackups

# Restore only when you mean it; a forced restore makes a pre-restore backup
.\.venv\Scripts\python.exe tools\backup_data.py --restore backups\evelyn-backup-....zip --force

# Read-only drift check: JSON facts/summaries vs. vector contents
.\.venv\Scripts\python.exe tools\memory_doctor.py
.\.venv\Scripts\python.exe tools\memory_doctor.py --json
```

The backup includes `config/memory.json`, relationship/runtime state,
`config/episodes/`, `config/archive/`, and `memory_store/`. Restore validates
archive paths, refuses to overwrite unless forced, and creates a
`pre-restore-*.zip` first.

To reset only what she remembers, stop the app and move (do not delete)
`config/memory.json`, `config/episodes/`, and `config/relationship.json` into a
safe folder. Archive old conversations under `config/archive/episodes/` rather
than deleting them.

---

## Tools

| Tool | What it does |
|---|---|
| `tools/backup_data.py` | Create or restore a portable data backup |
| `tools/memory_doctor.py` | Read-only JSON/vector drift report |
| `tools/measure_cadence.py` | Question/beat/note metrics from episodes |
| `tools/measure_prompt.py` | Prompt-size and provider-usage summary from logs |
| `tools/live2d_preview.py` | Tune framing, liveliness, and body behavior |
| `tools/diagnose_live2d.py` | Live2D/system diagnostics (`diagnose.bat`) |
| `tools/gen_sfx.py` | Generate SFX clips in her voice |
| `tools/gen_sfx_foley.py` | Generate foley clips |
| `tools/behavior_state_table.py` | Print behavior-state response tables |
| `tools/demo_body_tags.py` | Exercise body-tag parsing/actions |
| `tools/build_release.py` | Build a private release bundle |

Cadence check:

```powershell
.\.venv\Scripts\python.exe tools\measure_cadence.py --days 7
.\.venv\Scripts\python.exe tools\measure_cadence.py --json
```

The tool strips `**narration**` and `{{remember: ...}}` notes before counting
questions, so prompt-quality decisions use spoken text only.

---

## Testing

The gate is the full suite; it must stay green after every change:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

The suite is headless-friendly and covers configuration, memory hygiene, vector
sync, prompt blocks, audio failure handling, GUI geometry, and the behavioral
director. Add a focused test with every behavior change, and keep a manual
checklist for anything that needs a real window, microphone, or speakers.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `Python not found` during setup | Reinstall Python with **Add Python to PATH** |
| No reply or no voice | Check `CEREBRAS_API_KEY` and `FISH_API_KEY` in `.env` |
| First reply is slow | Whisper downloads its model once, then uses the cache |
| App will not start | Run `diagnose.bat`, inspect `diagnose_out.txt` and `logs/` |
| Memory seems inconsistent | Run `tools/memory_doctor.py`; back up before restoring |
| TTS fails | The reply stays in the bubble with a one-time voice-unavailable note |
| No Live2D model | The sprite fallback is intentional; check `face_renderer` in `config/live2d.json` |

---

## Documentation index

- [ARCHITECTURE.md](ARCHITECTURE.md) - current runtime/module/data-flow map.
- [BEHAVIOR_DIRECTOR.md](BEHAVIOR_DIRECTOR.md) - behavior state, motion budget,
  gaze, and Live2D integration.
- [PROMPT_BUDGET.md](PROMPT_BUDGET.md) - prompt-size observability, budgets,
  logs, and `/stats`.
- [CHANGELOG.md](CHANGELOG.md) - the historical feature-by-feature build log.
- [README_FIRST.md](README_FIRST.md) - shortest friend-test setup path.
