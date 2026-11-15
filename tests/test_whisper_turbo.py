"""Feature 23: her ears — Whisper large-v3-turbo (auto GPU pick) + pin keys.

resolve_whisper_settings() now picks large-v3-turbo on CUDA: the same
encoder as large-v3 (English accuracy within noise of it) with 4 decoder
layers instead of 32 — several times faster transcription at about half
the VRAM, which matters next to Live2D on a 4 GB card.  The no-GPU
fallback (small / cpu / int8) is unchanged, and personality.json may pin
whisper_model / whisper_device / whisper_compute_type explicitly.
"""
import importlib
import json
import sys
from pathlib import Path

for _m in ("core", "core.config"):
    sys.modules.pop(_m, None)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
core_config = importlib.import_module("core.config")
Config = core_config.Config


def _fresh_cfg(monkeypatch, cuda: bool) -> "Config":
    """A Config whose CUDA probe answers deterministically."""
    monkeypatch.setattr(core_config, "_cuda_available", lambda: cuda)
    return Config()


# ── the auto picks ────────────────────────────────────────────────────────────

def test_auto_gpu_resolves_to_turbo(monkeypatch):
    obj = _fresh_cfg(monkeypatch, cuda=True)
    model, device, compute = obj.resolve_whisper_settings()
    assert (model, device, compute) == ("large-v3-turbo", "cuda", "float16")


def test_auto_no_gpu_fallback_unchanged(monkeypatch):
    obj = _fresh_cfg(monkeypatch, cuda=False)
    model, device, compute = obj.resolve_whisper_settings()
    assert (model, device, compute) == ("small", "cpu", "int8")


def test_resolve_is_idempotent(monkeypatch):
    obj = _fresh_cfg(monkeypatch, cuda=True)
    first = obj.resolve_whisper_settings()
    assert obj.resolve_whisper_settings() == first


# ── pinning via personality.json (Feature 23 keys) ───────────────────────────

def _tmp_personality(tmp_path, **extra):
    real = json.loads(
        (Path(__file__).parent.parent / "config" / "personality.json")
        .read_text(encoding="utf-8"))
    real.update(extra)
    tmp = tmp_path / "personality.json"
    tmp.write_text(json.dumps(real, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_personality_pin_overrides_whisper(monkeypatch, tmp_path):
    obj = _fresh_cfg(monkeypatch, cuda=True)
    obj.personality_file = _tmp_personality(
        tmp_path, whisper_model="large-v3", whisper_device="cuda",
        whisper_compute_type="float16")
    obj._load_personality()
    assert obj.resolve_whisper_settings() == ("large-v3", "cuda", "float16")


def test_pinned_model_kept_when_device_auto(monkeypatch, tmp_path):
    obj = _fresh_cfg(monkeypatch, cuda=True)
    obj.personality_file = _tmp_personality(
        tmp_path, whisper_model="large-v3")
    obj._load_personality()
    model, device, _compute = obj.resolve_whisper_settings()
    assert model == "large-v3"          # the pin is respected
    assert device == "cuda"             # the device still auto-resolves


def test_pin_reappearing_on_reload_re_resolves(monkeypatch, tmp_path):
    obj = _fresh_cfg(monkeypatch, cuda=True)
    obj.resolve_whisper_settings()
    assert obj.whisper_model == "large-v3-turbo"
    obj.personality_file = _tmp_personality(
        tmp_path, whisper_model="large-v3")
    obj._load_personality()             # a reload resets the resolved flag
    assert obj.resolve_whisper_settings()[0] == "large-v3"
