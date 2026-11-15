"""Startup/log hygiene tests.

Covers the INFO-by-default log policy, bounded rotation, the Cerebras stop
cap (now INFO, not a warning every launch), and the local-cache-first Whisper
loader.
"""
import logging
import sys
from pathlib import Path

import pytest

for _m in ("core", "core.config", "core.model", "core.whisper_cache"):
    sys.modules.pop(_m, None)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.config import setup_logging                       # noqa: E402
import core.model as model_module                           # noqa: E402
from core.model import (                                    # noqa: E402
    CEREBRAS_STOP_LIMIT, ModelInterface, cap_stop_sequences,
)
from core.whisper_cache import load_cached                  # noqa: E402

ROOT = Path(__file__).parent.parent


@pytest.fixture
def clean_logging(monkeypatch):
    """Give each test a pristine root logger and restore it afterwards."""
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    noisy = ("httpcore", "httpcore2", "httpx", "urllib3", "huggingface_hub",
             "chromadb", "faster_whisper", "comtypes", "PIL", "matplotlib")
    old_noisy = {name: logging.getLogger(name).level for name in noisy}
    for name in ("EVELYN_LOG_LEVEL", "EVELYN_LOG_MAX_MB", "EVELYN_LOG_BACKUPS"):
        monkeypatch.delenv(name, raising=False)
    root.handlers.clear()
    yield
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:
            pass
    root.handlers[:] = old_handlers
    root.setLevel(old_level)
    for name, level in old_noisy.items():
        logging.getLogger(name).setLevel(level)


def _file_handler():
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_evelyn_file", False):
            return handler
    return None


def _console_handler():
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_evelyn_console", False):
            return handler
    return None


def test_logging_defaults_to_info_with_bounded_rotation(tmp_path, clean_logging):
    setup_logging(tmp_path)
    fh = _file_handler()
    assert fh is not None
    assert fh.level == logging.INFO
    assert fh.maxBytes == 5 * 1024 * 1024
    assert fh.backupCount == 3
    assert _console_handler().level == logging.INFO
    assert logging.getLogger("httpcore2").level == logging.WARNING


def test_debug_escape_hatch_and_overrides(tmp_path, clean_logging, monkeypatch):
    monkeypatch.setenv("EVELYN_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("EVELYN_LOG_MAX_MB", "9")
    monkeypatch.setenv("EVELYN_LOG_BACKUPS", "2")
    setup_logging(tmp_path)
    fh = _file_handler()
    assert fh.level == logging.DEBUG
    assert fh.maxBytes == 9 * 1024 * 1024
    assert fh.backupCount == 2
    assert _console_handler().level == logging.INFO


def test_invalid_levels_and_bounds_fall_back(tmp_path, clean_logging, monkeypatch):
    monkeypatch.setenv("EVELYN_LOG_LEVEL", "LOUD")
    monkeypatch.setenv("EVELYN_LOG_MAX_MB", "0")
    monkeypatch.setenv("EVELYN_LOG_BACKUPS", "999")
    setup_logging(tmp_path)
    fh = _file_handler()
    assert fh.level == logging.INFO
    assert fh.maxBytes == 1 * 1024 * 1024
    assert fh.backupCount == 20


def test_info_reaches_file_but_debug_does_not(tmp_path, clean_logging):
    setup_logging(tmp_path)
    logging.getLogger("test.startup").info("visible-line")
    logging.getLogger("test.startup").debug("hidden-line")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = (tmp_path / "evelyn.log").read_text(encoding="utf-8")
    assert "visible-line" in text
    assert "hidden-line" not in text


def test_setup_logging_is_idempotent(tmp_path, clean_logging):
    setup_logging(tmp_path)
    count = len(logging.getLogger().handlers)
    setup_logging(tmp_path)
    assert len(logging.getLogger().handlers) == count


# ── Cerebras stop sequences ───────────────────────────────────────────────────

def test_stop_cap_keeps_order_and_drops_extras():
    stop = ["User:", "Assistant:", "assistant:", "Evelyn:", "Eve:"]
    assert cap_stop_sequences(stop) == stop[:CEREBRAS_STOP_LIMIT]
    assert CEREBRAS_STOP_LIMIT == 4


def test_stop_cap_dedupes_and_skips_bad_entries():
    assert cap_stop_sequences(
        ["A", "A", "", None, "B", 3, "C", "D", "E"]) == ["A", "B", "C", "D"]
    assert cap_stop_sequences([], limit=4) == []
    assert cap_stop_sequences(["A", "B", "C"], limit=2) == ["A", "B"]


def test_model_trims_stop_at_info_not_warning(monkeypatch, caplog):
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    # Patch the exact module object that owns ModelInterface: later test files
    # may replace sys.modules["core.model"] during collection.
    monkeypatch.setattr(model_module, "Cerebras", lambda **kwargs: object())
    caplog.set_level(logging.INFO)
    model = ModelInterface(
        "test-model",
        use_cerebras=True,
        stop=["User:", "Assistant:", "assistant:", "Evelyn:", "Eve:"],
    )
    assert model.stop == ["User:", "Assistant:", "assistant:", "Evelyn:"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("stop sequences limited to 4" in m for m in messages)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ── Whisper cache-first loading ──────────────────────────────────────────────

def test_whisper_tries_cache_before_download(monkeypatch):
    calls = []

    def factory(model, **kwargs):
        calls.append((model, kwargs))
        if kwargs.get("local_files_only"):
            raise FileNotFoundError("not cached")
        return "MODEL"

    result, used_cache = load_cached(
        factory, "small.en", device="cpu", compute_type="int8")
    assert result == "MODEL"
    assert used_cache is False
    assert calls[0][1]["local_files_only"] is True
    assert calls[1][1]["local_files_only"] is False


def test_whisper_cached_path_makes_one_local_call():
    calls = []

    def factory(model, **kwargs):
        calls.append(kwargs)
        return "CACHED"

    result, used_cache = load_cached(factory, "small.en")
    assert result == "CACHED"
    assert used_cache is True
    assert len(calls) == 1
    assert calls[0]["local_files_only"] is True


def test_whisper_download_failure_propagates():
    def factory(model, **kwargs):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        load_cached(factory, "small.en")


def test_she_uses_the_cache_first_loader():
    src = (ROOT / "src" / "she.py").read_text(encoding="utf-8")
    joined = "".join(src.split())
    assert "load_whisper_cached(WhisperModel" in joined
    assert "local_files_only=True" in (
        ROOT / "src" / "core" / "whisper_cache.py").read_text(encoding="utf-8")
