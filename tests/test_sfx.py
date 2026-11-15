"""Feature 12: real SFX audio — config, marker detection, cooldowns, no-ops.

Pure logic only: no pygame playback is exercised (the real clips are user-
provided and may not exist), no mixer is initialised.  The only covered
non-pure path is SfxPlayer.play() degrading to False without clips.

Click-through/wiring to core.audio.Speaker is asserted as a source tripwire
(test_headless stubs core.* at session level, so a live Speaker can't be
built headless).
"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Drop stubbed core package parents before importing the real module
# (same pattern as test_text_input / test_sound_script).
for _name in ("core", "core.sfx"):
    if _name in sys.modules:
        del sys.modules[_name]

from core.sfx import (                                   # noqa: E402
    SfxConfig, SfxPlayer, clip_for, detect_sfx, load_config,
    DEFAULT_CONFIG_PATH,
)


def _cfg_raw(tmp_path, text, name="sfx.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── config ────────────────────────────────────────────────────────────────────

def test_missing_file_uses_defaults():
    cfg = load_config(Path("Z:/nope/sfx.yaml"))
    assert cfg.enabled is True
    assert cfg.folder == "assets/sfx"
    assert cfg.volume == 0.5 and cfg.cooldown == 2.0 and cfg.gap == 0.4
    assert cfg.max_per_sentence == 4
    assert ("♥", ("heart",)) in cfg.triggers
    assert cfg.words, "default onomatopoeia patterns compile"


def test_shipped_config_is_enabled():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.enabled is True
    assert cfg.volume == 0.5
    assert ("♪", ("giggle",)) in cfg.triggers
    assert any(p.pattern.startswith("mwah") for p, _ in cfg.words)


def test_flags_and_clamping(tmp_path):
    p = _cfg_raw(tmp_path, "\n".join([
        "enabled: false",
        "volume: 3.0",          # > 1 -> clamped
        "cooldown: -5",         # negative -> clamped
        "gap: 1.25",
        "max_per_sentence: 2",
        "folder: custom_sfx",
    ]) + "\n")
    cfg = load_config(p)
    assert cfg.enabled is False
    assert cfg.volume == 1.0
    assert cfg.cooldown == 0.0
    assert cfg.gap == 1.25
    assert cfg.max_per_sentence == 2
    assert cfg.folder == "custom_sfx"


def test_custom_mapping_parsed(tmp_path):
    p = _cfg_raw(tmp_path, "\n".join([
        'triggers: {"♪": ["giggle", "laugh"]}',
        'words: {"hehe|tehee": [giggle]}',
    ]) + "\n")
    cfg = load_config(p)
    assert ("♪", ("giggle", "laugh")) in cfg.triggers
    hits = detect_sfx("Tehee~", cfg)
    assert ("hehe|tehee", ("giggle",)) in hits


def test_bad_mapping_patterns_are_skipped(tmp_path):
    p = _cfg_raw(tmp_path, 'triggers: {"": [x]}  \n' 'words: {"([bad": [y]}\n')
    cfg = load_config(p)                    # nothing usable -> defaults survive
    assert ("♥", ("heart",)) in cfg.triggers
    assert all(p.pattern != "([bad" for p, _ in cfg.words)


# ── detection ─────────────────────────────────────────────────────────────────

def test_detect_categories_follow_the_config_order():
    """Categories are the config keys (the markers), returned in the config's
    own order — deterministic, not text order."""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    hits = detect_sfx("nnh~ don't stop ♡ ... ahh♥", cfg)
    assert hits == [("♥", ("heart",)), ("♡", ("tender",)), ("~", ("breath",)),
                    ("\\bhn+ng+\\b|\\bnn+gh?\\b|\\bhnn+h\\b|\\bnn+h\\b",
                     ("whimper",))]


def test_detect_onomatopoeia_case_insensitive():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert ("mwah|mmwah|smooch|\\bchu\\b", ("kiss",)) in detect_sfx("MWAH!, careful", cfg)
    assert detect_sfx("tell me more", cfg) == []


def test_detect_dedupes_and_caps():
    cfg = SfxConfig(triggers=(("~", ("breath",)), ("♥", ("heart",))),
                    max_per_sentence=1)
    assert detect_sfx("~ ~ ♥ ♥ ♥", cfg) == [("~", ("breath",))]


def test_detect_disabled_is_always_empty():
    cfg = SfxConfig(enabled=False)
    assert detect_sfx("♥♡♪~ mwah", cfg) == []
    assert detect_sfx(None) == []          # default cfg path, no text


def test_detect_expanded_library_categories():
    """Feature 12d: the rest of the sound-script vocabulary maps to stems."""
    cfg = load_config(DEFAULT_CONFIG_PATH)
    cases = {
        "Mmmh": "moan",
        "mmmph": "moan",
        "Hnnng": "whimper",
        "nngh": "whimper",
        "Haaah": "sigh",
        "aahh": "sigh",
        "Hehehe": "laugh",
        "teehee": "laugh",
        "Prrrr": "purr",
        "mrrp": "purr",
        "ba-dum": "heartbeat",
        "thump thump": "heartbeat",
        "sniff": "sniff",
        "gulp": "gulp",
    }
    for text, stem in cases.items():
        stems = {s for _, group in detect_sfx(text, cfg) for s in group}
        assert stem in stems, (text, stems)


def test_expanded_words_ignore_ordinary_speech():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    for text in ("hmm, maybe later", "he he, nice try", "purr for me"):
        assert detect_sfx(text, cfg) == [], text


def test_shipped_yaml_contains_every_default_mapping():
    """config/sfx.yaml must stay a superset of the code fallbacks."""
    shipped = load_config(DEFAULT_CONFIG_PATH)
    defaults = load_config(Path("Z:/nope/sfx.yaml"))
    have = {(p.pattern, stems) for p, stems in shipped.words}
    want = {(p.pattern, stems) for p, stems in defaults.words}
    assert want <= have


# ── file resolution ───────────────────────────────────────────────────────────

def test_clip_for_matcher():
    files = ["breath_1.wav", "breath2.ogg", "suckle_1.wav", "heart.wav",
             "GIGGLE_02.OGG"]
    assert clip_for("breath", files) == "breath_1.wav"   # first match wins
    assert clip_for("BREATH", files) == "breath_1.wav"   # case-insensitive
    assert clip_for("heart", files) == "heart.wav"
    assert clip_for("giggle", files) == "GIGGLE_02.OGG"
    assert clip_for("suck", files) is None               # 'suckle' must NOT match
    assert clip_for("giggle9", files) is None
    assert clip_for("  ", files) is None


def _empty_folder_cfg(tmp_path):
    """A config whose clip folder is guaranteed empty (hermetic no-op tests:
    the shipped assets/sfx now contains her generated pack)."""
    return _cfg_raw(tmp_path, f'folder: "{(tmp_path / "empty").as_posix()}"\n')


def test_player_no_clips_is_a_silent_noop(tmp_path):
    p = SfxPlayer(config_path=_empty_folder_cfg(tmp_path))
    assert p.play([("~", ("breath",))], now=10.0) is False
    assert p._last_any is None                            # cooldowns untouched
    assert p._last_cat == {}


def test_player_disabled_config_noop(tmp_path):
    p = _cfg_raw(tmp_path, "enabled: false\n")
    player = SfxPlayer(config_path=p)
    assert player.play([("♥", ("heart",))], now=0.0) is False  # disabled gate
    assert player._last_any is None                            # never bookkept


def test_player_cooldown_gating():
    p = SfxPlayer()
    cfg = SfxConfig()
    p._last_cat, p._last_any = {"breath": 0.0}, 0.0
    assert p._allowed(cfg, "breath", 1.0) is False        # same cat too soon
    assert p._allowed(cfg, "kiss", 0.1) is False          # global gap
    assert p._allowed(cfg, "kiss", 0.5) is True           # gap passed...
    assert p._allowed(cfg, "breath", 2.5) is True         # cooldown passed
    assert p._allowed(cfg, "kiss", 2.5) is True


def test_player_stop_all_without_pygame():
    p = SfxPlayer()
    p.stop_all()                                          # must not raise
    p._channels = [object()]                              # poisoned on purpose
    p.stop_all()                                          # survives bad channel


def test_play_prefers_first_allowed_and_only_one_clip(tmp_path):
    """Two hits: the first has no clip file, the second neither -> False, no
    crash; cooldown bookkeeping only ever touches successful plays."""
    p = SfxPlayer(config_path=_empty_folder_cfg(tmp_path))
    hits = detect_sfx("mwah~", load_config(DEFAULT_CONFIG_PATH))
    assert len(hits) == 2
    assert p.play(hits, now=5.0) is False
    assert p._last_cat == {} and p._last_any is None


# ── wiring tripwires (Speaker integration) ────────────────────────────────────

def test_speaker_wiring():
    root = Path(__file__).parent.parent
    src = (root / "src" / "core" / "audio.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "from core.sfx import SfxPlayer, detect_sfx" in src
    assert "from core.sound_splice import split_text" in src
    assert "self._sfx = SfxPlayer()" in src
    # Feature 12c: the split runs on the RAW sentence (before cleaning strips
    # the symbols) and only splices tokens whose clip really exists.
    assert "parts = split_text(raw, cfg, has_clip=player.has_clip)" in src
    assert src.index("parts = split_text(raw, cfg") < src.index("parts = _clean_parts(parts)")
    # the part list travels as one sentence tuple; cue parts have no audio
    assert "self.sentences.put((self.gen, reveal, parts, mood, tag_list, t0))" in src
    assert "gen, reveal, parts, mood, tags, t0 = self.sentences.get" in src
    assert "if data is None:" in src
    assert "self._cue_beat()" in src
    assert "self._sfx.play(sfx_hits)" in src
    assert "self._sfx.stop_all()" in src
    # legacy path (splice off): clip still fires at sentence start
    assert "hits = detect_sfx(raw)" in src
    assert sfx_passes_through(src)


def sfx_passes_through(src: str) -> bool:
    synth = 'self.audio.put((gen, reveal if first else "", mood,'
    playloop = "gen, text, mood, tags, sfx_hits, data, t0 = self.audio.get"
    return synth in src and playloop in src
