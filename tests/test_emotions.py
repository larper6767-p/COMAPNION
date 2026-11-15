"""Feature 3 tests: config-driven mood -> expression mapping.

Pure-logic tests for ``emotion.controller``.  No Qt, no Live2D model, no
hardware — the renderer's SetExpression path is exercised by the existing app.
"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emotion.controller import (                                    # noqa: E402
    EmotionsConfig, load_config, next_expression_action, DEFAULT_CONFIG_PATH,
    DEFAULT_EXPRESSIONS, DEFAULT_MOOD_EXPRESSIONS,
)

MISSING = Path("does/not/exist/emotions.yaml")


# ── defaults (== the previously hard-coded behaviour) ─────────────────────────

def test_missing_file_gives_builtin_defaults():
    cfg = load_config(MISSING)
    assert cfg.enabled is True
    assert cfg.mood_expressions == DEFAULT_MOOD_EXPRESSIONS
    assert cfg.expressions == DEFAULT_EXPRESSIONS


def test_default_expression_for_known_moods():
    cfg = load_config(MISSING)
    assert cfg.expression_for("joyful") == "blush"
    assert cfg.expression_for("excited") == "star_eyes"
    assert cfg.expression_for("embarrassed") == "blush"
    assert cfg.expression_for("sad") == "tears"
    assert cfg.expression_for("angry") == "angry"
    assert cfg.expression_for("love") == "heart_eyes"
    assert cfg.expression_for("aroused") == "blush"
    for mood in ("idle", "surprised", "curious", "relaxed"):
        assert cfg.expression_for(mood) is None


def test_unknown_mood_returns_none():
    assert load_config(MISSING).expression_for("nonexistent") is None


def test_key_for_file():
    cfg = load_config(MISSING)
    assert cfg.key_for_file("脸红.exp3.json") == "blush"
    assert cfg.key_for_file("nope.exp3.json") is None


# ── the enable toggle ─────────────────────────────────────────────────────────

def test_disabled_returns_none_but_keeps_maps():
    cfg = EmotionsConfig(enabled=False)
    assert cfg.expression_for("joyful") is None
    assert cfg.expression_for("love") is None
    # the maps are still intact, just not played
    assert cfg.mood_expressions["joyful"] == "blush"


# ── config overrides (merged over defaults) ───────────────────────────────────

def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "emotions.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_yaml_overrides_single_mood_and_keeps_others(tmp_path):
    cfg = load_config(_write(tmp_path, "mood_expressions:\n  joyful: heart_eyes\n"))
    assert cfg.expression_for("joyful") == "heart_eyes"     # overridden
    assert cfg.expression_for("sad") == "tears"             # default kept
    assert cfg.expression_for("excited") == "star_eyes"     # default kept


def test_yaml_null_disables_a_single_mood(tmp_path):
    cfg = load_config(_write(tmp_path, "mood_expressions:\n  joyful: null\n"))
    assert cfg.expression_for("joyful") is None
    assert cfg.expression_for("sad") == "tears"


def test_yaml_can_add_a_new_mood(tmp_path):
    cfg = load_config(_write(tmp_path, "mood_expressions:\n  smug: eyeroll\n"))
    assert cfg.expression_for("smug") == "eyeroll"


def test_yaml_enabled_false(tmp_path):
    cfg = load_config(_write(tmp_path, "enabled: false\n"))
    assert cfg.enabled is False
    assert cfg.expression_for("joyful") is None


def test_yaml_expression_registration_merges(tmp_path):
    cfg = load_config(_write(
        tmp_path, 'expressions:\n  "custom.exp3.json": cool\n'))
    assert cfg.key_for_file("脸红.exp3.json") == "blush"        # default kept
    assert cfg.key_for_file("custom.exp3.json") == "cool"       # added


def test_yaml_expression_null_removes(tmp_path):
    cfg = load_config(_write(
        tmp_path, 'expressions:\n  "泪.exp3.json": null\n'))
    assert cfg.key_for_file("泪.exp3.json") is None
    assert cfg.key_for_file("脸红.exp3.json") == "blush"


# ── robustness ────────────────────────────────────────────────────────────────

def test_malformed_yaml_falls_back(tmp_path):
    cfg = load_config(_write(tmp_path, "enabled: [unclosed\n"))
    assert cfg.enabled is True
    assert cfg.mood_expressions == DEFAULT_MOOD_EXPRESSIONS


def test_empty_yaml_falls_back(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.enabled is True
    assert cfg.mood_expressions == DEFAULT_MOOD_EXPRESSIONS


# ── renderer action planning (the SetExpression decision) ─────────────────────

def test_action_sets_expression_for_mapped_mood():
    cfg = load_config(MISSING)
    assert next_expression_action(cfg, "joyful", active=None) == ("set", "blush")


def test_action_is_noop_when_already_active():
    cfg = load_config(MISSING)
    assert next_expression_action(cfg, "joyful", active="blush") is None


def test_action_resets_when_mood_unmapped():
    cfg = load_config(MISSING)
    # switching from an expression to a mood with none -> reset
    assert next_expression_action(cfg, "idle", active="blush") == ("reset", None)


def test_action_noop_for_idle_when_nothing_active():
    cfg = load_config(MISSING)
    assert next_expression_action(cfg, "idle", active=None) is None


def test_action_resets_when_disabled():
    cfg = EmotionsConfig(enabled=False)
    assert next_expression_action(cfg, "joyful", active="blush") == ("reset", None)


# ── shipped config validation ─────────────────────────────────────────────────

def test_shipped_config_is_valid():
    """The live config/emotions.yaml must parse and stay internally valid.

    Re-tuning the mapping is allowed (it is user-owned), so this no longer
    pins the built-in defaults — but a mood pointing at an UNREGISTERED
    expression key is always a bug (the renderer would silently do nothing).
    """
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert isinstance(cfg.enabled, bool)
    assert cfg.expressions, "expression registry must not be empty"
    # every mood maps to None or to a registered expression key
    for mood, key in cfg.mood_expressions.items():
        assert key is None or key in cfg.expressions.values(), mood
    # every mood the app can emit has an entry (missing = silently ignored)
    for mood in DEFAULT_MOOD_EXPRESSIONS:
        assert mood in cfg.mood_expressions
