"""Model A/B harness tests: the .env CEREBRAS_MODEL override.

Feature 26a (voice upgrade, step 1): `core/config.Config` now reads the
LLM name from the environment (`CEREBRAS_MODEL`) with the shipped
default kept as fallback, so swapping brains is a one-line .env edit
instead of a code change.  Also locks the conversation-temperature fix:
persona 1.5 -> 1.0 (1.5 was cooking gpt-oss-120b into wobble).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

for _m in ("core", "core.config"):
    sys.modules.pop(_m, None)

from core import config as cfgmod                    # noqa: E402

_ROOT = Path(__file__).parent.parent


def test_env_overrides_the_brain(monkeypatch):
    monkeypatch.setenv("CEREBRAS_MODEL", "llama-3.3-70b")
    c = cfgmod.Config()
    assert c.cerebras_model == "llama-3.3-70b"
    assert c.use_cerebras is True                    # untouched by the swap


def test_unset_env_keeps_the_shipped_default(monkeypatch):
    monkeypatch.delenv("CEREBRAS_MODEL", raising=False)
    c = cfgmod.Config()
    assert c.cerebras_model == "gpt-oss-120b"


def test_blank_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CEREBRAS_MODEL", "   ")
    c = cfgmod.Config()
    assert c.cerebras_model == "gpt-oss-120b"


def test_persona_temperature_tuned_for_coherence():
    persona = json.loads(
        (_ROOT / "config" / "personality.json").read_text(encoding="utf-8")
    )
    t = persona["temperature"]
    assert isinstance(t, (int, float))
    assert 0.8 <= t <= 1.2, f"conversation temp drifted: {t}"
