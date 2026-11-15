"""Feature 24 tests: long mode — she unpacks on /long or stretch phrases.

core/verbosity.py is pure policy (toggles, phrase matching, the override
paragraph); the wiring is asserted as tripwires over she.py / app.py, and
the sticky toggle is driven behaviourally through the real method.
"""
import importlib
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

for _m in ("core", "core.verbosity"):
    sys.modules.pop(_m, None)

from core import verbosity as vb                      # noqa: E402

_APP = Path(__file__).parent.parent / "src" / "app.py"
_SHE = Path(__file__).parent.parent / "src" / "she.py"


# ── the slash toggles ─────────────────────────────────────────────────────────

def test_parse_slash_exact_matches():
    assert vb.parse_slash("/long") == "on"
    assert vb.parse_slash("/SHORT ") == "off"
    assert vb.parse_slash("  /long ") == "on"
    assert vb.parse_slash("") is None


def test_parse_slash_ignores_chat_text():
    assert vb.parse_slash("long shift today") is None
    assert vb.parse_slash("/longx") is None
    assert vb.parse_slash("why don't you go long on this") is None


def test_parse_slash_custom_commands():
    cfg = {"long_command": "/yap", "short_command": "/off"}
    assert vb.parse_slash("/yap", cfg) == "on"
    assert vb.parse_slash("/off", cfg) == "off"
    assert vb.parse_slash("/long", cfg) is None


# ── one-turn phrase triggers ──────────────────────────────────────────────────

def test_phrase_hit_matches():
    assert vb.phrase_hit("do you want me to go on?") is True
    assert vb.phrase_hit("Tell me EVERYTHING, please") is True
    assert vb.phrase_hit("keep going, I'm listening") is True
    assert vb.phrase_hit("unpack it for me") is True


def test_phrase_hit_word_boundaries():
    assert vb.phrase_hit("she's gonna be there") is False   # "go on" \b trap
    assert vb.phrase_hit("the longshoreman union") is False  # 'longConcat'
    assert vb.phrase_hit("") is False
    assert vb.phrase_hit(None) is False


def test_phrase_hit_custom_list():
    assert vb.phrase_hit("give me the deep dive", ["deep dive"]) is True
    assert vb.phrase_hit("go on", ["deep dive"]) is False


# ── the override paragraph ────────────────────────────────────────────────────

def test_suffix_default_overrides_and_limits_questions():
    s = vb.long_suffix(None)
    assert "SUSPENSION" in s and "OVERRIDE" in s
    assert "ignore" in s.lower() or "ignore" in s
    assert "question" in s.lower()


def test_suffix_custom_preserved():
    assert vb.long_suffix({"suffix": "be brief but long"}) == "be brief but long"
    assert vb.long_suffix({"suffix": "   "}) == vb.DEFAULTS["suffix"]


# ── live config ───────────────────────────────────────────────────────────────

def test_live_config_parses_with_known_types():
    cfg = vb.get_config(force=True)
    assert isinstance(cfg["enabled"], bool)            # user-owned on/off
    assert isinstance(cfg["phrases"], list) and cfg["phrases"]
    assert cfg["long_command"].startswith("/")
    assert cfg["short_command"].startswith("/")
    # canary: only lives in the YAML, so a silent fallback onto DEFAULTS
    # (the ROOT-path bug) can never hide again
    assert "lay it on me" in cfg["phrases"]


# ── behavioural: the sticky toggle through the real method ────────────────────

class _FakeSpeaker:
    def __init__(self):
        self.said = []

    def say(self, *a, **k):
        self.said.append(a)


def _verb_obj(monkeypatch, cfg):
    for m in list(sys.modules):
        if m == "she" or m == "core" or m.startswith("core."):
            sys.modules.pop(m)
    import she                                        # noqa: F401  (binds core)
    importlib.import_module("core.verbosity")         # the lazy-load target
    vb_live = sys.modules["core.verbosity"]           # the object she binds
    monkeypatch.setattr(vb_live, "get_config", lambda force=False: dict(cfg))
    obj = object.__new__(she.VoiceCompanion)
    obj.speaker = _FakeSpeaker()
    obj._long_mode = False
    return obj


_ON = dict(vb.DEFAULTS, enabled=True)


def test_verb_command_toggles_stickily(monkeypatch):
    obj = _verb_obj(monkeypatch, dict(_ON))
    assert obj._verb_command("/long") is True
    assert obj._long_mode is True
    assert len(obj.speaker.said) == 1                  # spoken confirm
    obj._verb_command("/long")                          # idempotent on
    assert obj._long_mode is True
    assert obj._verb_command("/short") is True
    assert obj._long_mode is False


def test_verb_command_disabled_is_inert(monkeypatch):
    obj = _verb_obj(monkeypatch, dict(vb.DEFAULTS))    # enabled: False
    assert obj._verb_command("/long") is False
    assert obj.speaker.said == []
    assert obj._long_mode is False


def test_verb_command_ignores_other_text(monkeypatch):
    obj = _verb_obj(monkeypatch, dict(_ON))
    assert obj._verb_command("you look long today") is False
    assert obj.speaker.said == []


# ── wiring tripwires ──────────────────────────────────────────────────────────

def test_she_system_message_has_the_long_branch():
    src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert '_vb.phrase_hit(query,verb_cfg.get("phrases"))' in src
    assert "_vb.long_suffix(verb_cfg)" in src
    assert "Keeprepliesunder{self.config.persona['max_reply_length']}characters." in src


def test_she_wires_the_toggle_everywhere():
    src = "".join(_SHE.read_text(encoding="utf-8").split())
    assert "def_verb_command(self,text:str)->bool:" in src
    assert "self._long_mode=(act==\"on\")" in src
    assert "self.speaker.say(str(line))" in src
    assert 'elifnamein("/long","/short"):' in src
    assert "self._long_mode=False" in src              # init state


def test_app_respond_consumes_toggles_before_the_llm():
    src = "".join(_APP.read_text(encoding="utf-8").split())
    assert "ifself._verb_command(text):" in src


def test_she_long_suffix_beats_the_short_cap_order():
    src = _SHE.read_text(encoding="utf-8")
    long_idx = src.find("_vb.long_suffix(verb_cfg)")
    short_idx = src.find("Keep replies under")
    assert long_idx < short_idx                        # appended FIRST, cap LAST
