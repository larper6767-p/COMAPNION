# Architecture

Evelyn is a single-process Python application with two entry points:

- `src/app.py` - desktop pet GUI (PySide6).
- `src/she.py` - terminal companion; also owns the shared `VoiceCompanion`
  turn pipeline used by the GUI.

Both entry points build the same core services: `Config`, `ModelInterface`,
`Speaker`, `MemoryManager`, and the optional vector store. The GUI adds the Qt
window, signal bus, timers, and Live2D/sprite renderer.

---

## Runtime map

```text
                    +-------------------+
  microphone ------>| core.recording    |----> Whisper (cache-first)
                    | VAD / endpointing |      core.whisper_cache
                    +---------+---------+
                              |
                              v
  keyboard / GUI ----> VoiceCompanion (src/she.py)
                              |
                 +------------+-------------+
                 |                          |
                 v                          v
        memory + episodes            web search / agenda
        core.memory / memory.*       core.websearch / core.agenda
                 |                          |
                 +------------+-------------+
                              |
                              v
                     ModelInterface (Cerebras / Ollama)
                              |
                    streaming sentence parser
                     (body tags / mood tags)
                              |
              +---------------+---------------+
              |                               |
              v                               v
        Speaker (Fish Audio)             GUI Bus -> Pet
        cache / ducking / retries        bubble, mood, body tags
              |                               |
              +---------------+---------------+
                              |
                              v
                    episode log + daily summary
```

The CLI follows the same path but prints text instead of emitting Qt signals.

---

## Entry points and GUI split

### `src/app.py`

Owns process startup:

1. Redirects `stdout`/`stderr` when running under `pythonw`.
2. Installs `sys.excepthook` / `threading.excepthook` crash handlers.
3. Parses CLI arguments (`--config-dir`, `--hotkey`, `--art`, `--size`).
4. Loads `config/live2d.json`, `vision.yaml`, and `text_input.yaml`.
5. Probes for the Live2D runtime and falls back to sprites if unavailable.
6. Builds the `PetCompanion` controller and the `Pet` widget.
7. Wires the Qt signal bus, tray menu, global hotkeys, timers, and wake word.
8. Provides small compatibility re-exports (`Bus`, `FaceLabel`, `Pet`, etc.) for
   callers and tests that import from `app`.

### `src/gui/`

The Qt-only building blocks live here so `app.py` is not the only home for
reusable window code:

| Module | Responsibility |
|---|---|
| `gui/art.py` | Art-directory resolution, mood sprites, placeholder rendering |
| `gui/bus.py` | Qt signal contract between controller and window |
| `gui/widgets.py` | `FaceLabel`, `TypingIndicator`, `StatusIndicator`, `TouchZoneOverlay`, `BubbleTail` |
| `gui/runtime.py` | `ProactiveTimer`, `WakeWordThread` |

`app.py` re-exports the public names (`Bus`, `FaceLabel`, `TypingIndicator`,
`StatusIndicator`, `TouchZoneOverlay`, `BubbleTail`, `ProactiveTimer`,
`WakeWordThread`) so imports remain stable.

### `src/she.py`

`VoiceCompanion` is the shared conversation controller. It owns the model loop,
speech queue, memory harvesting, summaries, commands, and shutdown behavior.
The GUI subclass `PetCompanion` overrides delivery points to emit bus signals
and to drive the Live2D viewer.

---

## Core modules

| Area | Modules | Notes |
|---|---|---|
| Configuration | `core/config.py` | Validation, hot reload, logging policy, persona + runtime state |
| LLM | `core/model.py` | Cerebras default, Ollama fallback, stop-sequence cap, streaming |
| Speech input | `core/recording.py`, `core/turn_taking.py`, `core/whisper_cache.py` | VAD, endpointing, interrupt monitor, cached Whisper load |
| Speech output | `core/audio.py` | Fish Audio synthesis, timeout guard, retry, cache, ducking, failure callback |
| Conversation memory | `core/memory.py`, `core/memory_hygiene.py`, `core/memory_notes.py` | Facts, summaries, durable filtering, self-note harvesting |
| Vector memory | `memory/vector_store.py` | ChromaDB adapter, stable IDs, startup sync |
| Prompt layer | `core/aliveness.py`, `core/verbosity.py`, `core/circadian.py`, `core/agenda.py`, `core/threads.py`, `core/interruption.py`, `core/prompt_budget.py` | Feature-owned prompt text, contextual lines, and prompt-size accounting |
| Senses | `core/websearch.py`, `vision/screen_capture.py` | Optional web lookup, on-demand in-memory screenshot |
| Emotion | `emotion/controller.py`, `emotion/mood_delivery.py`, `emotion/sound_script.py`, `emotion/style_detector.py` | Expressions, delivery, sound scripts |
| Body / animation | `behavior_state.py`, `behavior_director.py`, `live2d_face.py`, `motion3_player.py`, `lipsync.py`, `core/drag_physics.py`, `core/framing.py` | Pure state model, motion budget, Live2D integration, framing |
| Touch | `core/touch.py`, `touch_reactions.py` | Zone classification, escalation, spoken reactions |

---

## Startup sequence

1. `.env` and `config/personality.json` load through `core.config`.
2. Logging is configured once (INFO, rotating; `EVELYN_LOG_LEVEL=DEBUG` opt-in).
3. `Config` validates the persona and runtime state.
4. Whisper is initialized cache-first (`local_files_only=True`); a true cache miss
   downloads normally.
5. When vector memory is enabled, the startup sync mirrors facts and current
   summaries into `evelyn_memories` using stable `fact:*` / `summary:<date>` IDs.
6. The speaker and model clients are created.
7. The GUI builds the Pet, connects the bus, starts the idle/proactive timer, and
   optionally starts the wake-word thread.

The app exits through `VoiceCompanion.shutdown()`, which flushes the session to
the episode log and writes the daily summary when enabled.

---

## Conversation contract

A turn moves through these stages:

1. **Input** - microphone (with VAD/endpointing) or typed text; screen capture
   may be attached to the next turn when armed.
2. **Prompt assembly** - persona, feature-owned prompt blocks, selected memory
   facts/summaries/vectors, agenda/circadian/thread context, web snippets, and
   any pending vision image. `VoiceCompanion.build_messages()` is the single
   assembly point; it records per-section estimates and applies the optional
   oldest-history budget without changing the in-memory history.
3. **Generation** - `ModelInterface.stream_chat()` yields chunks.
4. **Parsing** - mood tags are separated from text; body-control mode parses
   sentence tags and narration; self-written `{{remember: ...}}` notes are
   stripped from display and harvested after the turn.
5. **Delivery** - each sentence is queued for TTS; narration is display-only;
   the GUI receives incremental text, mood, body, and status signals.
6. **Persistence** - the turn is appended to the dated episode file; after the
   configured threshold, older history is summarized.

TTS failure is non-fatal: the Speaker retries once, then calls
`on_tts_failure`; the GUI shows a rate-limited, display-only note and keeps the
reply in the bubble. The model window is never moved or resized by the note.

---

## Memory model

There are four layers, each with one source of truth:

| Layer | Location | Owner |
|---|---|---|
| Durable facts | `config/memory.json` | `MemoryManager` |
| Daily transcripts | `config/episodes/YYYY-MM-DD.json` | `MemoryManager` |
| Daily summaries | `config/episodes/summaries.json` | `MemoryManager` |
| Vector index | `memory_store/` collection `evelyn_memories` | `memory/vector_store.py` |

The JSON files and episodes are the source of truth. Startup synchronization
adds missing vectors and prunes stale ones; `/forget` clears both flat facts and
their vectors. `tools/memory_doctor.py` reports drift without writing. Archived
Marin-era transcripts live under `config/archive/` and are not loaded.

---

## Prompt ownership

`config/personality.json` is the character card and is not edited for feature
work. Feature prompt text belongs in feature-owned YAML:

- `config/aliveness.yaml` - turn shape, listening, narration, opinions, memory
  notes, question guard.
- `config/verbosity.yaml` - reply length policy.
- `config/circadian.yaml`, `agenda.yaml`, `threads.yaml`, `interruption.yaml` -
  contextual prompt lines.
- `config/websearch.yaml` - search limits, not prompt voice.
- `config/prompt_budget.yaml` - size caps and observability knobs only; it
  contains no prompt text.

Prompt-quality changes are measured before they are kept. The cadence ruler
(`tools/measure_cadence.py`) reports question rate, narration, one-word beats,
and self-notes after stripping non-spoken text. The prompt ruler
(`tools/measure_prompt.py`) reports per-turn size, section totals, trim counts,
and provider usage from `logs/evelyn.log` without reading prompt text.

---

## GUI geometry contract

- The speech bubble is on the **left** of the model, with a **2 px** gap.
- The bubble is narrower than the face and semitransparent.
- Showing or hiding the bubble, typing indicator, status pill, or voice-failure
  note must not move or resize the model window.
- `Pet._apply_layout()` owns face size, window size, bubble rectangle, and
  hit-test masks. `gui/widgets.py` only paints its assigned rectangle.
- `FaceLabel` and `TouchZoneOverlay` read live geometry from the parent `Pet`.

Tests in `tests/test_bubble_side.py`, `tests/test_headless.py`, and
`tests/test_vision.py` lock these contracts.

---

## Failure policy

| Failure | Behavior |
|---|---|
| Cerebras unavailable | Fall back to Ollama / in-character error |
| Five stop sequences configured | `cap_stop_sequences()` trims to four at INFO |
| Whisper cache miss | Download once, then use local cache |
| TTS timeout | 30 s cap, one retry, then visible display-only note |
| Prompt over budget | Oldest outgoing history is trimmed; system/persona text is never cut |
| Vector store unavailable | Degrade to JSON memory, log once |
| Live2D runtime missing | Sprite fallback; no crash |
| Missing optional config | Safe defaults; malformed config logs a warning |

---

## Testing and extension rules

- Run the full gate after every change:
  `.\.venv\Scripts\python.exe -m pytest tests/ -q`.
- Add focused tests for new pure logic and for GUI geometry contracts.
- Keep feature-owned config and prompt text out of `personality.json`.
- Archive old data; never delete user data during a cleanup or migration.
- Keep code and documentation files on CRLF line endings in this repository.
- Prefer small modules with one responsibility; `src/gui/` is the preferred home
  for new Qt-only widgets and background helpers.

For the behavior-state math, gaze rules, motion budget, and Live2D integration
details, see [BEHAVIOR_DIRECTOR.md](BEHAVIOR_DIRECTOR.md).
