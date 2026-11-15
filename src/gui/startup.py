"""Process-level startup helpers shared by the GUI entry point.

Kept free of Qt and heavy imports so it can run before the GUI stack is loaded.
"""
from __future__ import annotations

import datetime
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Sequence

KEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("FISH_API_KEY", "Fish Audio (the voice): get a key at https://fish.audio"),
    ("CEREBRAS_API_KEY", "Cerebras (the brain): get a key at https://cloud.cerebras.ai"),
)


def redirect_missing_std_streams() -> None:
    """Give pythonw a writable stdout/stderr instead of ``None``."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def install_crash_handlers(root: Path) -> None:
    """Append uncaught exceptions to ``logs/crash.log`` before the default hook."""
    log_dir = root / "logs"

    def handle(exc_type, exc_value, exc_tb) -> None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "crash.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n{'=' * 60}\n")
                fh.write(f"{datetime.datetime.now().isoformat()}  UNCAUGHT EXCEPTION\n")
                fh.writelines(traceback.format_exception(exc_type, exc_value, exc_tb))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handle
    threading.excepthook = lambda args: handle(
        args.exc_type, args.exc_value, args.exc_traceback
    )


def load_env(root: Path, fields: Sequence[tuple[str, str]] = KEY_FIELDS) -> dict[str, str]:
    """Load ``root/.env`` and return the current key values (never logs them)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
        load_dotenv()
    except Exception:
        pass
    return {name: os.getenv(name, "").strip() for name, _message in fields}


def ensure_env_keys(
    root: Path,
    fields: Sequence[tuple[str, str]] = KEY_FIELDS,
) -> None:
    """Prompt for missing/placeholder keys on first run and write ``.env``."""
    try:
        from dotenv import set_key
    except Exception:
        return

    values = load_env(root, fields)
    missing = [
        (name, message) for name, message in fields
        if not values.get(name)
        or "your_" in values.get(name, "")
        or "here" in values.get(name, "")
    ]
    if not missing:
        return

    print("\n=== First-time setup ===")
    print("You need two free API keys for Evelyn to work. Enter them now")
    print("(they are saved to .env in this folder and never printed back).\n")
    for name, message in missing:
        print(f"  {message}")
        try:
            value = input(f"  {name} = ").strip()
        except EOFError:
            value = ""
        if value:
            set_key(str(root / ".env"), name, value)
            print("  saved.")
        else:
            print("  skipped (she'll warn you if it's still missing).")
    print()


__all__ = [
    "KEY_FIELDS", "ensure_env_keys", "install_crash_handlers", "load_env",
    "redirect_missing_std_streams",
]
