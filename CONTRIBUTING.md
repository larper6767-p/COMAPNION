# Contributing to Evelyn

## Ground rules

- Evelyn is the canon character. Marin-era data is archived, never deleted.
- Do not edit persona text unless the change is measured first. Personal
  personas belong in the ignored `config/personality.local.json`; the tracked
  `config/personality.json` is the non-NSFW placeholder.
- Do not change closeness or reset relationship state.
- Keep the bubble on the left of the model with a 2 px gap. It must stay
  narrower and semitransparent, and must never move or resize the model window.
- Keep web search enabled, along with listening, verbosity, emotions, and hand
  gestures.
- Preserve CRLF in code, tests, config, and documentation.

## Setup

```powershell
setup.bat
```

The setup script installs the pinned runtime from `requirements.lock`. For a
development environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

## Tests

Run the full suite from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

For headless GUI tests:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:SDL_AUDIODRIVER = "dummy"
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

CI runs:

- **Linux headless:** the suite with `requirements-ci.txt`
- **Windows full:** the suite with `requirements.txt`

Hardware-heavy tests should carry the appropriate pytest marker
(`gui`, `audio`, `live2d`, `network`, `slow`, or `windows`).

## Privacy

Never commit:

- `.env`
- `config/personality.local.json`
- `config/memory.json`, `config/relationship.json`, `config/pet_state.json`
- `config/threads_state.json`, `config/websearch_cache.json`
- `config/episodes/`, `config/archive/`
- `logs/`, `backups/`, `memory_store/`

Use `tools/backup_data.py` before changing memory or runtime data. Archive old
data instead of deleting it.

## Before opening a pull request

1. Run the full test gate.
2. Check that no private or generated data is staged.
3. Verify the bubble geometry contract if the GUI changed.
4. Include a manual checklist for user-facing behavior changes.
