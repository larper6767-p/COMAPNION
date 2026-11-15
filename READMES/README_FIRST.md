# Evelyn - Quick Start

Evelyn is a desktop AI companion with voice input, an LLM brain, text-to-speech,
persistent memory, and an animated Live2D/sprite pet. This is the short setup
guide for a friend-test build.

The full guide is [README.md](README.md). The module map is
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Install

1. Install **Python 3.11-3.14** from <https://www.python.org/downloads/> and tick
   **Add Python to PATH**.
2. Double-click **`setup.bat`**. It creates a private `.venv` and installs the
   exact tested versions.
3. Double-click **`run.bat`**.

On the first launch the app asks for two API keys and writes them to `.env`.
After that, `run.bat` is the only command you need.

> If your build does not include the Live2D model, the pet uses the sprite
> fallback. That is expected; Live2D assets are optional and license-dependent.

---

## 2. Keys

| Service | Used for | Get a key |
|---|---|---|
| **Cerebras** | The LLM brain | <https://cloud.cerebras.ai> |
| **Fish Audio** | The voice | <https://fish.audio> |

You can paste the keys when prompted, or copy `.env.example` to `.env` and fill
in the values yourself.

**Never share `.env` or your keys.**

---

## 3. What is sent where

- Your text is sent to **Cerebras** to generate a reply.
- Her reply text is sent to **Fish Audio** for speech synthesis.
- Speech-to-text (Whisper) runs **on your computer**.
- Facts, episodes, summaries, and settings are stored in this folder.
- Web search is enabled; only the search query leaves your machine.

Local data locations:

| Path | Contents |
|---|---|
| `config/memory.json` | Facts she remembers |
| `config/relationship.json` | Closeness state |
| `config/episodes/` | Dated conversation logs and summaries |
| `config/pet_state.json` | Window position/size and UI settings |
| `memory_store/` | Local vector index when enabled |
| `logs/` | Runtime logs; no conversation text by default |

To reset her memory, stop the app and move `config/memory.json`,
`config/episodes/`, and `config/relationship.json` into a safe folder. Archive old
data instead of deleting it.

Before changing memory, make a backup:

```powershell
.\.venv\Scripts\python.exe tools\backup_data.py
```

---

## 4. Everyday use

- Press **F9** (default) or left-click the pet to talk.
- Press **Ctrl+T** to type instead.
- Right-click the pet for volume, speed, reload, stats, and quit.
- The tray icon can show/hide, mute, or quit.
- Say **"hello eve"** if the wake word is enabled.

---

## 5. If something is wrong

Double-click **`diagnose.bat`**. It writes `diagnose_out.txt` and prints the
same report; the report contains no conversation content.

Common fixes:

- **Python not found** - reinstall Python with **Add Python to PATH**.
- **No reply** - check `CEREBRAS_API_KEY` in `.env`.
- **No voice** - check `FISH_API_KEY`; the reply still appears in the bubble.
- **First reply is slow** - Whisper downloads its model once, then caches it.
- **App will not start** - inspect `logs/` and send `diagnose_out.txt`.
- **Memory looks inconsistent** - run `tools\memory_doctor.py`; back up first.

---

## 6. Model license

The Live2D model is not included in every build while its license is being
verified. The app runs in sprite mode without it. Software dependencies retain
their own licenses.
