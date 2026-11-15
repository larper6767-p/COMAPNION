# Evelyn

Evelyn is a desktop AI companion with voice input, an LLM brain, text-to-speech,
persistent memory, and a Live2D/sprite pet.

## Start here

| I want to... | Read |
|---|---|
| Set up and run Evelyn | [READMES/README_FIRST.md](READMES/README_FIRST.md) |
| See features, commands, and configuration | [READMES/README.md](READMES/README.md) |
| Understand the modules and data flow | [READMES/ARCHITECTURE.md](READMES/ARCHITECTURE.md) |
| Tune body behavior and animation | [READMES/BEHAVIOR_DIRECTOR.md](READMES/BEHAVIOR_DIRECTOR.md) |
| Review the feature-by-feature build history | [READMES/CHANGELOG.md](READMES/CHANGELOG.md) |

Quick launch on Windows after `setup.bat`:

```powershell
run.bat
```

Terminal companion:

```powershell
.\.venv\Scripts\python.exe src\she.py
```

Full test gate:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
```
