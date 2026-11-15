"""Feature 12c tests: positional SFX splicing (pure, no audio hardware).

A sentence is split into say/cue parts at its sound-script triggers, so
the mapped word is never spoken — the cue plays exactly where it was.
Tokens without a real clip stay in the speech text (nothing silently
vanishes).  Config comes from temp yml files here, so the tests do not
depend on the user's live config/sfx.yaml choices.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

for _m in ("core", "core.sfx", "core.sound_splice"):
    sys.modules.pop(_m, None)

from core.sfx import load_config                    # noqa: E402
from core.sound_splice import split_text            # noqa: E402


def _cfg(tmp_path, extra=""):
    p = tmp_path / "sfx.yaml"
    p.write_text("enabled: true\n" + extra, encoding="utf-8")
    return load_config(p)


def _cues(parts):
    return [payload for kind, payload in parts if kind == "cue"]


def _says(parts):
    return [payload for kind, payload in parts if kind == "say"]


def test_plain_text_is_one_say_part(tmp_path):
    parts = split_text("tell me more", _cfg(tmp_path))
    assert parts == [("say", "tell me more")]


def test_word_is_spliced_at_its_position(tmp_path):
    parts = split_text("I missed you... mwah. Get back here.", _cfg(tmp_path))
    assert [k for k, _ in parts] == ["say", "cue", "say"]
    assert _says(parts) == ["I missed you... ", ". Get back here."]
    assert _cues(parts) == [
        (("mwah|mmwah|smooch|\\bchu\\b", ("kiss",)),)
    ]


def test_symbol_is_spliced(tmp_path):
    parts = split_text("That's better \u2665", _cfg(tmp_path))
    assert parts == [
        ("say", "That's better "),
        ("cue", (("\u2665", ("heart",)),)),
    ]


def test_multiple_triggers_follow_text_order_and_cap(tmp_path):
    # 4 is the default max_per_sentence: the 5th trigger is left spoken.
    parts = split_text("\u2665 \u2661 \u266a ~ \u2665 mwah", _cfg(tmp_path))
    assert [h[0][0] for h in _cues(parts)] == ["\u2665", "\u2661", "\u266a", "~"]
    # the 5th symbol and the word survive in the tail speech text
    assert "mwah" in "".join(_says(parts))


def test_missing_clip_keeps_the_word_spoken(tmp_path):
    cfg = _cfg(tmp_path)
    parts = split_text("I missed you... mwah. Get back here.", cfg,
                       has_clip=lambda stems: stems != ("kiss",))
    assert parts == [("say", "I missed you... mwah. Get back here.")]


def test_disabled_config_leaves_text_untouched(tmp_path):
    p = tmp_path / "off.yaml"
    p.write_text("enabled: false\n", encoding="utf-8")
    cfg = load_config(p)
    assert split_text("mwah \u2665", cfg) == [("say", "mwah \u2665")]


def test_empty_and_none():
    cfg = load_config(Path("Z:/nope/sfx.yaml"))
    assert split_text("", cfg) == [("say", "")]
    assert split_text(None, cfg) == [("say", "")]


def test_adjacent_triggers_have_no_empty_say(tmp_path):
    parts = split_text("mwah~", _cfg(tmp_path))
    assert [k for k, _ in parts] == ["cue", "cue"]
    assert [h[0][0] for h in _cues(parts)] == [
        "mwah|mmwah|smooch|\\bchu\\b", "~"]


def test_expanded_vocabulary_splices_at_position(tmp_path):
    """Feature 12d: every new sound word is replaced by its cue."""
    cfg = _cfg(tmp_path)
    cases = (("mmmph", "moan"), ("hnnng", "whimper"), ("haaah", "sigh"),
             ("hehehe", "laugh"), ("prrr", "purr"), ("ba-dum", "heartbeat"),
             ("sniff", "sniff"), ("gulp", "gulp"))
    for text, stem in cases:
        parts = split_text(text, cfg)
        got = [s for payload in _cues(parts) for _, stems in payload
               for s in stems]
        assert got == [stem], (text, got)


def test_splice_config_defaults_and_clamping(tmp_path):
    default = load_config(Path("Z:/nope/sfx.yaml"))
    assert default.splice is True
    assert default.cue_beat_s == 0.25
    cfg = load_config(tmp_path / "x.yaml")       # missing -> defaults
    assert cfg.splice is True
    p = tmp_path / "custom.yaml"
    p.write_text("splice: false\ncue_beat_s: 9\n", encoding="utf-8")
    cfg2 = load_config(p)
    assert cfg2.splice is False
    assert cfg2.cue_beat_s == 1.0                # clamped to the ceiling
