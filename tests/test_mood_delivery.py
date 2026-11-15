"""Feature 8 tests: mood-driven delivery (speed / interjection / gesture).

Pure logic — no Qt, no LLM, no network, no real TTS.  The Speaker seam is
exercised on an uninitialised ``Speaker`` (``object.__new__``) with a fake
client whose ``convert`` records its kwargs and stops, so no audio is made.
"""
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emotion.mood_delivery import (                                  # noqa: E402
    CANONICAL_MOODS,
    DEFAULT_CONFIG_PATH,
    SPEED_MAX,
    SPEED_MIN,
    DeliveryConfig,
    DeliveryState,
    MoodEntry,
    decorate_speech,
    gesture_for,
    interjection_for,
    load_config,
)

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def cfg() -> DeliveryConfig:
    c = load_config(DEFAULT_CONFIG_PATH)
    assert c.enabled, "shipped config should be enabled"
    assert c.cooldown_turns >= 0
    assert 0.0 <= c.chance <= 1.0
    return c


# ── config ───────────────────────────────────────────────────────────────────

def test_shipped_config_covers_canonical_moods(cfg):
    for mood in CANONICAL_MOODS:
        assert mood in cfg.moods, mood


def test_shipped_entries_are_wellformed(cfg):
    for mood, entry in cfg.moods.items():
        assert SPEED_MIN <= entry.speed <= SPEED_MAX, mood
        assert entry.interjections, mood
        assert all(isinstance(s, str) and s.strip() for s in entry.interjections), mood
        assert entry.gesture is None            # shipped: gestures are opt-in


def test_speed_table(cfg):
    table = cfg.speed_table()
    assert set(table) == set(cfg.moods)
    assert table["excited"] == pytest.approx(1.10)
    assert table["sad"] == pytest.approx(0.90)
    assert replace(cfg, enabled=False).speed_table() == {}


def test_malformed_config_disables(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    c = load_config(p)
    assert c.enabled is False
    assert c.moods == {}
    assert c.speed_table() == {}


def test_missing_config_disables(tmp_path):
    assert load_config(tmp_path / "nope.json").enabled is False


def test_config_without_moods_disables(tmp_path):
    p = tmp_path / "d.json"
    p.write_text('{"enabled": true, "moods": {}}', encoding="utf-8")
    assert load_config(p).enabled is False


def test_bad_values_are_sanitised(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("""{
      "enabled": true,
      "cooldown_turns": -5,
      "chance": 9,
      "moods": {
        "joyful":   {"speed": 9, "interjections": "Ha!", "gesture": "nope"},
        "sad":      {"speed": "slow", "interjections": [" Mm. ", "", 3], "gesture": "nod"},
        "curious":  "not an object",
        "  RELAXED ": {"speed": 0.9, "interjections": ["Mm."]}
      }
    }""", encoding="utf-8")
    c = load_config(p)
    assert c.enabled
    assert c.cooldown_turns == 0          # negative clamped to 0
    assert c.chance == 1.0                # 9 clamped to 1.0
    assert c.moods["joyful"].speed == SPEED_MAX     # 9 clamped
    assert c.moods["joyful"].interjections == ()    # a bare string is not a list
    assert c.moods["joyful"].gesture is None        # unknown gesture dropped
    assert c.moods["sad"].speed == 1.0              # unparseable -> neutral
    assert c.moods["sad"].interjections == ("Mm.",)  # stripped, blanks/non-str gone
    assert c.moods["sad"].gesture == "nod"          # valid gesture kept
    assert "curious" not in c.moods                 # non-object entry skipped
    assert "relaxed" in c.moods                     # key normalised
    assert c.moods["relaxed"].speed == pytest.approx(0.9)


def test_wink_gesture_is_not_offered(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("""{"enabled": true, "moods": {
        "joyful": {"gesture": "wink"}}}""", encoding="utf-8")
    assert load_config(p).moods["joyful"].gesture is None


def test_extra_mood_keys_are_kept(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("""{"enabled": true, "moods": {
        "smug": {"speed": 1.2, "interjections": ["Heh."]}}}""", encoding="utf-8")
    c = load_config(p)
    assert c.moods["smug"].speed == pytest.approx(1.2)


# ── state ────────────────────────────────────────────────────────────────────

def test_state_begin_reply_resets_flags():
    st = DeliveryState()
    assert st.turns_since_interjection >= 10 ** 8   # eligible on the first reply
    st.frames = 5
    st.interjected = True
    st.gestured = True
    before = st.turns_since_interjection
    st.begin_reply()
    assert st.frames == 0 and not st.interjected and not st.gestured
    assert st.turns_since_interjection == before + 1


# ── interjections ────────────────────────────────────────────────────────────

def test_interjection_picks_and_marks(cfg):
    st = DeliveryState()
    pick = interjection_for("joyful", cfg, st)
    assert pick in cfg.moods["joyful"].interjections
    assert st.interjected
    assert st.turns_since_interjection == 0
    assert interjection_for("joyful", cfg, st) == ""      # once per reply


def test_interjection_cooldown(cfg):
    st = DeliveryState()
    assert interjection_for("joyful", cfg, st)            # fires (first reply)
    for _ in range(cfg.cooldown_turns - 1):
        st.begin_reply()
        assert interjection_for("joyful", cfg, st) == ""
    st.begin_reply()
    assert interjection_for("joyful", cfg, st) != ""      # cooldown elapsed


def test_interjection_chance_zero_never_fires(cfg):
    st = DeliveryState()
    assert interjection_for("joyful", replace(cfg, chance=0.0), st) == ""
    assert not st.interjected


def test_interjection_chance_one_always_fires(cfg):
    st = DeliveryState()
    assert interjection_for("joyful", replace(cfg, chance=1.0), st) != ""


def test_interjection_uses_supplied_rng(cfg):
    class Rng:
        def __init__(self, roll):
            self.roll = roll
        def random(self):
            return self.roll
        def choice(self, seq):
            return seq[0]

    st = DeliveryState()
    half = replace(cfg, chance=0.5)
    assert interjection_for("joyful", half, st, rng=Rng(0.49)) != ""  # 0.49 < 0.5
    st.begin_reply()
    assert interjection_for("joyful", half, st, rng=Rng(0.5)) == ""   # boundary skips


def test_interjection_disabled_or_unknown_mood(cfg):
    st = DeliveryState()
    assert interjection_for("joyful", replace(cfg, enabled=False), st) == ""
    assert interjection_for("neutral", cfg, st) == ""
    assert interjection_for(None, cfg, st) == ""
    assert not st.interjected


def test_interjection_custom_pool():
    c = DeliveryConfig(enabled=True, cooldown_turns=0, chance=1.0,
                       moods={"joyful": MoodEntry(interjections=("Only this.",))})
    st = DeliveryState()
    assert interjection_for("joyful", c, st) == "Only this."
    st.begin_reply()
    assert interjection_for("joyful", c, st) == "Only this."


# ── gesture fallback ─────────────────────────────────────────────────────────

def test_gesture_is_opt_in_in_shipped_config(cfg):
    for mood in CANONICAL_MOODS:
        st = DeliveryState()
        assert gesture_for(mood, cfg, st) is None


def test_gesture_fires_once_per_reply():
    c = DeliveryConfig(enabled=True, moods={
        "joyful": MoodEntry(gesture="nod")})
    st = DeliveryState()
    assert gesture_for("joyful", c, st) == "nod"
    assert gesture_for("joyful", c, st) is None           # only one per reply
    st.begin_reply()
    assert gesture_for("joyful", c, st) == "nod"


def test_gesture_disabled_or_unknown_mood():
    c = DeliveryConfig(enabled=True, moods={
        "joyful": MoodEntry(gesture="nod")})
    st = DeliveryState()
    assert gesture_for("joyful", replace(c, enabled=False), st) is None
    assert gesture_for("sad", c, st) is None


# ── decorate_speech (the composition the app calls per spoken frame) ─────────

def _joyful_cfg(**kw) -> DeliveryConfig:
    defaults = dict(enabled=True, cooldown_turns=0, chance=1.0, moods={
        "joyful": MoodEntry(speed=1.05, interjections=("Ha!", "Heh."),
                            gesture="nod")})
    defaults.update(kw)
    return DeliveryConfig(**defaults)


def test_decorate_interjects_first_frame_only():
    c = _joyful_cfg()
    st = DeliveryState()
    text1, tags1 = decorate_speech("I know, right.", "joyful", [], c, st, {})
    assert text1 != "I know, right."
    assert text1.endswith("I know, right.")
    assert text1.split(" ")[0] in ("Ha!", "Heh.") or text1.startswith(("Ha!", "Heh."))
    assert tags1 and tags1[0].kind == "gesture"           # nod on frame 1 too
    text2, tags2 = decorate_speech("And then we went.", "joyful", [], c, st, {})
    assert text2 == "And then we went."                    # interjection: once
    assert tags2 == []                                     # gesture: once
    assert st.frames == 2


def test_decorate_appends_gesture_tag():
    c = _joyful_cfg()
    st = DeliveryState()
    _, tags = decorate_speech("Hey.", "joyful", [], c, st, {}, cap_remaining=1)
    assert len(tags) == 1
    assert tags[0].kind == "gesture" and tags[0].name == "nod"
    assert tags[0].raw == "[gesture:nod]"


def test_decorate_skips_gesture_when_reply_has_one():
    c = _joyful_cfg()
    st = DeliveryState()
    _, tags = decorate_speech("Hey.", "joyful", [], c, st,
                              {"gesture": 1}, cap_remaining=3)
    assert tags == []
    assert not st.gestured                                 # still available later
    st.begin_reply()
    _, tags = decorate_speech("Hey.", "joyful", [], c, st,
                              {"wink": 1}, cap_remaining=3)
    assert tags == []


def test_decorate_skips_gesture_when_tag_budget_exhausted():
    c = _joyful_cfg()
    st = DeliveryState()
    _, tags = decorate_speech("Hey.", "joyful", [], c, st, {}, cap_remaining=0)
    assert tags == []


def test_decorate_disabled_or_no_mood_is_identity():
    st = DeliveryState()
    disabled = _joyful_cfg(enabled=False)
    text, tags = decorate_speech("Hey.", "joyful", [], disabled, st, {})
    assert text == "Hey." and tags == []

    enabled = _joyful_cfg()
    text, tags = decorate_speech("Hey.", "neutral", [], enabled, st, {})
    assert text == "Hey." and tags == []                   # mood not configured
    assert st.frames == 0                                  # frame never counted


def test_decorate_never_touches_the_original_text_object():
    """The app passes the frame text after history captured it — we must not
    mutate shared state, only return a new string."""
    c = _joyful_cfg()
    st = DeliveryState()
    original = "I told you so."
    out, _ = decorate_speech(original, "joyful", [], c, st, {})
    assert original == "I told you so."
    assert out is not original


# ── Speaker seam (prosody) ───────────────────────────────────────────────────

class _Stop(Exception):
    """Raised by the fake client right after recording its kwargs."""


def _real_speaker_class():
    """Return the REAL ``Speaker``, dropping any stubbed parents first.

    ``test_headless`` / ``test_state_prompt`` replace ``core.audio`` (and the
    bare ``core``) with ``ModuleType`` fakes for the session; a fake has no
    ``__path__`` and no ``_synth``.  Dropping only the fakes keeps the real
    package (and the already-cached real ``fishaudio.utils``) intact.
    """
    for name in ("core", "core.audio", "fishaudio"):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if name == "fishaudio" and "fishaudio.utils" in sys.modules:
            continue                       # cached real submodule still usable
        if not hasattr(mod, "__path__"):
            sys.modules.pop(name, None)
    from core.audio import Speaker
    if not hasattr(Speaker, "_synth"):     # pragma: no cover - env dependent
        pytest.skip("real core.audio unavailable in this session")
    return Speaker


def _fake_speaker(base=1.0, provider=None):
    Speaker = _real_speaker_class()

    class _FakeCache:
        def __init__(self):
            self.get_args = None
        def get(self, *a):
            self.get_args = a
            return None
        def put(self, *a):
            pass

    class _TTS:
        def __init__(self):
            self.kwargs = None
        def convert(self, **kw):
            self.kwargs = kw
            raise _Stop()

    class _Client:
        pass

    sp = object.__new__(Speaker)               # no pygame mixer, no threads
    sp.speed = base
    sp.voice_id = "vid"
    sp.model = "model"
    sp.emotion_template = None
    sp.cache = _FakeCache()
    sp._mood_speeds = provider
    client = _Client()
    client.tts = _TTS()
    sp.client = client
    return sp


def _synth_kwargs(sp, mood="excited", text="hello"):
    with pytest.raises(_Stop):
        sp._synth(text, mood)
    return sp.client.tts.kwargs


def test_speaker_applies_mood_speed():
    sp = _fake_speaker(base=1.0, provider=lambda: {"excited": 1.10})
    kw = _synth_kwargs(sp, "excited")
    assert kw["speed"] == pytest.approx(1.10)
    assert sp.cache.get_args[4] == pytest.approx(1.10)      # cache key uses it too


def test_speaker_without_provider_is_unchanged():
    sp = _fake_speaker(base=1.0, provider=None)
    kw = _synth_kwargs(sp, "excited")
    assert "speed" not in kw                                # 1.0x is not sent
    assert sp.cache.get_args[4] == pytest.approx(1.0)


def test_speaker_empty_table_is_unchanged():
    sp = _fake_speaker(base=1.0, provider=lambda: {})
    kw = _synth_kwargs(sp, "excited")
    assert "speed" not in kw


def test_speaker_scales_the_base_speed():
    sp = _fake_speaker(base=1.2, provider=lambda: {"sad": 0.90})
    kw = _synth_kwargs(sp, "sad")
    assert kw["speed"] == pytest.approx(1.08)


def test_speaker_effective_speed_is_clamped():
    sp = _fake_speaker(base=1.0, provider=lambda: {"excited": 9.0})
    assert _synth_kwargs(sp, "excited")["speed"] == SPEED_MAX
    sp = _fake_speaker(base=1.0, provider=lambda: {"excited": 0.1})
    assert _synth_kwargs(sp, "excited")["speed"] == pytest.approx(SPEED_MIN)


def test_speaker_unknown_or_missing_mood_uses_base():
    sp = _fake_speaker(base=1.0, provider=lambda: {"sad": 0.90})
    assert "speed" not in _synth_kwargs(sp, "excited")
    assert "speed" not in _synth_kwargs(sp, None)


def test_speaker_broken_provider_falls_back_to_base():
    def boom():
        raise RuntimeError("config exploded")
    sp = _fake_speaker(base=1.0, provider=boom)
    assert "speed" not in _synth_kwargs(sp, "excited")


def test_speaker_set_mood_speeds_none_clears():
    sp = _fake_speaker(base=1.0, provider=lambda: {"excited": 1.5})
    assert _synth_kwargs(sp, "excited")["speed"] == pytest.approx(1.5)
    sp.set_mood_speeds(None)
    assert "speed" not in _synth_kwargs(sp, "excited")


# ── wiring (cheap tripwires) ─────────────────────────────────────────────────

def test_wired_into_speaker_stream_and_speaker_setup():
    def src(rel):
        return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")

    assert "set_mood_speeds(" in src("src/core/audio.py")
    assert "_effective_speed" in src("src/core/audio.py")
    assert "set_mood_speeds(" in src("src/she.py")
    assert "decorate_speech(" in src("src/app.py")
    assert "begin_reply()" in src("src/app.py")
