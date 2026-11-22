"""Evelyn's desktop pet GUI.

This module owns process startup, CLI/config resolution, the PetCompanion
controller, and the Pet window. Reusable Qt-only building blocks live in
``src/gui/``; the shared conversation pipeline lives in ``src/she.py``.

Run:
    python src/app.py
    python src/app.py --hotkey f9 --size 400
    python src/app.py --art characters/marin --config-dir config
"""
import re
import sys
import json
import time
import logging
import argparse
import threading
import datetime
import random
from pathlib import Path
from typing import Callable, Dict, List, Optional

from gui.startup import (
    ensure_env_keys, install_crash_handlers, redirect_missing_std_streams,
)

# ── pythonw safety (item 5) ───────────────────────────────────────────────────
# pythonw.exe has no console; stdout/stderr are None, which crashes anything
# that tries to print (progress bars, logger StreamHandler, etc.).
ROOT = Path(__file__).resolve().parent.parent

redirect_missing_std_streams()
install_crash_handlers(ROOT)
# ─────────────────────────────────────────────────────────────────────────────

import pygame
import keyboard
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, QEvent
from PySide6.QtGui import QIcon, QGuiApplication, QWheelEvent, QRegion
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QSystemTrayIcon, QMenu,
    QInputDialog, QMessageBox, QLineEdit,
)

from she import VoiceCompanion
from core.config import Config
from core.model import parse_mood_tag, split_sentences
from core.recording import InterruptMonitor
from body_tags import StreamingTagParser, actions as body_actions, build_body_prompt

# Qt-facing building blocks (art, signal bus, widgets, background helpers).
from gui import art as _ui_art
from gui.bus import Bus
from gui.runtime import ProactiveTimer, WakeWordThread
from gui.widgets import (
    BubbleTail, FaceLabel, StatusIndicator, TouchZoneOverlay, TypingIndicator,
)

# Phase 2b: pure behavior state model + motion budget (no GL/Qt — safe to import
# unconditionally; only used when the Live2D renderer is active).
from behavior_state import BehaviorState, FeelingsBucket
from behavior_director import MotionBudget
from proactive import ProactiveChooser, ProactiveLimits, ProactiveScheduler
from body_stats import BodyStats

# Feature 13: touch-reaction zones + input classification.  core.touch is pure
# (json + dataclasses only) — safe to import unconditionally, incl. headless
# runs. Modes: quick click = talk (unchanged), Ctrl+click on a zone = poke,
# still-hold on the head zone = pat. Zones: head / face / shoulders ONLY.
from core.touch import (
    TouchClassifier, Outcome as TouchOutcome,
    find_zone as touch_find_zone,
    get_config as get_touch_config,
)
# Feature 15: the model frame — a model-space crop that fills the face square.
# It decouples the window size from the model's framing and gives the touch
# zones an exact mapping (no calibration constants).
from core.framing import Frame, Framing, resize_edge_at
# Feature 16: her circadian rhythm — the wall clock biases her state axes and
# the prompt (guarded: a missing module leaves today's behaviour exactly).
try:
    from core.circadian import (
        get_config as _circadian_cfg,
        prompt_line as _circadian_prompt_line,
        snapshot as _circadian_snapshot,
        describe_time as _circadian_describe,
        bias as _circadian_bias,
    )
except ImportError:            # pragma: no cover — headless stub environments
    _circadian_cfg = _circadian_prompt_line = _circadian_snapshot = None
    _circadian_describe = _circadian_bias = None
# Feature 17: her own life — a simulated daily agenda (pure; like circadian).
try:
    from core.agenda import (
        get_config as _agenda_cfg,
        prompt_line as _agenda_prompt_line,
        AgendaWatch as _AgendaWatch,
    )
except ImportError:            # pragma: no cover
    _agenda_cfg = _agenda_prompt_line = _AgendaWatch = None
# Feature 18: dangling topics — threads she asks about later (pure core).
try:
    from core.threads import (
        get_config as _threads_cfg,
        ThreadStore as _ThreadStore,
        ask_line as _threads_ask_line,
        STATE_PATH as _THREADS_STATE_PATH,
    )
except ImportError:            # pragma: no cover
    _threads_cfg = _ThreadStore = _threads_ask_line = None
    _THREADS_STATE_PATH = None
# Feature 19: interruption manners — recovery line, clash-free typing cut-in.
try:
    from core.interruption import (
        get_config as _interruption_cfg,
        RecoveryGate as _RecoveryGate,
    )
except ImportError:            # pragma: no cover
    _interruption_cfg = _RecoveryGate = None
# Feature 13 Stage B + C: escalation/gating/mapping + spoken reactions (pure).
from touch_reactions import (
    gate as touch_gate,
    startle_only as touch_startle_only,
    TouchEscalation, get_escalation,
    resolve as touch_resolve,
    VoiceGate as TouchVoiceGate,
    get_spoken as get_touch_spoken,
    tier as touch_spoken_tier,
    pick_line as touch_pick_line,
)

logger = logging.getLogger(__name__)  # noqa: E402

# ── Live2D availability check ─────────────────────────────────────────────────
# Import is attempted here so the flag is available before Pet is constructed.
# The actual widget import happens lazily in Pet.__init__ so a missing package
# does not abort startup.
_LIVE2D_AVAILABLE = False
try:
    import live2d  # type: ignore   # just probe; real import is in live2d_face
    from live2d_face import Live2DWidget, configure_opengl_format
    _LIVE2D_AVAILABLE = True
except Exception as _live2d_err:
    logging.getLogger(__name__).warning(
        f"live2d-py unavailable — sprite fallback active: {_live2d_err}"
    )
    print("[Evelyn] live2d renderer unavailable — using sprite mode. "
          "If you expected the 3D model, run diagnose.bat and send "
          "diagnose_out.txt to the developer.")
    Live2DWidget = None                # type: ignore
    configure_opengl_format = lambda: None  # type: ignore

# ── CLI args ──────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--config-dir", default=None)
ap.add_argument("--state-dir", default=None)
ap.add_argument("--hotkey", default="f9")
ap.add_argument("--art", default=None)
ap.add_argument("--size", type=int, default=None,
                help="Override starting face size (pixels, default from config/live2d.json or 400)")
args = ap.parse_args()

# ── Live2D config (size + framing) ────────────────────────────────────────────
LIVE2D_CFG_FILE = ROOT / "config" / "live2d.json"
STATE_FILE      = ROOT / "state" / "pet_state.json"
ART_DIR         = ROOT / "characters" / "marin"           # overridden in main()

def _load_live2d_cfg() -> dict:
    defaults = {
        "face_renderer": "sprites",
        "face": {
            "size": 400, "size_min": 200, "size_max": 900,
            "scale": 2.2, "offset_x": 0.0, "offset_y": 0.35, "fps": 30,
        },
        "invert_eye_x": False,
        "invert_head_x": False,
        "invert_head_y": False,
        "idle_loop": True,
        "body_control": True,
        "body_log": False,
        "state_in_prompt": False,
        "wink_swap": False,
        "look_invert_x": False,
        "behavior_director": True,
        "liveliness": 0.7,
        "lean_param": "ParamBodyAngleY0",
        "lean_sign": 1.0,
        "breath_pause_enabled": True,
        "breath_pause_every": [40.0, 90.0],
        "breath_pause_hold": [1.0, 1.5],
        "lip_sync_enabled": True,
        "lip_sync_offset_ms": 0.0,
        "motion_budget": {"max_head_angle": 8.0, "max_eye_offset": 0.5,
                          "max_body_drift": 2.0},
    }
    try:
        raw = json.loads(LIVE2D_CFG_FILE.read_text(encoding="utf-8"))
        face = raw.get("face", {})
        defaults["face_renderer"] = raw.get("face_renderer", defaults["face_renderer"])
        for k, v in face.items():
            defaults["face"][k] = v
        for k in ("invert_eye_x", "invert_head_x", "invert_head_y", "idle_loop",
                  "body_control", "body_log", "state_in_prompt", "wink_swap", "look_invert_x",
                  "behavior_director", "liveliness", "breath_pause_enabled",
                  "lip_sync_enabled"):
            if k in raw:
                defaults[k] = bool(raw[k]) if k not in ("liveliness",) else float(raw[k])
        defaults["lean_param"] = raw.get("lean_param", defaults["lean_param"])
        defaults["lean_sign"] = float(raw.get("lean_sign", defaults["lean_sign"]))
        defaults["breath_pause_every"] = raw.get("breath_pause_every", defaults["breath_pause_every"])
        defaults["breath_pause_hold"] = raw.get("breath_pause_hold", defaults["breath_pause_hold"])
        defaults["lip_sync_offset_ms"] = float(raw.get("lip_sync_offset_ms", defaults["lip_sync_offset_ms"]))
        if isinstance(raw.get("motion_budget"), dict):
            defaults["motion_budget"] = {
                **defaults["motion_budget"], **raw["motion_budget"]}
    except Exception:
        pass
    return defaults

_live2d_cfg = _load_live2d_cfg()
_FACE_RENDERER = _live2d_cfg["face_renderer"]   # "live2d" | "sprites"

# Body-control features (config/live2d.json).  body_control gates whether the
# streaming reply is parsed into sentences with tags; body_log logs every
# received/executed body action to logs/evelyn.log.
_BODY_CONTROL = bool(_live2d_cfg.get("body_control", True))
_BODY_LOG = bool(_live2d_cfg.get("body_log", False))

# Phase 4b: running body-control statistics, logged as [body] lines.
_BODY_STATS = BodyStats()

# Phase 3b: state-aware personality.  When on (and the BODY rule holds), a
# short "Right now you feel …" line is appended to the very end of the system
# message, derived only from her own feeling (never the user's behaviour).
# Default OFF — opt-in.
_STATE_IN_PROMPT = bool(_live2d_cfg.get("state_in_prompt", False))

# Tuning for the body-control L/R mapping (used in Phase 1c to map screen-side
# to model params on the Live2D model).  Left/right = the screen side as the
# viewer sees it; invert the X axis to compensate for models whose forward is
# flipped, and swap the wink eye params if the character's sides are reversed.
_WINK_SWAP = bool(_live2d_cfg.get("wink_swap", False))
_LOOK_INVERT_X = bool(_live2d_cfg.get("look_invert_x", False))

# Phase 2b: behavior-director switch (fall back to the old idle behaviour when
# off) and the motion budget (config numbers, subtle by default).  liveliness is
# a plain 0..1 scalar; presets/menu come in 2e.
_BEHAVIOR_DIRECTOR = bool(_live2d_cfg.get("behavior_director", True))
_LIVELINESS = float(_live2d_cfg.get("liveliness", 0.7))
_MOTION_BUDGET_CFG = _live2d_cfg.get("motion_budget", {}) or {}

# ── Feature 4: on-demand screen vision (config/vision.yaml) ───────────────────
# Opt-in.  The hotkey captures one frame into memory; it is attached to the next
# turn.  Nothing is captured unless the hotkey is pressed and nothing hits disk.
VISION_CFG_FILE = ROOT / "config" / "vision.yaml"


def _load_vision_cfg():
    try:
        from vision.screen_capture import load_config as _lv
        return _lv(VISION_CFG_FILE)
    except Exception as _ve:
        logging.getLogger(__name__).warning(f"vision config unavailable: {_ve}")
        return None


_VISION_CFG = _load_vision_cfg()

# ── Feature 5: text input (type a message instead of speaking) ────────────────
# Adds a right-click / tray "Type a message…" entry.  The typed text runs through
# the exact same turn pipeline as a voice turn, so voice keeps working as before.
# Feature 8B: in inline mode the input lives IN the speech bubble slot instead of
# a separate dialog — core.text_input also owns the pure slot state machine.
from core.text_input import InlineBubbleState  # noqa: E402

# Feature 8B: height of the single-line inline input inside the bubble strip.
_INPUT_BUBBLE_H = 44

TEXT_INPUT_CFG_FILE = ROOT / "config" / "text_input.yaml"


def _load_text_input_cfg():
    try:
        from core.text_input import load_config as _lt
        return _lt(TEXT_INPUT_CFG_FILE)
    except Exception as _te:
        logging.getLogger(__name__).warning(f"text input config unavailable: {_te}")
        return None


_TEXT_INPUT_CFG = _load_text_input_cfg()

# Phase 2c: which body param the lean uses + direction (config value; the preview
# tool lets you try the alternatives visually) and the optional breath pauses.
_LEAN_PARAM = _live2d_cfg.get("lean_param", "ParamBodyAngleY0")
_LEAN_SIGN = float(_live2d_cfg.get("lean_sign", 1.0))
_BREATH_PAUSE_ENABLED = bool(_live2d_cfg.get("breath_pause_enabled", True))
_BREATH_PAUSE_EVERY = _live2d_cfg.get("breath_pause_every", [40.0, 90.0])
_BREATH_PAUSE_HOLD = _live2d_cfg.get("breath_pause_hold", [1.0, 1.5])

# Phase 4a: real lip sync.  lip_sync_enabled gates envelope decoding; the offset
# tunes audio-vs-mouth timing (positive = mouth lags audio) in milliseconds.
_LIP_SYNC_ENABLED = bool(_live2d_cfg.get("lip_sync_enabled", True))
_LIP_SYNC_OFFSET_MS = float(_live2d_cfg.get("lip_sync_offset_ms", 0.0))

RELATIONSHIP_FILE = ROOT / "state" / "relationship.json"


def _closeness_baseline() -> float:
    """Small persistent baseline from config/relationship.json for the state model.
    Transient mood is NOT persisted; this only seeds valence/engagement base."""
    try:
        return float(json.loads(RELATIONSHIP_FILE.read_text(encoding="utf-8")).get("closeness", 0.0) or 0.0)
    except Exception:
        return 0.0

# Sentences shorter than this are merged into the next one (so exclamations like
# "Oh!" are not separate TTS calls) in the body-control streaming path.
_MIN_SENTENCE_CHARS = 10


def _resolve_body_actions(tags) -> list:
    """Convert body Tags (from body_tags) into concrete action dicts.

    The Live2D widget consumes these dicts (kind/name/params/duration/side).
    Mood tags are skipped — the mood is already applied separately via on_mood.
    """
    ba = body_actions()
    acts = []
    for t in tags:
        if t.kind == "clear":
            acts.append({"kind": "clear"})
        elif t.kind == "mood":
            continue
        elif t.kind == "fx":
            cfg = ba.fx.get(t.name)
            if cfg:
                acts.append({"kind": "fx", "name": t.name,
                             "params": cfg.get("params", {}) or {},
                             "expression_ops": cfg.get("expression_ops", []) or [],
                             "duration": cfg.get("duration", 4.0)})
        elif t.kind == "look":
            cfg = ba.look.get(t.name)
            if cfg:
                acts.append({"kind": "look", "name": t.name,
                             "params": cfg.get("params", {}) or {},
                             "expression_ops": cfg.get("expression_ops", []) or [],
                             "duration": cfg.get("duration", 0.8)})
        elif t.kind == "gesture":
            cfg = ba.gestures.get(t.name)
            if cfg:
                acts.append({"kind": "gesture", "name": t.name,
                             "params": cfg.get("params", {}) or {},
                             "expression_ops": cfg.get("expression_ops", []) or [],
                             "duration": cfg.get("duration", 0.9)})
        elif t.kind == "wink":
            wc = ba.wink_config()
            if wc:
                acts.append({"kind": "wink", "side": t.name,
                             "duration": wc.get("duration", 0.5)})
    return acts

# Determine starting size: --size CLI > saved state > config
def _resolve_initial_size() -> int:
    if args.size is not None:
        return max(200, min(900, args.size))
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        s = int(state.get("face_size", 0))
        if s >= 200:
            return s
    except Exception:
        pass
    return int(_live2d_cfg["face"].get("size", 400))

_INITIAL_SIZE = _resolve_initial_size()

# ── Module-level size constants (LEGACY — kept for FaceLabel/placeholder use
#    but NOT used directly in Pet geometry; Pet._apply_layout() owns that) ─────
SIZE     = _INITIAL_SIZE
WIDTH    = int(SIZE * 1.2)
BUBBLE_H = int(SIZE * 0.7)
FACE_TOP = BUBBLE_H + 6

MOOD_COLORS = {
    "idle":        "#b8a6ff",
    "joyful":      "#ffd56b",
    "excited":     "#ff9ecb",
    "embarrassed": "#ff8f8f",
    "sad":         "#8fb8ff",
    "angry":       "#ff6b6b",
    "surprised":   "#9be7ff",
    "curious":     "#b9f29a",
    "relaxed":     "#c9b8ff",
}


# Art defaults; configured after CLI/config resolution.
SIZE = _INITIAL_SIZE
WIDTH = int(SIZE * 1.2)
BUBBLE_H = int(SIZE * 0.7)
FACE_TOP = BUBBLE_H + 6
_ui_art.configure(size=SIZE, art_dir=ART_DIR)
MOOD_COLORS = _ui_art.MOOD_COLORS
load_art = _ui_art.load_art
load_pixmap = _ui_art.load_pixmap
placeholder = _ui_art.placeholder


# ── Extended companion ────────────────────────────────────────────────────────

class PetCompanion(VoiceCompanion):
    def __init__(self, config: Config, bus: Bus):
        super().__init__(config)
        self.bus = bus
        # Feature 23c: the Speaker reports abandoned synthesis from its worker
        # thread; the bus marshals the notice to the GUI thread.
        self.speaker.on_tts_failure = self._on_tts_failure
        self.turn_lock = threading.Lock()
        self.interrupt_monitor = InterruptMonitor(
            sample_rate=config.sample_rate,
            threshold_multiplier=3.5,
        )
        # Phase 3b: coarse, hysteresis-smoothed buckets of HER OWN feeling, held
        # here (not in BehaviorState).  ``_state_getter`` is wired from main() to
        # return pet._state_snapshot (the immutable per-tick snapshot), never the
        # live mutable BehaviorState.
        self._feelings = FeelingsBucket()
        self._state_getter: Optional[Callable[[], Optional[object]]] = None
        # Phase 3d: state-aware proactive messages.  The chooser picks a tone
        # pool from her feeling snapshot; the scheduler enforces the approved
        # limits (minimum interval, daily cap, quiet hours, do-not-disturb).
        self._proactive_chooser = ProactiveChooser(pools=self.config.proactive_pools)
        self._proactive_scheduler = ProactiveScheduler(ProactiveLimits(
            min_interval_minutes=self.config.proactive_min_interval_minutes,
            daily_cap=self.config.proactive_daily_cap,
            quiet_start_hour=self.config.proactive_quiet_start_hour,
            quiet_end_hour=self.config.proactive_quiet_end_hour,
            dnd=self.config.proactive_dnd,
        ))
        # Feature 4: on-demand screen vision (config/vision.yaml).  None when the
        # capture layer is unavailable — the hotkey path then stays inert.
        self.vision = self._make_screen_capture()

    def _make_screen_capture(self):
        """Build the Feature 4 capture helper, or None if it is unavailable."""
        try:
            from vision.screen_capture import ScreenCapture, load_config

            return ScreenCapture(load_config(VISION_CFG_FILE))
        except Exception as e:
            logger.warning("[vision] screen capture unavailable: %s", e)
            return None

    def _on_tts_failure(self, text: str = "") -> None:
        """Feature 23c: voice failed after retries; keep the reply readable."""
        try:
            logger.warning("[tts] voice unavailable; keeping text visible")
            self.bus.voice_issue.emit(text or "")
        except Exception:
            pass

    def _state_line(self) -> Optional[str]:
        """The short self-feeling line, or None when the state feature is off.

        Gated by the same rule as the BODY section (Live2D renderer active +
        live2d available + body_control) AND ``state_in_prompt``.  Describes
        only her own feeling (calm / cheerful / a little tired + energy) — never
        the user's behaviour, never ignored/wandered/away.
        """
        if not (_FACE_RENDERER == "live2d" and _LIVE2D_AVAILABLE
                and _BODY_CONTROL and _STATE_IN_PROMPT):
            return None
        if self._state_getter is None:
            return None
        try:
            snap = self._state_getter()
        except Exception:
            return None
        if snap is None:
            return None
        phrase = self._feelings.phrase(snap)
        return (f"Right now you feel {phrase}. Do not mention or complain about "
                "this state, about being ignored, or about how long the user "
                "was away unless they bring it up.")

    def system_message(self, query: Optional[str] = None) -> Dict[str, str]:
        msg = super().system_message(query=query)
        # BODY section is only injected when the Live2D renderer is actually
        # enabled (so a sprite/user, or a headless run, never gets it).
        if _FACE_RENDERER == "live2d" and _LIVE2D_AVAILABLE and _BODY_CONTROL:
            block = build_body_prompt(body_actions())
            if block:
                msg["content"] += block
                self._add_prompt_section("body", block)
        # Feature 16: the clock + what the hour means to her (empty when off).
        if _circadian_prompt_line is not None:
            try:
                line = _circadian_prompt_line(datetime.datetime.now(),
                                              _circadian_cfg())
                if line:
                    block = "\n\n" + line
                    msg["content"] += block
                    self._add_prompt_section("circadian", block)
            except Exception:
                pass
        # Feature 17: her agenda — what she is (or isn't) in the middle of.
        if _agenda_prompt_line is not None:
            try:
                line = _agenda_prompt_line(datetime.datetime.now(),
                                           _agenda_cfg())
                if line:
                    block = "\n\n" + line
                    msg["content"] += block
                    self._add_prompt_section("agenda", block)
            except Exception:
                pass
        # Phase 3b: the state line goes at the VERY END of the system message.
        line = self._state_line()
        if line is not None:
            block = "\n\n" + line
            msg["content"] += block
            self._add_prompt_section("state", block)
            if _BODY_LOG:
                logger.info("[body] state -> prompt: %s", line)
        # Feature 27: the one-turn question guard outranks everything above it.
        # If the previous assistant reply ended with a question, this reply may
        # not end with one (the interview-cadence hard stop).
        try:
            from core import aliveness as _alive
            last_reply = self.last_exchange[1] if self.last_exchange else ""
            guard = _alive.guard_block(last_reply)
            if guard:
                block = "\n\n" + guard
                msg["content"] += block
                self._add_prompt_section("question_guard", block)
        except Exception:
            pass
        return msg

    def start_turn(self):
        """Called by hotkey / face click — begins a voice turn."""
        self.speaker.stop()
        if not self.turn_lock.locked():
            threading.Thread(target=self._run_turn, daemon=True).start()

    def _run_turn(self):
        if not self.turn_lock.acquire(blocking=False):
            return
        b = self.bus
        try:
            b.status.emit("Listening…", True)
            b.listening.emit(True)   # Phase 2b: user is speaking (recording active)
            try:
                # Features 21+22: patient endpointing + backchannels
                # (the plain capture when config/listening.yaml is off).
                audio = self._record_voice()
            finally:
                b.listening.emit(False)  # silence detected / recording finished
            if audio is None or len(audio) / self.config.sample_rate < self.config.min_audio_length:
                b.status.emit("(didn't catch that)", False)
                self._missed_ack()
                return

            text = self.transcribe(audio)
            if not text:
                b.status.emit("(didn't catch that)", False)
                self._missed_ack()
                return

            self._respond(text)

        except Exception as e:
            b.status.emit(f"Error: {e}", False)
        finally:
            b.typing.emit(False)
            self.turn_lock.release()

    def _respond(self, text: str):
        """Command handling + history + reply for one turn (voice OR typed).

        Shared by ``_run_turn`` (voice) and ``_run_text_turn`` (Feature 5) so a
        typed message behaves exactly like a spoken one.
        """
        b = self.bus

        reply = self.try_command(text)
        if reply:
            b.status.emit(reply, False)
            self.speaker.say(reply)
            self.memory.log_turn("user", text)
            self.memory.log_turn("assistant", reply)
            self.last_exchange = (text, reply)
            return

        # Feature 24: /long & /short toggles are consumed before the LLM
        # (a mode flip is not a conversation turn).
        if self._verb_command(text):
            b.status.emit("Long mode toggled", False)
            return

        b.typing.emit(True)
        self.history.append({"role": "user", "content": text})
        self._maybe_summarize()

        # Feature 4: attach a pending screenshot (if any) to this turn.
        image_png = None
        if self.vision is not None:
            cap = self.vision.take()
            if cap is not None:
                image_png = cap.png
                logger.info(
                    "[vision] pending capture consumed by this turn "
                    "(%s monitor=%d %dx%d, %.1fs old)",
                    cap.mode, cap.monitor, cap.width, cap.height, cap.age())

        # Feature 20: when enabled, ask quietly whether the web would answer
        # this better and (rarely) run one DuckDuckGo search.  The results
        # arrive as extra SYSTEM context for this turn only; a disabled
        # feature, an off-topic message or a failed lookup changes nothing.
        try:
            web_block = self._web_context(text)
        except Exception as e:
            logger.debug(f"[websearch] skip: {e}")
            web_block = None

        messages = self.build_messages(
            query=text,
            web_block=web_block,
            has_image=image_png is not None,
        )

        try:
            reply_text, mood = self.stream_reply(messages, image_png=image_png)
            self._finalize_prompt_report()
        except Exception:
            # stream_reply already emitted and spoke the fallback; just clean up
            self._finalize_prompt_report()
            self.history.pop()
            return

        # item 15: if interrupted before any text was generated, discard the turn
        if not reply_text:
            self.history.pop()
            return

        self.history.append({"role": "assistant", "content": reply_text})
        self._trim_history()   # item 15: use safe trim instead of raw slice
        self.memory.log_turn("user", text)
        self.memory.log_turn("assistant", reply_text, mood)
        self.memory.after_turn()
        self.last_exchange = (text, reply_text)
        # Feature 29: her self-written {{remember: ...}} notes become facts
        # before passive extraction runs, so equivalents never duplicate.
        try:
            self._harvest_memory_notes(reply_text)
        except Exception as e:
            logger.debug(f"[self-notes] harvest skipped: {e}")
        self._extract_facts(text, reply_text)
        # Feature 18: notice dangling topics worth a follow-up later.
        try:
            self._extract_threads(text)
        except Exception:
            pass

    def start_text_turn(self, text: str):
        """Feature 5: send a typed message through the same path as a voice turn.

        Voice is untouched — this is simply a second entry point into
        :meth:`_respond`.
        """
        text = (text or "").strip()
        if not text:
            self.bus.status.emit("(nothing typed)", False)
            return
        self.speaker.stop()
        if self.turn_lock.locked():
            # Feature 19: cutting in — a typed message trips her interrupt
            # monitor and waits a moment instead of refusing outright.
            if not self._cut_in():
                self.bus.status.emit(
                    "(couldn't cut in — try again in a sec)", False)
                return
        self.bus.status.emit("💬 " + text, False)
        threading.Thread(target=self._run_text_turn, args=(text,), daemon=True).start()

    def _cut_in(self, timeout_s: float = 4.0) -> bool:
        """Feature 19: let a typed message interrupt the turn in progress.

        Trips the interrupt monitor (the streaming loops poll it and stop
        spawning new sentences) and, if it was her SPEECH still running
        (the turn already ended), just waits for the lock.  True = go."""
        try:
            self.interrupt_monitor.trip()
        except Exception:
            pass
        deadline = time.monotonic() + float(timeout_s)
        while self.turn_lock.locked():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        # her voice is already silenced (start_text_turn stops it up front)
        try:
            self.interrupt_monitor.reset()
        except Exception:
            pass
        return True

    def _run_text_turn(self, text: str):
        if not self.turn_lock.acquire(blocking=False):
            return
        b = self.bus
        try:
            self._respond(text)
        except Exception as e:
            b.status.emit(f"Error: {e}", False)
        finally:
            b.typing.emit(False)
            self.turn_lock.release()

    def _say_apart(self, text: str, mood=None):
        """Features 28/29 (legacy no-body path): speak only the voice part.

        Silent narration spans are diverted to the bubble (bus.text), memory
        notes vanish.  Used for the sentence/tail splits so narration written
        mid-line still lands in the bubble without being spoken."""
        from core import narration as _narr
        from core import memory_notes as _mn
        voice, notes = _narr.split_voice(_mn.strip_notes(text))
        for note in notes:
            self.bus.text.emit(" " + note + " ")
        if voice.strip():
            self.speaker.say(voice, mood)

    def stream_reply(self, messages, image_png=None):
        """Like VoiceCompanion.stream_reply but also drives the GUI bus.

        When body control is active (Live2D renderer + config body_control) the
        stream is parsed by StreamingTagParser into clean sentences carrying
        inline tags; otherwise the legacy leading-mood path runs unchanged."""
        from core.model import next_fallback
        mood = None
        interrupted = False   # item 14
        b = self.bus
        b.start.emit()

        # Part 3-A: start the interrupt monitor now; keep it alive until TTS
        # drains completely (not just until LLM streaming ends).
        # A 500 ms grace period at start prevents her own speaker output from
        # immediately triggering the threshold (feedback false-positive).
        self.interrupt_monitor.start()
        _grace_until = [0.0]   # mutable cell for lambda; set after first audio

        import time as _time

        body_on = _BODY_CONTROL and _FACE_RENDERER == "live2d" and _LIVE2D_AVAILABLE
        if _BODY_LOG:
            if body_on:
                logger.info("[body] BODY section + tag parsing ON "
                            "(renderer=%s, live2d_available=%s, body_control=%s)",
                            _FACE_RENDERER, _LIVE2D_AVAILABLE, _BODY_CONTROL)
            else:
                why = ("live2d renderer is active" if _FACE_RENDERER == "live2d"
                       else f"renderer={_FACE_RENDERER!r}")
                if not _LIVE2D_AVAILABLE and _FACE_RENDERER == "live2d":
                    why += ", live2d module unavailable"
                if not _BODY_CONTROL:
                    why += ", body_control disabled in config"
                logger.info("[body] BODY section + tag parsing OFF (%s)", why)

        try:
            if body_on:
                full, mood = self._stream_body(messages, b, _grace_until, _time, image_png)
                return full, mood

            full = buf = head = ""
            mood_done = False
            for chunk in self.model.stream_chat(messages, image_png=image_png):
                # Only check interrupt after grace period has elapsed
                if (self.interrupt_monitor.triggered
                        and _time.monotonic() > _grace_until[0]):
                    self.speaker.stop()
                    interrupted = True
                    break

                piece = chunk["message"]["content"]
                if not mood_done:
                    head += piece
                    parsed, remaining = parse_mood_tag(head)
                    if parsed is not None:
                        mood = parsed
                        piece, mood_done = remaining, True
                        b.mood.emit(mood)
                    elif len(head.lstrip()) > 30:
                        piece, mood_done = head, True
                    else:
                        continue

                full += piece
                buf += piece
                b.text.emit(piece)

                parts = split_sentences(buf, min_length=20)
                if len(parts) == 2:
                    complete, buf = parts
                    self._say_apart(complete, mood)
                    # Start grace period on first queued sentence
                    if _grace_until[0] == 0.0:
                        _grace_until[0] = _time.monotonic() + 0.5

            # item 14: don't speak leftover buffer if the user interrupted
            if not mood_done and not interrupted:
                full = buf = head
                b.text.emit(head)
            if buf and not interrupted:
                self._say_apart(buf, mood)
                if _grace_until[0] == 0.0:
                    _grace_until[0] = _time.monotonic() + 0.5

            # Feature 19: she yields the floor after a real mid-reply cut-off
            if interrupted:
                self._interrupt_yield(len(full))

            return full.strip(), mood

        except Exception as e:
            fallback = next_fallback()
            b.text.emit(fallback)
            self.speaker.say(fallback)
            raise

        finally:
            # Part 3-A: poll until all TTS has finished, then stop the monitor.
            # This is done in a daemon thread so the finally block returns fast.
            def _wait_and_stop():
                import time as _t
                deadline = _t.monotonic() + 30.0   # hard cap: 30 s max wait
                while _t.monotonic() < deadline:
                    if not self.speaker.is_playing():
                        break
                    if self.interrupt_monitor.triggered:
                        self.speaker.stop()
                        break
                    _t.sleep(0.05)
                self.interrupt_monitor.stop()
                self.interrupt_monitor.reset()

            threading.Thread(target=_wait_and_stop, daemon=True).start()
            b.end.emit()

    def _stream_body(self, messages, b, _grace_until, _time, image_png=None):
        """Body-control streaming path: parse tags into sentences and speak each
        with its tags.  Returns ``(clean_full_text, reply_mood)``.

        Bubble text is revealed at audio start (via on_sentence_start → on_body),
        NOT here.  Very short sentences (< _MIN_SENTENCE_CHARS) are merged into the
        next one so "Oh!" / "Hmm." are not separate TTS calls; their tags stay
        attached to the first sentence and fire at the merged audio start."""
        from body_tags import StreamingTagParser
        from emotion.mood_delivery import (DeliveryState, decorate_speech,
                                           get_config as _delivery_cfg)
        parser = StreamingTagParser()
        full_parts: List[str] = []
        reply_mood = None          # first mood seen = reply default (like legacy)
        mood_shown = None          # last mood emitted to the GUI bus
        pending = None             # a too-short sentence awaiting merge
        # Phase 4b: per-reply honoured tags by kind (across all spoken frames).
        reply_kinds: Dict[str, int] = {}
        # Feature 8: per-reply delivery state; the cooldown counter survives on
        # the instance so it spans replies.
        st = getattr(self, "_delivery_state", None)
        if st is None:
            st = DeliveryState()
            self._delivery_state = st
        st.begin_reply()
        dcfg = _delivery_cfg()

        def _speak(text, mood, tags):
            if _grace_until[0] == 0.0:
                _grace_until[0] = _time.monotonic() + 0.5
            # Features 28/29: the voice gets the spoken text only.  Silent
            # narration (double-asterisk beats) is diverted to the bubble
            # display instead, and {{remember: ...}} self-notes vanish (they
            # are harvested from the full reply after the turn).  Body tags
            # attached to a frame whose spoken text is empty are dropped:
            # tag-free narration is what the prompt teaches.
            from core import narration as _narr
            from core import memory_notes as _mn
            voice, narration_notes = _narr.split_voice(_mn.strip_notes(text))
            for note in narration_notes:
                b.text.emit(" " + note + " ")
            if not voice.strip():
                return
            # Feature 8: mood -> interjection / fallback gesture.  Applied here
            # (after full_parts captured the model's text), so the bubble and
            # the voice hear it while history/memory stay clean.
            try:
                voice, tags = decorate_speech(
                    voice, mood, tags, dcfg, st, reply_kinds,
                    cap_remaining=parser.cap_remaining)
            except Exception as e:
                logger.warning("[delivery] decorate failed: %s", e)
            self.speaker.say(voice, mood, tags=tags)

        def _count_tags(tags) -> None:
            for t in tags:
                reply_kinds[t.kind] = reply_kinds.get(t.kind, 0) + 1

        def _consume(f):
            nonlocal reply_mood, mood_shown, pending
            if f.mood is not None:
                if reply_mood is None:
                    reply_mood = f.mood
                if f.mood != mood_shown:
                    mood_shown = f.mood
                    b.mood.emit(f.mood)
            if pending is not None:
                # Merge the held short sentence into this one.  Tags stay attached
                # to the first sentence; both fire together at the merged audio start.
                text = pending.text + " " + f.text
                tags = pending.tags + f.tags
                _count_tags(tags)
                full_parts[-1] = text      # replace the held entry
                pending = None
                _speak(text, f.mood, tags)
                return
            if len(f.text) < _MIN_SENTENCE_CHARS:
                pending = f
                full_parts.append(f.text)
                return
            _count_tags(f.tags)
            full_parts.append(f.text)
            _speak(f.text, f.mood, f.tags)

        for chunk in self.model.stream_chat(messages, image_png=image_png):
            if (self.interrupt_monitor.triggered
                    and _time.monotonic() > _grace_until[0]):
                self.speaker.stop()
                # Feature 19: she yields the floor after a real cut-off
                self._interrupt_yield(sum(len(p) for p in full_parts))
                return " ".join(full_parts).strip(), reply_mood
            piece = chunk["message"]["content"]
            for f in parser.feed(piece):
                _consume(f)
        for f in parser.finish():
            _consume(f)
        # A trailing short sentence with nothing to merge into still gets spoken.
        if pending is not None:
            _count_tags(pending.tags)
            _speak(pending.text, pending.mood, pending.tags)
        # Phase 4b: log per-reply tag stats (tags by kind + dropped counts).
        if _BODY_LOG:
            pstats = parser.stats()
            logger.info(
                "[body] reply stats: honoured=%s unknown_dropped=%d cap_dropped=%d",
                reply_kinds,
                pstats["unknown_dropped"],
                pstats["cap_dropped"],
            )
        return " ".join(full_parts).strip(), reply_mood

    def say_proactive(self, message: str):
        """Speak a proactive message and push it to the GUI."""
        mood, text = parse_mood_tag(message)
        from core import narration as _narr
        from core import memory_notes as _mn
        voice, notes = _narr.split_voice(_mn.strip_notes(text))
        display = " ".join([*notes, voice]).strip()
        if mood:
            try:
                self.bus.mood.emit(mood)
            except Exception:
                pass
        self.bus.proact.emit(display)
        if voice.strip():
            self.speaker.say(voice)
        self.memory.log_turn("assistant", display, mood=mood if mood else None)

    def _choose_proactive(self) -> Optional[str]:
        """State-aware pick (Phase 3d): choose a tone pool from her feeling and
        draw a message from it.  Returns None if suppressed (ignored long —
        fewer messages, never more insistent; no reproach)."""
        snap = None
        if self._state_getter is not None:
            try:
                snap = self._state_getter()
            except Exception:
                snap = None
        return self._proactive_chooser.choose(snap)

    def _interrupt_yield(self, spoken_chars: int = 0) -> None:
        """Feature 19: after being cut off mid-reply, she yields the floor
        (rate-limited; a too-short line or a rapid-fire interrupt chain gets
        a silent stop instead of an endless 'yes?' loop)."""
        try:
            cfg = _interruption_cfg()
            gate = getattr(self, "_interrupt_gate", None)
            if gate is None:
                gate = _RecoveryGate()
                self._interrupt_gate = gate
            line = gate.allow(datetime.datetime.now(), spoken_chars, cfg)
            if line:
                self.speaker.say(line)
                mood = cfg.get("yield_mood") or None
                if mood:
                    self.bus.mood.emit(mood)
        except Exception:
            pass

    def _thread_followup(self) -> str:
        """Feature 18: ask about the oldest dangling topic, at most once a
        day.  Empty string means 'not now' (the agenda/pools speak instead)."""
        if _ThreadStore is None:
            return ""
        try:
            cfg = _threads_cfg()
            if not cfg.get("enabled", False):
                return ""
            store = getattr(self, "_thread_store", None)
            if store is None:
                store = _ThreadStore(_THREADS_STATE_PATH)
                self._thread_store = store
            now = datetime.datetime.now()
            day = now.strftime("%Y-%m-%d")
            if getattr(self, "_thread_day", None) != day:
                self._thread_day = day
                self._thread_asked = 0
            if self._thread_asked >= int(cfg.get("max_ask_per_day", 1)):
                return ""
            t = store.take(now, cfg)
            if t is None:
                return ""
            self._thread_asked += 1
            self._thread_last_topic = t.topic
            import random as _rn
            return _threads_ask_line(t.topic, cfg, rng=_rn.Random())
        except Exception:
            return ""

    def _agenda_excuse(self) -> str:
        """Feature 17: a small 'what I'm in the middle of' opener, if one is
        due (rate-limited by AgendaWatch).  Empty string means no."""
        if _AgendaWatch is None:
            return ""
        try:
            watch = getattr(self, "_agenda_watch", None)
            if watch is None:
                watch = _AgendaWatch()
                self._agenda_watch = watch
            return watch.proactive_line(datetime.datetime.now(),
                                         _agenda_cfg()) or ""
        except Exception:
            return ""

    def circadian_tick(self) -> None:
        """Feature 16: push the hour's bias into her BehaviorState.

        Called from the pet's slow tick (~every 5 s — the drift half-lives
        are minutes, so this is plenty frequent).  Off / uncalled hours pass
        (0, 0, 0) = today's behaviour exactly."""
        st = getattr(self, "behavior_state", None)
        if st is None or _circadian_snapshot is None:
            return
        try:
            e = _circadian_snapshot(datetime.datetime.now(), _circadian_cfg())
            dv, da, de = _circadian_bias(e)
            st.set_circadian_bias(dv, da, de)
        except Exception:
            logger.exception("[circadian] bias tick failed")

    def generate_proactive(self) -> str:
        """Produce a proactive message.

        Primary path: a due dangling thread (grounded in something the user
        actually said), otherwise one short line generated by the model using
        her FULL system prompt — the real persona, memory, agenda, circadian
        state and last exchange, not a generic "you are a companion" card.
        Rate-limited agenda excuse and the state-aware static pools remain as
        fallbacks.  Ignored-long suppression returns "" (the timer reschedules
        without speaking).
        """
        if not self.config.proactive_generate:
            return (self._thread_followup() or self._agenda_excuse()
                    or self._choose_proactive() or "")

        grounded = self._thread_followup()
        if grounded:
            return grounded

        try:
            try:
                from core import aliveness as _alive
                instruction = _alive.proactive_instruction()
            except Exception:
                instruction = ""
            if not instruction:
                instruction = ("Say one short spoken line (1–2 sentences) that "
                               "starts a conversation from where you actually "
                               "are. No labels, no quotes, no stage directions.")

            prompt = [
                self.system_message(query=None),
                {"role": "user", "content": (
                    "(The user has been silent for a while. Follow the "
                    "instruction below and reply with the spoken line only — "
                    "no labels, no asterisks, no quotes.)\n\n"
                    + instruction)},
            ]
            full = ""
            for chunk in self.model.stream_chat(prompt, temperature=0.9):
                full += chunk["message"]["content"]
            line = full.strip().strip('"')
            if 10 <= len(line) <= 300:
                return line
        except Exception as e:
            logging.getLogger(__name__).warning(f"Proactive generation failed: {e}")

        return (self._thread_followup() or self._agenda_excuse()
                or self._choose_proactive() or "")

    @staticmethod
    def _safe_error() -> str:
        from core.model import next_fallback
        return next_fallback()


class Pet(QWidget):
    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # ── Dynamic size state (replaces module-level constants) ──────────────
        _cfg_face = _live2d_cfg.get("face", {})
        self.face_size    = _INITIAL_SIZE
        self.face_size_min = int(_cfg_face.get("size_min", 200))
        self.face_size_max = int(_cfg_face.get("size_max", 900))

        # Model framing (Live2D scale/offset, also saved to state)
        self._model_scale    = float(_cfg_face.get("scale", 2.2))
        self._model_offset_x = float(_cfg_face.get("offset_x", 0.0))
        self._model_offset_y = float(_cfg_face.get("offset_y", 0.35))

        # Persisted idle-loop override.  Loaded from pet_state.json FIRST so it
        # wins over the config default (config cannot re-clobber a user toggle).
        self._idle_enabled = self._load_idle_override(default=bool(_live2d_cfg.get("idle_loop", True)))

        # Phase 2e: persisted liveliness (0..1) + behavior-director on/off.  Both
        # are user overrides that survive restarts and the config cannot re-clobber.
        self._liveliness = self._load_liveliness_override(default=_LIVELINESS)
        self._behavior_director_user = self._load_behavior_director_override(default=_BEHAVIOR_DIRECTOR)

        # Live2D mood map for on_mood acceptance check
        self._live2d_moods: set[str] = set()

        # Debounce timer for saving state after resize
        self._save_state_timer = QTimer(self, singleShot=True, timeout=self._save_state_now)

        # Speech bubble — text gets set before first layout
        self.bubble = QLabel(self)
        self.bubble.setWordWrap(True)
        self.bubble.hide()

        # Feature 8B: inline text input sharing the SAME slot as the bubble.
        # Created only when text input is enabled in inline mode: while she is
        # silent this text box IS the bubble, and it disappears the moment her
        # speech is revealed (see _apply_slot).
        self.input_bubble: Optional[QLineEdit] = None
        self._bubble_state = InlineBubbleState(
            inline=bool(_TEXT_INPUT_CFG and _TEXT_INPUT_CFG.enabled
                        and _TEXT_INPUT_CFG.mode == "inline"))
        if self._bubble_state.inline:
            self.input_bubble = QLineEdit(self)
            self.input_bubble.setPlaceholderText(_TEXT_INPUT_CFG.inline_placeholder)
            self.input_bubble.setFrame(False)
            try:
                from core.text_input import MAX_INPUT_CHARS
                self.input_bubble.setMaxLength(MAX_INPUT_CHARS)
            except Exception:
                pass
            self.input_bubble.returnPressed.connect(self._on_input_submit)
            # Esc clears the line; a tap on HER (idle) bubble swaps back to
            # the input (window mode leaves both widgets alone entirely).
            self.input_bubble.installEventFilter(self)
            self.bubble.installEventFilter(self)

        # Typing indicator (shown while LLM generates, before first token)
        self.typing_label = TypingIndicator(self)

        # Status indicator — mic / speaker pill bottom-right of face
        self.status_indicator = StatusIndicator(self)

        # ── Face widget — Live2D or sprite fallback ───────────────────────────
        self.face = None
        self._use_live2d = False
        # Phase 2b: shared BehaviorState for the behavior director.  Closeness
        # from config/relationship.json seeds a small persistent baseline; the
        # app feeds mood/interact/hotkey/listening inputs and the widget drives
        # it per-frame (speaking state, director writes).
        self.behavior_state = BehaviorState(closeness=_closeness_baseline())
        # Phase 3b: immutable snapshot of the state, published each tick by the
        # widget's director loop and read once per turn by the companion (never
        # the live mutable BehaviorState).  Initialised now so reads are safe
        # even before the first frame.
        self._state_snapshot = self.behavior_state.snapshot()

        def _publish_state_snapshot():
            if self.behavior_state is not None:
                self._state_snapshot = self.behavior_state.snapshot()
        self._publish_state_snapshot = _publish_state_snapshot
        if _FACE_RENDERER == "live2d" and _LIVE2D_AVAILABLE:
            try:
                model_json  = ROOT / "characters" / "marin" / "live2d" / "简.model3.json"
                moods_json  = ROOT / "config" / "live2d_moods.json"
                self.face   = Live2DWidget(
                    model_json, moods_json, parent=self,
                    invert_eye_x=_live2d_cfg.get("invert_eye_x", False),
                    invert_head_x=_live2d_cfg.get("invert_head_x", False),
                    invert_head_y=_live2d_cfg.get("invert_head_y", False),
                    idle_enabled=self._idle_enabled,
                    body_enabled=_BODY_CONTROL,
                    wink_swap=_WINK_SWAP,
                    look_invert_x=_LOOK_INVERT_X,
                    max_simultaneous_fx=body_actions().max_simultaneous_fx,
                    behavior_director_enabled=self._behavior_director_user,
                    behavior_state=self.behavior_state,
                    motion_budget=MotionBudget(
                        max_head_angle=float(_MOTION_BUDGET_CFG.get("max_head_angle", 8.0)),
                        max_eye_offset=float(_MOTION_BUDGET_CFG.get("max_eye_offset", 0.5)),
                        max_body_drift=float(_MOTION_BUDGET_CFG.get("max_body_drift", 2.0)),
                    ),
                    liveliness=self._liveliness,
                    lean_param=_LEAN_PARAM,
                    lean_sign=_LEAN_SIGN,
                    breath_pause_enabled=_BREATH_PAUSE_ENABLED,
                    breath_pause_every=tuple(_BREATH_PAUSE_EVERY),
                    breath_pause_hold=tuple(_BREATH_PAUSE_HOLD),
                    lip_sync_enabled=_LIP_SYNC_ENABLED,
                    lip_sync_offset_ms=_LIP_SYNC_OFFSET_MS,
                    emotions_json=ROOT / "config" / "emotions.yaml",
                )
                self.face._body_log_enabled = _BODY_LOG
                self.face._snapshot_publisher = self._publish_state_snapshot
                self.face.set_model_scale(self._model_scale)
                self.face.set_model_offset(self._model_offset_x, self._model_offset_y)
                fps = int(_cfg_face.get("fps", 30))
                self.face.set_fps(fps)
                self._use_live2d = True
                self._live2d_moods = set(self.face.known_moods())
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Live2D widget init failed, falling back to sprites: {e}"
                )
                print(f"[Evelyn] Live2D model failed to load ({e}). "
                      "Using sprite mode. If you expected the 3D model, run "
                      "diagnose.bat and send diagnose_out.txt to the developer.")
                self.face = None

        if self.face is None:
            self.face = FaceLabel(self)

        self.mood        = "idle"
        self.mouth_open  = False
        self.speaking    = False
        self.bubble_text = ""
        self._pending_body = []        # staged tags awaiting 1c model application
        self._bubble_above = True  # flips when near screen top
        # Hover-reveal: the bubble/input is only shown while the pointer is over
        # the pet OR while she is speaking/generating (otherwise it stays hidden
        # so it never steals space or focus).  See _sync_slot_visibility.
        self._hovering = False

        # Manga side bubble: the speech panel lives in its own column to the
        # LEFT of her head (instead of above/below).  The column's width is
        # reserved in the window PERMANENTLY; it is simply clipped/clicked
        # through while the panel is hidden (see _update_window_mask).  The
        # window therefore never resizes or moves when the panel appears -
        # that resize+move used to make the model visibly jump/flicker for a
        # frame on every hover or new sentence (user report).
        self._use_side_bubble   = True
        self._bubble_shown      = False
        self._face_x_px         = 0     # fixed left offset of the face inside the window
        self._bubble_bw         = 0     # width of the manga panel column (reserved even while hidden)
        self._bubble_side       = -1    # -1: the bubble column sits on her
                                        #     LEFT; its width is permanently
                                        #     reserved in the window geometry.
                                        #  +1 remains supported but never set.
        self._bubble_gap        = 2     # px between the panel and her face
                                        # (user: closer to the model)
        self._bubble_tail       = BubbleTail(self, color="#f5f5f0")
        self._bubble_tail.hide()

        # ── Drag state ────────────────────────────────────────────────────────
        self._press_pos   = QPoint()
        self._origin      = QPoint()
        self._moved       = False
        # Alt/Shift drag for model panning
        self._pan_press_offset_x = 0.0
        self._pan_press_offset_y = 0.0
        self._pan_press_cursor   = QPoint()
        self._panning            = False

        # ── Feature 13 Stage A: touch-reaction state ─────────────────────────
        self._touch = TouchClassifier()
        # Pats need a still-hold: a held press emits no move events, so poll
        # (80 ms) checks whether hold_seconds elapsed with cancel_px undisturbed.
        self._touch_pat_timer = QTimer(self, interval=80, timeout=self._touch_poll)
        self._touch_debug = False
        self._touch_overlay = None
        self._touch_hover_zone: Optional[str] = None
        # Feature 13 Stage B: escalation machine + bookkeeping for pat credit.
        self._touch_sm = TouchEscalation()
        self._touch_pat_fired_s = 0.0     # hold amount already credited at fire
        self._listening = False           # mic recording active (gate for B)
        # Feature 13 Stage C: spoken-reaction rate limiter (10 s min / hourly cap).
        self._touch_voice = TouchVoiceGate()
        # Feature 14: drag-physics runtime switch (right-click menu).
        self._drag_physics_on = True
        # Feature 15: the model frame (model-space crop the square shows).
        self._frame: Optional[Frame] = None
        # Feature 15: free-form resize (the margin next to the model square).
        self._resize_edge: Optional[str] = None
        self._resize_start_size = int(self.face_size)
        # Hover (no buttons) delivery: the pet AND the face child need mouse
        # tracking (the GL widget ignores move events, which bubbles them up).
        self.setMouseTracking(True)
        if self.face is not None:
            self.face.setMouseTracking(True)

        # ── Timers ────────────────────────────────────────────────────────────
        # Feature 8B: when the speech display expires the slot becomes the
        # inline input again (window mode: plain bubble.hide() as before).
        # The input additionally waits `return_delay` seconds AFTER her TTS
        # has actually finished (grace timer below) before taking the slot.
        self.hide_timer  = QTimer(self, singleShot=True, timeout=self._on_hide_timeout)
        self._return_delay_ms = int(
            (_TEXT_INPUT_CFG.return_delay if _TEXT_INPUT_CFG else 2.0) * 1000)
        self._input_grace_armed = False
        self._input_grace_timer = QTimer(
            self, singleShot=True, interval=max(0, self._return_delay_ms),
            timeout=self._on_input_grace)
        self.idle_timer  = QTimer(self, singleShot=True, timeout=self._go_idle)
        self.tick        = QTimer(self, interval=140, timeout=self._on_tick)
        self.tick.start()

        # ── Apply initial layout (sizes and positions all widgets) ────────────
        self._apply_layout(initial=True)

        # Feature 15: adopt the model frame now that the square has its size
        # (the frame is configured in config/live2d.json; on the first run it
        # is derived from the legacy scale/offset and then saved back).
        self._init_frame()
        # the frame's aspect decides the model's HEIGHT -> re-layout with it
        self._apply_layout()

        # Feature 8B: the slot starts as the inline input, but with hover-reveal
        # it stays hidden until the pointer is over the pet (or she speaks).
        if self.input_bubble is not None:
            self._place_bubble()
            self._sync_slot_visibility()

        self.face.update_art("idle", False, crossfade=False)

    # ── Layout engine (THE single source of truth for all geometry) ──────────

    @property
    def _face_height_px(self) -> int:
        """The model's HEIGHT in px (derived - never stored)."""
        return self._face_height()

    def _face_height(self) -> int:
        """The model's HEIGHT in px.

        The width is the size the user picks ("face size"); the height
        follows the model frame's aspect - a taller frame means a taller
        window, which crops her body vertically (never stretches her).
        """
        s = float(getattr(self, "face_size", 0) or 0)
        if s <= 0.0:
            return 0
        frame = getattr(self, "_frame", None)
        if frame is None or frame.w <= 0.0 or frame.h <= 0.0:
            return int(round(s))
        return int(max(80, round(s * (frame.h / frame.w))))

    def _apply_layout(self, initial: bool = False) -> None:
        """Recompute all geometry from self.face_size and reposition every widget.

        Must be called from the GUI thread.  Keeps the face's bottom-center
        fixed on screen (when not initial) so the pet grows upward / outward.
        """
        s  = self.face_size
        fh = self._face_height()          # the model's height (frame aspect)
        wc = int(s * 1.2)                 # compact window width (no panel)
        # Fixed left offset of the face: the same 0.1s margin on BOTH sides of
        # the compact window, i.e. the face is centered in the compact frame.
        fpx = (wc - s) // 2

        if not initial:
            # Capture the CURRENT screen position of the face BEFORE any of the
            # geometry below is recalculated, so the face can stay anchored no
            # matter which side the panel opens on.
            old_face_cx  = self.x() + self._face_x_px + s // 2
            old_face_bot = self.y() + self._face_top + self._face_height_px
        else:
            old_face_cx = None
            old_face_bot = None

        # Manga side column: its width is reserved PERMANENTLY so opening or
        # closing the speech panel never resizes or moves this window.  It used
        # to grow leftward on every reveal; the resize + move + child-
        # reposition sequence made the model visibly jump/flicker for a frame
        # on every hover or new sentence (user report).  The panel now simply
        # appears inside the reserved column, and _update_window_mask() keeps
        # that column click-through while the panel is hidden.
        if self._use_side_bubble:
            # User tuning: a NARROWER column, sitting close against her, so
            # the open panel swallows far less of the screen.  No screen-room
            # clamping: the window is allowed to clip at the left edge (the
            # bubble clips, SHE never leaves the spot she was parked in).
            bw = max(200, int(s * 0.55))
            self._bubble_bw = bw
            need = self._bubble_gap + bw          # gap + panel width
            w  = wc + need
            # Panel on her LEFT: shift the face right inside the window so it
            # stays put on screen; the window itself never moves on reveal.
            self._bubble_side = -1
            self._face_x_px = fpx + need
        else:
            self._bubble_bw = 0
            w  = wc
            self._face_x_px = fpx
        bh = int(s * 0.28)    # compact chat strip (was 0.7 — wasted half the window)
        ft = bh + 6          # FACE_TOP — y of the face inside the window
        # Total window height: bubble strip above + face + padding + bubble strip below
        win_h = ft + fh + 4 + bh

        # Store layout values on self for other methods
        self._face_size = s
        self._win_width  = w
        self._bubble_h   = bh
        self._face_top   = ft
        self._win_height = win_h

        # Also update the module-level globals so FaceLabel helper fns still work
        global SIZE, WIDTH, BUBBLE_H, FACE_TOP
        SIZE     = s
        WIDTH    = w
        BUBBLE_H = bh
        FACE_TOP = ft
        _ui_art.SIZE = s

        # Window geometry
        if initial:
            self.setFixedSize(w, win_h)
        else:
            # Move so the FACE stays fixed on screen: this path only runs for
            # face-size / frame-aspect changes, never for panel reveals.  The
            # face offset inside the resized window shifts accordingly so she
            # never drifts.
            new_x = old_face_cx - self._face_x_px - s // 2
            new_y = old_face_bot - ft - s
            self.setFixedSize(w, win_h)
            self.move(new_x, new_y)
            # No clamping at all here: the move above is anchored to her
            # current face position on BOTH axes.  If the window ends up
            # poking off-screen (a very large face, no room at the edge) the
            # window clips — she stays exactly where she was parked.
            # The startup restore separately places a fresh session safely.

        # Face widget (a RECTANGLE: the height follows the frame's aspect).
        # Use the LOGICAL face offset _face_x_px (fpx + the permanently
        # reserved side column) so the widget really sits where the window
        # math believes she is.
        # Bugfix (user report): the bare compact `fpx` left the widget at the
        # far left of the WIDE window while all placement math assumed she was
        # `need` px further right - the bubble was drawn ON her and she slid
        # sideways every time the speech bubble appeared.
        face_x = self._face_x_px
        self.face.setGeometry(face_x, ft, s, fh)
        # Feature 13: keep the touch-zone debug overlay covering the face
        if self._touch_overlay is not None:
            self._touch_overlay.setGeometry(0, 0, s, fh)
        # Feature 15: the model FRAME survives a resize — the same model
        # region stays visible, it just gets more (or fewer) pixels.
        if self._frame is not None and self._use_live2d and self.face is not None:
            self.face.apply_frame(self._frame, float(s), float(fh))
            self._sync_model_from_frame()

        # Bubble (+ the inline input — separate widgets now: the speech bubble
        # lives in the manga side column, the typing box stays at the compact
        # width pinned to the bottom strip).  The speech bubble always owns the
        # reserved column width, even while hidden, so revealing it never has
        # to re-wrap or re-layout anything.
        if self._use_side_bubble:
            self.bubble.setFixedWidth(self._bubble_bw)
        else:
            self.bubble.setFixedWidth(w - 20)
        self.bubble.setMaximumHeight(bh - 10)
        self.bubble.setStyleSheet(self._bubble_style())
        if self.input_bubble is not None:
            # The typing box is SEPARATE and does not widen the window: always
            # the compact width, so it looks right whether her speech panel is
            # open on the side or not.
            self.input_bubble.setFixedWidth(wc - 20)
            self.input_bubble.setFixedHeight(_INPUT_BUBBLE_H)
            self.input_bubble.setStyleSheet(self._bubble_style())

        # Indicator / typing — pin them
        self._place_indicator()
        if (self.bubble.isVisible()
                or (self.input_bubble is not None and self.input_bubble.isVisible())):
            self._place_bubble()

        # Keep the reserved (currently hidden) side column click-through/hidden.
        self._update_window_mask()

    def _compact_left(self) -> int:
        """X of the resting compact frame's left edge inside the window.

        The window permanently reserves the side-panel column on its left, so
        the visible "pet frame" starts ``reserved`` px into the window.  All
        compact-width widgets (inline input, typing dots, above/below bubble)
        are positioned relative to this, and the hidden-window mask is exactly
        this rectangle.
        """
        s = int(getattr(self, "face_size", 0) or 0)
        wc = int(s * 1.2)
        fpx = (wc - s) // 2
        face_x = int(getattr(self, "_face_x_px", fpx) or 0)
        return max(0, face_x - fpx)

    def _update_window_mask(self) -> None:
        """Clip the window to what is actually visible.

        The side column is always part of the window geometry (so revealing
        the panel never resizes or moves the window and the model cannot
        flicker).  While the panel is hidden that column is empty, so the mask
        shrinks back to the compact frame - the empty column neither paints
        nor swallows desktop clicks/hover.  Revealing the panel only expands
        the mask over it; no window geometry changes.
        """
        if not getattr(self, "_use_side_bubble", False):
            self.clearMask()
            return
        s = int(getattr(self, "face_size", 0) or 0)
        
        # Safety: if face_size is 0 or too small, clear the mask entirely.
        # This prevents the model from becoming invisible when calculations fail.
        if s < 50:
            self.clearMask()
            return
        
        wc = int(s * 1.2)
        fpx = (wc - s) // 2
        left = self._compact_left()
        h = max(1, int(getattr(self, "_win_height", 0) or self.height()))
        if getattr(self, "_bubble_shown", False):
            # Panel visible: expose the panel column plus the compact frame.
            x = min(fpx, left)
            w = max(1, int(getattr(self, "_win_width", 0) or 0) - x)
            self.setMask(QRegion(x, 0, w, h))
        else:
            # Resting: exactly the compact window the pet always had.
            self.setMask(QRegion(left, 0, max(1, wc), h))

    def _clamp_to_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        # Use the screen that contains most of the window
        best = None
        best_area = -1
        wr = QRect(self.pos(), self.size())
        for sc in screens:
            inter = wr.intersected(sc.availableGeometry())
            area  = inter.width() * inter.height()
            if area > best_area:
                best_area = area
                best = sc
        if best is None:
            best = screens[0]
        ag = best.availableGeometry()
        x  = max(ag.left(), min(self.x(), ag.right()  - self.width()))
        y  = max(ag.top(),  min(self.y(), ag.bottom() - self.height()))
        if x != self.x() or y != self.y():
            self.move(x, y)

    # ── Position persistence ──────────────────────────────────────────────────

    def _load_idle_override(self, default: bool = True) -> bool:
        """Read the persisted idle-loop override from pet_state.json.

        This is a user override that survives restarts and is NOT overwritten
        by config/live2d.json."""
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return bool(s.get("idle_enabled", default))
        except Exception:
            return default

    def set_idle_enabled(self, enabled: bool) -> None:
        """Toggle the Live2D idle loop and persist the override immediately."""
        self._idle_enabled = bool(enabled)
        if self._use_live2d and self.face is not None:
            self.face.set_idle_enabled(self._idle_enabled)
        self.save_position()

    def _load_liveliness_override(self, default: float = 0.7) -> float:
        """Read the persisted liveliness (0..1) from pet_state.json.  A user
        override that survives restarts; when absent falls back to the config."""
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            v = float(s["liveliness"])
            return max(0.0, min(1.0, v))
        except Exception:
            return default

    def _load_behavior_director_override(self, default: bool = True) -> bool:
        """Read the persisted behavior-director on/off from pet_state.json.  A
        user override that survives restarts; when absent falls back to the config."""
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return bool(s["behavior_director"])
        except Exception:
            return default

    def set_liveliness(self, value: float) -> None:
        """Set the liveliness scalar and persist it.  Also applies to the widget."""
        self._liveliness = max(0.0, min(1.0, float(value)))
        if self._use_live2d and self.face is not None:
            self.face.set_liveliness(self._liveliness)
        self.save_position()

    def set_behavior_director_enabled(self, enabled: bool) -> None:
        """Toggle the behavior director (vs legacy idle) and persist it."""
        self._behavior_director_user = bool(enabled)
        if self._use_live2d and self.face is not None:
            self.face.set_behavior_director(self._behavior_director_user)
        self.save_position()

    def restore_position(self, screen):
        # Validate saved position against all screens before using it
        try:
            s = json.loads(STATE_FILE.read_text())
            x, y = int(s["x"]), int(s["y"])
            # Saved coordinates describe the COMPACT (resting) frame, not the
            # real window - the window permanently reserves the side-panel
            # column on its left.  Validate the compact frame and shift the
            # real window left by that reserved width so the face lands at
            # the exact spot the user parked her.
            wc = int(self.face_size * 1.2)
            pet_rect = QRect(x, y + self._face_top, wc, self._face_height_px)
            screens = QGuiApplication.screens()
            on_screen = any(pet_rect.intersects(sc.availableGeometry()) for sc in screens)
            if on_screen:
                self.move(x - self._compact_left(), y)
                # Restore framing values if present
                if "model_scale" in s:
                    self._model_scale    = float(s["model_scale"])
                    self._model_offset_x = float(s.get("model_offset_x", 0.0))
                    self._model_offset_y = float(s.get("model_offset_y", 0.35))
                    if self._use_live2d:
                        self.face.set_model_scale(self._model_scale)
                        self.face.set_model_offset(self._model_offset_x, self._model_offset_y)
            # Phase 2e: always apply persisted liveliness + director toggle, even
            # when the saved window position is off-screen (same fix as idle).
            if self._use_live2d and self.face is not None:
                self.face.set_liveliness(self._liveliness)
                self.face.set_behavior_director(self._behavior_director_user)
            return
        except Exception:
            pass
        g = screen.availableGeometry()
        self.move(g.right() - self._win_width - 20,
                  g.bottom() - (self._face_top + self._face_height_px) - 20)

    def save_position(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Persist the COMPACT-FRAME position derived from where her face
        # actually is, so the saved x is valid for a resting window even when
        # the speech panel is open (that window sits further left than the
        # spot the user parked her, and could even be clipped off-screen).
        s       = int(self.face_size)
        wc      = int(s * 1.2)
        fpx     = (wc - s) // 2
        face_cx = self.x() + self._face_x_px + s // 2
        data: dict = {
            "x": face_cx - fpx - s // 2, "y": self.y(),
            "face_size": self.face_size,
            "idle_enabled": self._idle_enabled,
        }
        if self._use_live2d:
            data["model_scale"]    = self._model_scale
            data["model_offset_x"] = self._model_offset_x
            data["model_offset_y"] = self._model_offset_y
            data["liveliness"]          = round(self._liveliness, 2)
            data["behavior_director"]   = self._behavior_director_user
        STATE_FILE.write_text(json.dumps(data))

    def _save_state_debounced(self) -> None:
        """Debounce save — called after every resize/pan; persists after 500 ms idle."""
        self._save_state_timer.start(500)

    def _save_state_now(self) -> None:
        self.save_position()

    def _place_indicator(self):
        """Pin the status pill to the bottom-right corner of the face.

        Uses the fixed face offset (not window-center) so it stays on the face
        even though the window permanently reserves the side-panel column.
        """
        face_right  = self._face_x_px + self._face_size
        face_bottom = self._face_top + self._face_height_px
        iw = self.status_indicator.sizeHint().width() or 36
        ih = self.status_indicator.sizeHint().height() or 26
        self.status_indicator.setGeometry(face_right - iw - 6, face_bottom - ih - 6, iw, ih)

    # ── Bubble placement ──────────────────────────────────────────────────────

    def _place_bubble(self):
        """Place the widgets SEPARATELY.

        * The SPEECH bubble lives in the manga side column (left of her head)
          where supported, otherwise flips above/below the face.
        * The TYPING box is a separate widget pinned to the bottom strip of the
          compact frame — it never shares the speech bubble's slot or touches
          the side column.

        Feature 8B still positions BOTH occupants for the typing indicator.
        """
        ih = _INPUT_BUBBLE_H
        bh = max(40, self.bubble.sizeHint().height())
        self.bubble.setFixedHeight(bh)
        # Compact-frame origin inside the (slightly wider) real window: the
        # side panel's column is reserved permanently on the left.
        x0 = self._compact_left()

        # ── Speech bubble ─────────────────────────────────────────────────────
        if self._use_side_bubble and self._bubble_shown:
            y = max(0, self._face_top + self._face_height_px // 2 - bh // 2)
            bw = self._bubble_bw
            gap = self._bubble_gap
            if self._bubble_side == -1:
                # Panel on her LEFT, close against her: `gap` px from the face
                # edge, tail's sharp point exactly touching her.
                x = self._face_x_px - gap - bw
                self._bubble_tail.set_flip(True)
                self._bubble_tail.move(x + bw + gap - 14, y + 4)
            else:
                # Panel on her RIGHT (legacy symmetric branch: never selected
                # by _apply_layout, which always reserves the column on her
                # left): bubble sits right of the face and the tail joins its
                # LEFT edge pointing left toward her.
                x = self._face_x_px + self._face_size + gap
                self._bubble_tail.set_flip(False)
                self._bubble_tail.move(x - gap, y + 4)
            self.bubble.move(x, y)
            self._bubble_tail.raise_()
            self._bubble_tail.setVisible(True)
        else:
            screen_top = QGuiApplication.primaryScreen().availableGeometry().top()
            near_top = (self.y() - screen_top) < self._bubble_h + 20
            if near_top:
                # Place bubble BELOW the face — inside the extra transparent strip
                self.bubble.move(x0 + 10, self._face_top + self._face_height_px + 4)
            else:
                # Place bubble ABOVE the face (original behaviour)
                self.bubble.move(x0 + 10, self._face_top - bh)
            self._bubble_tail.hide()

        # ── Typing box (separate widget, pinned to the bottom strip) ──────────
        if self.input_bubble is not None:
            self.input_bubble.setFixedHeight(ih)
            self.input_bubble.move(x0 + 10, self._face_top + self._face_height_px + 4)

        # Typing dots sit where the user's eye already is: her bubble when it
        # is showing, otherwise the resting slot (the typing-box position).
        if self.bubble.isVisible():
            self.typing_label.move(x0 + 10, self.bubble.y())
        elif self.input_bubble is not None:
            self.typing_label.move(x0 + 10, self.input_bubble.y())
        else:
            self.typing_label.move(x0 + 10, self.bubble.y())
        # In side mode the dots belong in the panel column too, whichever side
        # the panel is on.
        if self._use_side_bubble and self._bubble_shown:
            self.typing_label.move(self.bubble.x() + 10, self.bubble.y())

    @staticmethod
    def _bubble_style(highlight: bool = False) -> str:
        """The ONE style shared by the speech label and the inline input.

        A flat manga-style paper panel — LIGHTLY TRANSLUCENT (user request)
        so she shows through behind it.  Alpha 200/255 = just a bit of see-
        through while the text stays fully opaque.
        """
        border = "rgba(51,51,51,175)"
        base = ("background: rgba(245,245,240,200); color: #222222;"
                f"border: 2px solid {border}; border-radius: 10px;"
                "padding: 10px; font-size: 15px;")
        if highlight:
            # Slightly darker paper for the brief 'vision/attention' flash.
            return ("background: rgba(224,224,216,212); color: #111111;"
                    f"border: 2px solid {border}; border-radius: 10px;"
                    "padding: 10px; font-size: 15px;")
        return base

    def _slot_revealed(self) -> bool:
        """True when the bubble/input may be visible: the pointer is over the
        pet, or she is speaking/generating.  A running turn ("hidden" mode)
        never shows either widget regardless."""
        if self._bubble_state.mode == "hidden":
            return False
        if self._hovering:
            return True
        if self._bubble_busy() or self.typing_label.isVisible():
            return True
        return False

    def _sync_slot_visibility(self) -> None:
        """Apply the hover/speaking reveal gate to whichever widget owns the
        slot.  ``_apply_slot`` decides which widget is the candidate (via the
        state machine's mode); this decides whether it is actually visible.

        Also drives the manga side column: when the panel changes from hidden
        to shown (or back) the window MASK expands/shrinks over the column
        that is permanently reserved for it.  Window geometry never changes
        here, so the model cannot flicker when the panel appears.
        """
        mode = self._bubble_state.mode
        shown = self._slot_revealed()
        if not shown:
            self.bubble.hide()
            if self.input_bubble is not None:
                # keep the draft text so hovering back restores it
                self.input_bubble.hide()
            self._bubble_tail.hide()
        elif mode == "speech":
            self.bubble.setVisible(True)
            if self.input_bubble is not None:
                self.input_bubble.hide()
            self._bubble_tail.setVisible(self._use_side_bubble and self._bubble_shown)
        else:  # "input"
            self.bubble.hide()
            if self.input_bubble is not None:
                self.input_bubble.setVisible(True)
            self._bubble_tail.hide()

        # The manga side column's space is reserved permanently.  Revealing or
        # hiding the speech panel only toggles the window MASK over it - never
        # run _apply_layout here: resizing/moving the window on reveal is what
        # made the model flicker on every hover/sentence (user report).
        side_shown = shown and mode == "speech"
        if self._use_side_bubble and side_shown != self._bubble_shown:
            self._bubble_shown = side_shown
            if side_shown:
                self._place_bubble()
            self._update_window_mask()

    def _apply_slot(self, action: str) -> None:
        """Map a bubble-slot action (InlineBubbleState) onto the widgets.

        The two widgets never show at once: "speech" is her words, "input" is
        the typing box, "hidden" is a running turn/status owning the slot.
        Which one is the candidate is decided here; actual visibility is gated
        by :meth:`_sync_slot_visibility` (hover or speaking).
        """
        if action == "speech":
            # New words → the grace countdown starts over when she goes quiet.
            self._input_grace_armed = False
            self._input_grace_timer.stop()
            if self.input_bubble is not None:
                self.input_bubble.hide()
            self.bubble.adjustSize()
            self._place_bubble()
        elif action == "input":
            self.hide_timer.stop()
            self._input_grace_timer.stop()
            self.bubble.hide()
            if self.input_bubble is not None:
                self._place_bubble()
        elif action == "hidden":
            # Turn in progress: no grace pending until her next pause.
            self._input_grace_armed = False
            self._input_grace_timer.stop()
            self.bubble.hide()
            if self.input_bubble is not None:
                self.input_bubble.hide()
        self._sync_slot_visibility()

    def _bubble_busy(self) -> bool:
        """True while audio/generation/turn owns the slot, so the input must
        not pop up in the middle of her reply."""
        if self._audio_busy():
            return True
        if self.typing_label.isVisible():
            return True
        try:
            if _companion_ref is not None and _companion_ref.turn_lock.locked():
                return True
        except Exception:
            pass
        return False

    def _on_hide_timeout(self):
        """Feature 8B: display finished → the slot becomes the inline input
        (window mode: plain bubble.hide(), exactly as Feature 5 shipped).

        The input does NOT pop back the moment the timer fires: while her
        TTS/audio/turn is still running we poll (500 ms), and once it really
        is done the input waits one more `return_delay` grace period (default
        2 s) before taking the slot.
        """
        action = self._bubble_state.on_hide_timeout(busy=self._bubble_busy())
        if action == "retry":
            self.hide_timer.start(500)       # poll until her TTS is really done
            return
        if action == "input" and not self._input_grace_armed:
            # First idle moment after her speech: hold the input back while
            # the grace period runs (0 s delay = apply immediately).
            self._input_grace_armed = True
            if self._return_delay_ms > 0:
                self._input_grace_timer.start()
                return
        self._apply_slot(action)

    def _on_input_grace(self):
        """The grace period elapsed: the input takes the slot back — unless
        she started talking again in the meantime (then the new speech owns
        the slot and the countdown restarts at her next pause)."""
        if self._bubble_busy():
            return
        self._apply_slot("input")

    def _on_bubble_tapped(self):
        """Feature 8B: tapping her visible bubble swaps the slot to the input."""
        action = self._bubble_state.on_tap(busy=self._bubble_busy())
        if action == "input":
            self._apply_slot(action)
            if self.input_bubble is not None:
                self.input_bubble.setFocus()

    def eventFilter(self, obj, ev):
        # Feature 8B: taps stay inside the slot — her (idle) bubble swaps back
        # to the input; Esc in the input clears it (Enter is returnPressed).
        if (self.input_bubble is not None and obj is self.input_bubble
                and ev.type() == QEvent.KeyPress
                and ev.key() == Qt.Key_Escape):
            self.input_bubble.clear()
            return True
        if obj is self.bubble and ev.type() == QEvent.MouseButtonPress:
            self._on_bubble_tapped()
            return True
        return super().eventFilter(obj, ev)

    def _on_input_submit(self):
        """Enter in the inline input → the same turn pipeline as voice/dialog."""
        if self.input_bubble is None or not self.input_bubble.isVisible():
            return
        clean = InlineBubbleState.on_submit(self.input_bubble.text())
        if clean is None:
            return                              # empty Enter: stay typing
        self.input_bubble.clear()
        self.input_bubble.hide()
        if _companion_ref is not None:
            _companion_ref.start_text_turn(clean)

    def focus_text_input(self):
        """Feature 8B: bring the slot back to the input and focus it
        (right-click menu, tray menu and the hotkey all land here in inline
        mode).  Never yanks the slot away from a running turn."""
        if self.input_bubble is None:
            return
        if self.input_bubble.isVisible():
            self.input_bubble.setFocus()
            return
        if self._bubble_busy():
            return
        self.hide_timer.stop()
        self.bubble.hide()
        self.input_bubble.setVisible(True)
        self._place_bubble()
        self.input_bubble.setFocus()

    def show_bubble(self, text: str):
        self.bubble.setText(text)
        self._apply_slot(self._bubble_state.on_show(text))

    # ── Slot handlers (called from GUI thread via signals) ────────────────────

    def on_start(self):
        self.hide_timer.stop()
        self.bubble_text = ""
        self.show_bubble("")
        self.status_indicator.set_state("idle")  # done listening

    def on_text(self, s: str):
        self.typing_label.hide_indicator()   # item 12: dots gone on first token
        self.bubble_text += s
        self.show_bubble(self.bubble_text)

    def on_mood(self, m: str):
        known = (
            m in MOOD_COLORS
            or (ART_DIR / f"{m}.png").exists()
            or (ART_DIR / f"{m}.gif").exists()
            or (self._use_live2d and m in self._live2d_moods)
        )
        self.mood = m if known else "idle"
        self.face.update_art(self.mood, self.mouth_open, crossfade=True)

    def on_body(self, payload):
        """Sentence audio started: reveal its bubble text and apply body tags.

        Runs in the GUI thread (queued connection from the Speaker playback
        thread).  payload = (gen, text, tags) or, with Phase 4a lip sync, also
        (envelope, frame_ms).  The bubble text is revealed here (at audio start)
        rather than at generation time, the tags are applied to the Live2D model
        (look/gesture/wink/fx/clear), and the lip-sync envelope is installed so
        the mouth tracks the actual speech."""
        if len(payload) >= 6:
            gen, text, tags, envelope, frame_ms, t0 = payload
        else:
            gen, text, tags = payload
            envelope, frame_ms, t0 = None, None, None
        # 2c: each spoken sentence's audio start triggers a small speaking nod
        # (scaled by arousal + liveliness inside the widget).  Fires in the GUI
        # thread at playback start, including for the plain (no-tag) path.
        if self._use_live2d and hasattr(self.face, "note_speech_start"):
            self.face.note_speech_start(text or "")
        # Phase 4a: install the decoded loudness envelope so _tick_mouth tracks
        # speech instead of the random flap.  Only when live2d + lip sync is on.
        if (self._use_live2d and envelope
                and hasattr(self.face, "set_mouth_envelope")):
            self.face.set_mouth_envelope(envelope, frame_ms)
        elif (self._use_live2d and hasattr(self.face, "clear_mouth_envelope")):
            # No envelope for this clip (decode failed/idle) — drop any leftover
            # so the previous clip's timing never reapplies.
            self.face.clear_mouth_envelope()
        if _BODY_CONTROL:
            # Bubble text reveals when the sentence's audio starts.
            if text:
                self.typing_label.hide_indicator()
                self.bubble_text += text
                self.show_bubble(self.bubble_text)
        if tags:
            # Phase 4b: record tag-to-audio-start latency + per-kind action counts.
            if t0 is not None:
                _BODY_STATS.record_latency(time.monotonic() - t0)
            kinds: Dict[str, int] = {}
            for t in tags:
                kinds[t.kind] = kinds.get(t.kind, 0) + 1
            _BODY_STATS.record_reply(kinds)
            if _BODY_LOG:
                label = ", ".join(f"{t.kind}:{t.name or ''}" for t in tags)
                logger.info(f"[body] applying: {label}")
                if t0 is not None:
                    logger.info("[body] tag->audio latency: %.0f ms",
                                (time.monotonic() - t0) * 1000.0)
            # Phase 2b: mood tags are impulses into the behavior state (fire at
            # audio start, not at generation time, per the plan).  The mood
            # expression itself is applied separately via on_mood/update_art.
            if self.behavior_state is not None:
                for t in tags:
                    if t.kind == "mood":
                        self.behavior_state.mood_impulse(t.name)
            if self._use_live2d and hasattr(self.face, "apply_body_actions"):
                self.face.apply_body_actions(_resolve_body_actions(tags))
            self._pending_body = list(tags)

    def on_listening(self, on: bool):
        """Phase 2b: user speaking flag from recording state (bus queued to the
        GUI thread).  Drives the behavior state's 'listening' (attentive gaze)."""
        self._listening = bool(on)   # Feature 13: recording gate for touch
        if self.behavior_state is not None:
            self.behavior_state.set_listening(on)

    def on_end(self):
        self.hide_timer.start(max(5000, len(self.bubble_text) * 70))

    def on_voice_issue(self, text: str = ""):
        """TTS gave up on a sentence: leave a readable note, never silence."""
        note = "(Voice unavailable — the reply is in the bubble.)"
        if "voice unavailable" in self.bubble_text.lower():
            self.hide_timer.start(8000)
            return
        self.typing_label.hide_indicator()
        if not self.bubble_text.strip() and text.strip():
            # The first sentence failed before its audio could reveal it.
            self.bubble_text = text.strip()
        self.bubble_text = (self.bubble_text.rstrip() + "\n\n" + note).strip()
        self.show_bubble(self.bubble_text)
        self.hide_timer.start(8000)

    def on_status(self, msg: str, keep: bool):
        self.on_start()
        self.on_text(msg)
        if not keep:
            self.hide_timer.start(3000)
        # Show mic indicator while waiting for input
        if "listen" in msg.lower():
            self.status_indicator.set_state("listening")
            self._place_indicator()
        else:
            self.status_indicator.set_state("idle")

    def on_typing(self, active: bool):
        if active:
            self.typing_label.show_indicator()
            self._place_bubble()
        else:
            self.typing_label.hide_indicator()

    def on_vision_flash(self, msg: str):
        """Feature 4: brief, visible confirmation that a screenshot was armed."""
        self.on_status(msg, False)
        if not (_VISION_CFG and _VISION_CFG.indicator):
            return
        # Highlight the bubble so the confirmation reads at a glance.
        # Feature 8B: style BOTH slot occupants — whichever is visible shows it.
        try:
            style = self._bubble_style(highlight=True)
            self.bubble.setStyleSheet(style)
            if self.input_bubble is not None:
                self.input_bubble.setStyleSheet(style)
            QTimer.singleShot(1200, self._restore_bubble_style)
        except Exception:
            pass

    def _restore_bubble_style(self):
        try:
            style = self._bubble_style()
            self.bubble.setStyleSheet(style)
            if self.input_bubble is not None:
                self.input_bubble.setStyleSheet(style)
        except Exception:
            pass

    def prompt_text_input(self):
        """Feature 5/8B: focus the inline bubble input (inline mode) or open
        the separate dialog (mode: window).  Voice still works either way."""
        if not (_TEXT_INPUT_CFG and _TEXT_INPUT_CFG.enabled):
            return
        if self.input_bubble is not None:
            # Inline mode: no window — the bubble itself is the text box.
            self.focus_text_input()
            return
        try:
            from core.text_input import normalize_input

            text, ok = QInputDialog.getText(
                self, "Type to Evelyn", _TEXT_INPUT_CFG.placeholder)
        except Exception as e:
            logger.warning("[text_input] dialog failed: %s", e)
            return
        if not ok:
            return
        clean = normalize_input(text)
        if clean is None:
            self.on_status("(nothing typed)", False)
            return
        if _companion_ref is not None:
            _companion_ref.start_text_turn(clean)

    def on_proactive(self, msg: str):
        """Show proactive message in bubble (already spoken by speaker)."""
        self.on_start()
        self.on_text(msg)
        self.on_end()

    # ── Animation tick ────────────────────────────────────────────────────────

    @staticmethod
    def _audio_busy() -> bool:
        """Part 3-C: delegate to Speaker.is_playing() which covers the gap between
        sentences (synth queue non-empty but pygame not yet busy).
        Falls back to False if the companion ref isn't set yet or mixer is uninit.
        """
        try:
            if _companion_ref is not None:
                return _companion_ref.speaker.is_playing()
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    def _check_frame_config(self) -> None:
        """Adopt a frame that the frame editor wrote to config/live2d.json.

        The editor is a separate process, so the running pet watches the
        config (its own 140 ms tick) and re-frames + re-layouts when the
        frame changed - that is how a new aspect (a taller window) shows up
        immediately, without a restart.  The file is only parsed when its
        mtime changed."""
        if not (self._use_live2d and self.face is not None):
            return
        try:
            mtime = LIVE2D_CFG_FILE.stat().st_mtime
        except OSError:
            return
        if mtime == getattr(self, "_frame_cfg_mtime", None):
            return
        self._frame_cfg_mtime = mtime
        try:
            raw = json.loads(LIVE2D_CFG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        face = raw.get("face") if isinstance(raw, dict) else None
        if not isinstance(face, dict):
            return
        new = Frame.from_dict(face.get("frame"))
        cur = self._frame
        if new is None or cur is None:
            return
        if (abs(new.x - cur.x) < 1e-4 and abs(new.y - cur.y) < 1e-4
                and abs(new.w - cur.w) < 1e-4 and abs(new.h - cur.h) < 1e-4):
            return
        self._frame = new
        self.face.apply_frame(new, float(self._face_size),
                               float(self._face_height_px))
        self._apply_layout()      # the height follows the new aspect
        if _BODY_LOG:
            logger.info("[frame] adopted from config: %s", new.as_dict())

    def _on_tick(self):
        # Feature 16: keep her circadian bias in step with the wall clock
        # (~every 5 s; the axes drift in minutes-half-lives, so this counts).
        _comp = _companion_ref
        self._circadian_sequencer = getattr(self, "_circadian_sequencer", 0) + 1
        if self._circadian_sequencer % 36 == 0 and _comp is not None:
            try:
                _comp.circadian_tick()
            except Exception:
                pass
        # Feature 15: pick up frame changes made in the frame editor (live).
        self._check_frame_config()
        if self._audio_busy():
            self.speaking = True
            self.idle_timer.stop()
            if self._use_live2d:
                # Live2D drives its own smooth mouth; just signal speaking state
                if not self.mouth_open:
                    self.mouth_open = True
                    self.face.update_art(self.mood, True, crossfade=False)
            else:
                # Sprite mode: toggle mouth art at 140 ms for lip-flap effect
                self.mouth_open = not self.mouth_open
                self.face.update_art(self.mood, self.mouth_open, crossfade=False)
            if self.status_indicator._state != "talking":
                self.status_indicator.set_state("talking")
                self._place_indicator()
        else:
            if self.mouth_open:
                self.mouth_open = False
                self.face.update_art(self.mood, False, crossfade=False)
            if self.speaking:
                self.speaking = False
                self.idle_timer.start(4000)
                self.status_indicator.set_state("idle")

    def _go_idle(self):
        self.mood = "idle"
        self.face.update_art("idle", False, crossfade=True)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._origin    = self.pos()
            self._moved     = False
            # Phase 2b: any pointer contact with the pet is an interaction.
            if self.behavior_state is not None:
                self.behavior_state.interact()
            # Check for Alt/Shift pan modifier
            mods = e.modifiers()
            if (mods & Qt.AltModifier) or (mods & Qt.ShiftModifier):
                self._panning = True
                self._pan_press_cursor   = e.globalPosition().toPoint()
                self._pan_press_offset_x = self._model_offset_x
                self._pan_press_offset_y = self._model_offset_y
            else:
                self._panning = False
                # Feature 15: a press on the empty margin next to the model
                # SQUARE resizes the pet (free-form, pixel-exact) instead of
                # moving the window.  The margin is the window's side padding.
                edge = self._resize_edge_at(e)
                if edge is not None:
                    self._resize_edge = edge
                    self._resize_start_size = int(self.face_size)
                    self.setCursor(Qt.SizeHorCursor)
                else:
                    # Feature 13 Stage A: classify the press (Ctrl+click poke /
                    # head-hold pat).  Alt/Shift bypass us and keep native pan.
                    self._touch_arm(e, bool(mods & Qt.ControlModifier))
        elif e.button() == Qt.RightButton:
            # Right-click on pet body — show quick menu
            self._show_context_menu(e.globalPosition().toPoint())

    # ── Feature 15: free-form window resizing ───────────────────────────────

    _RESIZE_BAND = 10        # px: how wide the invisible resize edge is

    def _resize_edge_at(self, e) -> Optional[str]:
        """Which resize edge (if any) the press is on.

        The pet's window is wider than the model square (1.2x), so there is
        an empty margin on each side of the model - that margin acts as an
        invisible resize handle, exactly like a normal window border.  The
        bubble strips (above / below) are NOT resize areas.
        """
        if self.face is None:
            return None
        local = e.position()
        fx = local.x() - self.face.x()          # face-local px
        fy = local.y() - self.face.y()
        return resize_edge_at(fx, fy, float(self._face_height_px),
                              float(self._RESIZE_BAND))

    def _resize_to(self, new_size: int) -> None:
        """Apply a new face-square size (the whole layout follows)."""
        new_size = int(max(self.face_size_min,
                           min(self.face_size_max, new_size)))
        
        # Safety: log if size is suspiciously small (helps diagnose invisible bug)
        if new_size < 50:
            logger.warning(
                f"[resize] face_size={new_size} is too small! "
                f"(min={self.face_size_min}, max={self.face_size_max})"
            )
        
        if new_size != int(self.face_size):
            self.face_size = new_size
            self._apply_layout()
            self._save_state_debounced()

    def enterEvent(self, e):
        """Hover-reveal: show the bubble/input while the pointer is over the
        pet.  If she is idle (the slot is the input), focus the box right away
        so the user can just start typing."""
        self._hovering = True
        self._sync_slot_visibility()
        if (self.input_bubble is not None
                and self._bubble_state.mode == "input"
                and self.input_bubble.isVisible()):
            self.input_bubble.setFocus()
        super().enterEvent(e)

    def leaveEvent(self, e):
        """Hide the bubble/input again once the pointer leaves — unless she is
        still speaking or generating (then it stays until that finishes)."""
        self._hovering = False
        self._sync_slot_visibility()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        # Feature 13 Stage A: hover cursor over zones + still-hold tracking.
        # Inert when the feature is disabled or in sprite mode; the ordinary
        # drag/pan/eye-tracking flow below is completely untouched.
        self._touch_move(e)
        if e.buttons() & Qt.LeftButton:
            if self._resize_edge is not None:
                # Free-form resize: the margin drag sets the size directly.
                delta = e.globalPosition().toPoint() - self._press_pos
                if self._resize_edge == "right":
                    self._resize_to(self._resize_start_size + delta.x())
                else:
                    self._resize_to(self._resize_start_size - delta.x())
                self._moved = True
                return
            if self._panning and self._use_live2d:
                # Alt/Shift drag → move the MODEL FRAME (the model follows the
                # cursor exactly: the conversion uses the runtime's measured
                # pixels-per-model-unit, not a guessed constant).
                delta = e.globalPosition().toPoint() - self._pan_press_cursor
                frame = self._frame
                if frame is None:
                    frame = self.face.visible_frame(float(self._face_size),
                                              float(self._face_height_px))
                if frame is not None:
                    pu = self.face.px_per_unit() or 1.0
                    # dragging right moves the model right → the visible
                    # region moves LEFT in model space (and down → up in
                    # model space, since model y points up)
                    new_frame = frame.moved(-delta.x() / pu, delta.y() / pu)
                    self._apply_frame(new_frame)
                self._moved = True
            else:
                delta = e.globalPosition().toPoint() - self._press_pos
                self._moved = self._moved or delta.manhattanLength() > 5
                self.move(self._origin + delta)
            # Feed cursor to Live2D eye tracking
            if self._use_live2d and not self._panning:
                local = e.position()
                self.face.feed_cursor(local.x() - self.face.x(),
                                      local.y() - self.face.y())
        elif e.buttons() == Qt.NoButton:
            # Feature 15: the margin next to the model square is a resize
            # handle - show the resize cursor when the pointer is on it.
            if self._resize_edge is None:
                edge = self._resize_edge_at(e)
                if edge is not None:
                    self.setCursor(Qt.SizeHorCursor)
                elif self._touch_hover_zone is None:
                    self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        # Feature 15: end a free-form resize.
        if self._resize_edge is not None:
            self._resize_edge = None
            self.setCursor(Qt.ArrowCursor)
            self.save_position()
            return
        # Feature 13 Stage A: dispatch classified outcomes BEFORE the quick
        # click. poke/pat are ours; talk falls through to the original
        # face-square + hit-test logic unchanged.
        if self._touch_release(e):
            return
        self._panning = False
        if self._moved:
            # Persist position immediately so a hard kill doesn't lose it
            self.save_position()
            return
        # Only trigger voice turn when clicking the face sprite, not the bubble area
        local_y = e.position().toPoint().y()
        local_x = e.position().toPoint().x()
        face_top = self._face_top
        face_bot = self._face_top + self._face_height_px
        if face_top <= local_y <= face_bot:
            # Live2D hit test (checks transparent areas)
            if self._use_live2d:
                lx = local_x - self.face.x()
                ly = local_y - self.face.y()
                if self.face.hit_test(lx, ly):
                    self.on_click()
            else:
                self.on_click()

    def wheelEvent(self, e: QWheelEvent) -> None:
        """Ctrl+wheel → resize face.  Alt+wheel → zoom model."""
        delta_y = e.angleDelta().y()
        if delta_y == 0:
            return
        mods = e.modifiers()
        step = 1 if delta_y > 0 else -1

        if mods & Qt.ControlModifier:
            # Resize the face square CONTINUOUSLY (2% per notch - no 5%
            # quantisation, any pixel size inside the limits).  The model's
            # frame is kept, so the same part of the model stays visible and
            # simply gets more pixels.
            new_size = int(round(self.face_size * (1.0 + step * 0.02)))
            new_size = max(self.face_size_min, min(self.face_size_max, new_size))
            if new_size != self.face_size:
                self.face_size = new_size
                self._apply_layout()
                self._save_state_debounced()
            e.accept()

        elif mods & Qt.AltModifier:
            # Zoom the model: the FRAME grows/shrinks about its centre.
            if self._use_live2d:
                frame = self._frame
                if frame is None:
                    frame = self.face.visible_frame(float(self._face_size),
                                              float(self._face_height_px))
                if frame is not None:
                    factor = 1.05 if step > 0 else 1.0 / 1.05
                    self._apply_frame(frame.zoomed(factor))
            e.accept()

        else:
            e.ignore()

    # ── Feature 15: the model frame ─────────────────────────────────────────

    def _init_frame(self) -> None:
        """Adopt the configured model frame (or derive it from the legacy
        scale/offset the first time) and make the square show it."""
        if not self._use_live2d or self.face is None:
            return
        if self._frame is not None:
            # already known - just make sure the square shows it
            self.face.apply_frame(self._frame, float(self._face_size),
                                    float(self._face_height_px))
            return
        frame = Frame.from_dict(_live2d_cfg.get("face", {}).get("frame"))
        if frame is None:
            # First run (or a config without a frame): read the frame the
            # runtime is currently showing, then persist it as the new truth.
            frame = self.face.visible_frame(float(self._face_size),
                                              float(self._face_height_px))
            if frame is None:
                # The GL canvas has not rendered yet -> the runtime cannot be
                # measured.  Try again shortly (see _schedule_frame_retry).
                self._schedule_frame_retry()
                return
            self._frame = frame
            self._save_frame_debounced()
            if _BODY_LOG:
                logger.info("[frame] derived from legacy framing: %s",
                            frame.as_dict())
            return
        self._frame = frame
        self.face.apply_frame(frame, float(self._face_size),
                                float(self._face_height_px))
        self._sync_model_from_frame()

    def _schedule_frame_retry(self, attempt: int = 0) -> None:
        """Retry the frame measurement until the runtime can be read (the GL
        canvas needs one rendered frame before its pixel size is known)."""
        if attempt > 20 or self._frame is not None:
            return

        def _retry():
            if self._frame is not None:
                return
            self._init_frame()
            if self._frame is None:
                self._schedule_frame_retry(attempt + 1)

        QTimer.singleShot(400, _retry)

    def _sync_model_from_frame(self) -> None:
        """Keep the legacy scale/offset values in sync with the frame (they
        are still what the widget's SetScale/SetOffset need)."""
        if self.face is None:
            return
        self._model_scale = float(self.face.get_model_scale())
        self._model_offset_x, self._model_offset_y = \
            self.face.get_model_offset()

    def _apply_frame(self, frame: Frame, save: bool = True) -> None:
        """Make the face square show `frame` and remember it."""
        if frame is None or self.face is None or not self._use_live2d:
            return
        if self.face.apply_frame(frame, float(self._face_size),
                                float(self._face_height_px)):
            self._frame = frame
            self._sync_model_from_frame()
            if save:
                self._save_frame_debounced()

    def _save_frame_debounced(self) -> None:
        """Persist the frame into config/live2d.json (debounced)."""
        if self._frame is None:
            return
        timer = getattr(self, "_frame_save_timer", None)
        if timer is None:
            timer = QTimer(self, singleShot=True, interval=800,
                           timeout=self._save_frame_now)
            self._frame_save_timer = timer
        timer.start()

    def _save_frame_now(self) -> None:
        """Write the frame into config/live2d.json, keeping every other key."""
        if self._frame is None:
            return
        try:
            raw = {}
            if LIVE2D_CFG_FILE.exists():
                raw = json.loads(LIVE2D_CFG_FILE.read_text(encoding="utf-8"))
            face = raw.get("face") if isinstance(raw.get("face"), dict) else {}
            face["frame"] = self._frame.as_dict()
            # NOTE: the legacy scale/offset are deliberately NOT overwritten -
            # they stay the "default framing" that "Reset model frame" restores.
            raw["face"] = face
            LIVE2D_CFG_FILE.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
            if _BODY_LOG:
                logger.info("[frame] saved: %s", self._frame.as_dict())
        except Exception as e:
            logger.warning("could not save the model frame: %s", e)

    def _open_frame_editor(self) -> None:
        """Right-click menu: open the model-frame editor (the preview tool)."""
        import subprocess
        tool = ROOT / "tools" / "live2d_preview.py"
        try:
            subprocess.Popen([sys.executable, str(tool)])
        except Exception as e:
            logger.warning("could not start the frame editor: %s", e)

    # ── Feature 13 Stage A: touch-reaction plumbing ──────────────────────────

    def _touch_framing(self, cfg):
        """Face square + model frame together (exact, no calibration)."""
        frame = self._frame
        if frame is None and self._use_live2d and self.face is not None:
            frame = self.face.visible_frame(float(self._face_size),
                                              float(self._face_height_px))
        if frame is None:
            return None
        return Framing(frame=frame,
                        width_px=float(self._face_size),
                        height_px=float(self._face_height_px))

    def _touch_face_pos(self, e):
        """Event position in face-widget-local px (or None if no face)."""
        if self.face is None:
            return None, None
        local = e.position()
        return (local.x() - self.face.x(), local.y() - self.face.y())

    def _touch_usable(self) -> bool:
        cfg = get_touch_config()
        return bool(cfg.enabled and self._use_live2d)

    def _touch_arm(self, e, ctrl: bool) -> None:
        if not self._touch_usable():
            return
        cfg = get_touch_config()
        fx, fy = self._touch_face_pos(e)
        if fx is None:
            return
        f = self._touch_framing(cfg)
        if f is None:
            return
        zone = touch_find_zone(fx, fy, cfg, f)
        self._touch.press(cfg, fx, fy, time.monotonic(), zone, ctrl)
        if self._touch.armed and zone == "head" and not ctrl:
            self._touch_pat_timer.start()
        if _BODY_LOG:
            logger.info("[touch] press armed zone=%s ctrl=%s at (%.0f, %.0f)",
                        zone, ctrl, fx, fy)

    def _touch_move(self, e) -> None:
        if not self._touch_usable():
            return
        cfg = get_touch_config()
        fx, fy = self._touch_face_pos(e)
        if fx is None:
            return
        f = self._touch_framing(cfg)
        if f is None:
            return
        if e.buttons() == Qt.NoButton:
            # Hover cursor: open hand over the head zone, arrow elsewhere.
            zone = touch_find_zone(fx, fy, cfg, f)
            if zone != self._touch_hover_zone:
                self._touch_hover_zone = zone
                self.setCursor(
                    Qt.OpenHandCursor if zone == "head" else Qt.ArrowCursor)
            return
        if not self._touch.armed:
            return
        oc = self._touch.move(cfg, fx, fy, time.monotonic())
        if oc is None:
            return
        if oc.kind == "drag":
            # Past cancel_px: hand the press back to the ordinary window drag
            # (that flow is already producing the moves) and stop the timer.
            self._moved = True
            self._touch_pat_timer.stop()
            if _BODY_LOG:
                logger.info("[touch] press became a normal window drag "
                            "(moved > %.0f px)", cfg.cancel_px)
        elif oc.kind == "pat_end":
            # A fired pat cut short by movement: credit the hold, not a drag.
            self._touch_pat_timer.stop()
            self._on_touch_outcome(oc)

    def _touch_release(self, e) -> bool:
        cfg = get_touch_config()
        if not (cfg.enabled and self._use_live2d):
            return False
        fx, fy = self._touch_face_pos(e)
        oc = self._touch.release(cfg, fx, fy, time.monotonic())
        self._touch_pat_timer.stop()
        if oc is None:
            return False                      # not armed: original path runs
        if oc.kind in ("talk", "drag"):
            # talk → the original click-to-talk logic runs as ever; drag →
            # _moved is already True so the transform/save path runs as ever.
            return False
        self._on_touch_outcome(oc)
        return True

    def _touch_poll(self) -> None:
        """Timer tick: fire a pat when hold_seconds elapsed without movement."""
        cfg = get_touch_config()
        oc = self._touch.poll(cfg, time.monotonic())
        if oc is not None:
            self._touch_pat_timer.stop()
            self._on_touch_outcome(oc)

    def _on_touch_outcome(self, oc: TouchOutcome) -> None:
        """Feature 13 Stage B: gate → escalate → react to a classified touch.

        Gates (touch_reactions.gate): DND disables the feature entirely; the
        mic recording state ignores every touch; while she is SPEAKING only a
        small startle may fire — never a reaction that touches her audio
        (speaker.stop() is never called from here).
        """
        companion = _companion_ref
        dnd = bool(getattr(companion.config, "proactive_dnd", False)) \
            if companion is not None else False
        speaking = bool(companion.speaker.is_playing()) \
            if companion is not None else False
        cfg = get_touch_config()
        now = time.monotonic()

        if oc.kind == "pat_end":
            # A long hold finished (she already reacted at its boundary):
            # credit only the portion past the fired amount.
            extra = max(0.0, oc.duration - self._touch_pat_fired_s)
            self._touch_sm.process(now, "pat_end", duration=extra,
                                   cfg=get_escalation())
            if _BODY_LOG:
                logger.info("[touch] pat held %.2f s (credited +%.2f)",
                            oc.duration, extra)
            return

        verdict = touch_gate(oc.kind, speaking, self._listening, dnd)
        if _BODY_LOG:
            logger.info("[touch] %s(%s) speaking=%s listening=%s dnd=%s -> %s",
                        oc.kind, oc.zone, speaking, self._listening, dnd, verdict)
        if verdict == "ignore":
            return
        if self.behavior_state is not None:
            self.behavior_state.interact()          # Phase 2b contact impulse

        duration = cfg.hold_seconds if oc.kind == "pat" else 0.0
        reaction = self._touch_sm.process(
            now, oc.kind, duration=duration,
            state=self.behavior_state, cfg=get_escalation())
        if oc.kind == "pat":
            self._touch_pat_fired_s = duration      # release credits the rest
        if reaction is None:
            return
        if verdict == "startle_only":
            reaction = touch_startle_only(reaction)

        reaction = touch_resolve(
            reaction, _touch_available_actions())

        if _BODY_LOG:
            logger.info("[touch] %s on %s -> reaction=%s gesture=%s "
                        "actions=%s val%+.2f aro%+.2f",
                        oc.kind, oc.zone, reaction.name, reaction.gesture,
                        reaction.actions, reaction.valence, reaction.arousal)

        b = self.behavior_state
        if b is not None:
            # Small impulses (same pattern set_preview_axes establishes).
            b.valence = max(-1.0, min(1.0, b.valence + reaction.valence))
            b.arousal = max(0.0, min(1.0, b.arousal + reaction.arousal))
        if reaction.actions:
            self._fire_touch_action_keys(reaction.actions)
        if reaction.gesture and self._use_live2d:
            self.face.play_touch_gesture(reaction.gesture)
        if verdict == "full":
            # Stage C: only a fully-gated (not speaking, not recording,
            # not DND) touch may ever produce a spoken line.
            self._maybe_speak_touch(reaction)

    def _maybe_speak_touch(self, reaction) -> None:
        """Feature 13 Stage C: a voiced touch reaction.

        Routed through ``Speaker.say()`` (whose TTSCache LRU keeps repeat
        lines cheap).  Rates are limited by ``VoiceGate`` (min 10 s between
        spoken reactions, hourly cap).  ``touch_llm_reaction`` is explicitly
        a NO-LLM feature: the pools are the only source, and even if the key
        is flipped on, no LLM call is made in this build.
        """
        companion = _companion_ref
        if companion is None or getattr(companion, "speaker", None) is None:
            return
        spoken = get_touch_spoken()
        if not spoken.enabled:
            return
        now = time.monotonic()
        if not self._touch_voice.allow(now, spoken):
            if _BODY_LOG:
                logger.info("[touch] voice rate-limited (10 s interval / "
                            "hourly cap) — silent %s", reaction.name)
            return
        closeness = _closeness_baseline()
        tier_name = touch_spoken_tier(closeness, spoken.warm_at, spoken.close_at)
        line = touch_pick_line(spoken, reaction.name, tier_name, random)
        if line is None:
            return
        mood = dict(spoken.moods).get(reaction.name, "idle")
        self._touch_voice.record(now)
        if _BODY_LOG:
            logger.info("[touch] speaks %s (tier %s, mood %s): %r",
                        reaction.name, tier_name, mood, line)
        try:
            companion.speaker.say(line, mood=mood)
        except Exception as e:
            logger.warning("[touch] spoken reaction failed: %s", e)

    def _fire_touch_action_keys(self, keys) -> None:
        """Push touch reaction body actions through the same tag pipeline the
        test menu uses (on_body → live2d apply).  Unknown/disabled keys are
        dropped silently — the reaction degrades gracefully."""
        if not (self._use_live2d and _BODY_CONTROL):
            return
        try:
            from body_tags import Tag
            available = set(body_actions().enabled_capped_actions().keys())
        except Exception:
            available = set()
        tags = []
        for key in keys:
            kind, _, name = key.partition(":")
            if available and key not in available:
                continue
            tags.append(Tag(kind=kind, name=name, raw=f"[touch:{key}]"))
        if tags:
            # (gen, "", tags): bubble untouched, tags only reach the model.
            self.on_body((None, "", tags))

    def _set_drag_physics_enabled(self, on: bool) -> None:
        """Feature 14: runtime switch for the drag-physics inertia overlay."""
        self._drag_physics_on = bool(on)
        if self._use_live2d and self.face is not None:
            setter = getattr(self.face, "set_drag_physics_enabled", None)
            if setter is not None:
                setter(self._drag_physics_on)
        if _BODY_LOG:
            logger.info("[physics] drag physics %s",
                        "enabled" if self._drag_physics_on else "disabled")

    def _toggle_touch_debug(self) -> None:
        """Right-click menu: show/hide the touch-zone debug overlay."""
        self._touch_debug = not self._touch_debug
        if self.face is None:
            return
        if self._touch_overlay is None:
            self._touch_overlay = TouchZoneOverlay(self, self.face)
            self._touch_overlay.setGeometry(0, 0, self._face_size, self._face_height_px)
            self._touch_overlay.raise_()
        self._touch_overlay.setVisible(self._touch_debug)

    # ── Context menu (right-click) ────────────────────────────────────────────

    def _show_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1740; color: #f2eaff; border: 1px solid #4a3f8a; "
            "border-radius: 8px; padding: 4px; font-size: 14px; }"
            "QMenu::item:selected { background: #3a2f7a; }"
        )
        menu.addAction("🎤 Talk", self.on_click)
        if _TEXT_INPUT_CFG and _TEXT_INPUT_CFG.enabled:
            menu.addAction(_TEXT_INPUT_CFG.menu_label, self.prompt_text_input)
        menu.addSeparator()

        # ── Scene1 gag animation ──────────────────────────────────────────────
        if self._use_live2d and "scene1" in self.face._motions:
            loops = int(_live2d_cfg.get("scene1_loops", 1))
            if self.face.motion_is_playing():
                menu.addAction("⏹  Stop animation", self.face.stop_motion)
            else:
                menu.addAction("✨ Play Scene1", lambda: self.face.play_motion("scene1", loops))
            menu.addSeparator()

        # ── Test body action (fire any enabled fx/look/gesture/wink directly) ──
        if (self._use_live2d and _BODY_CONTROL
                and hasattr(self.face, "apply_body_actions")):
            test_menu = menu.addMenu("🎭 Test body action")
            ba = body_actions()
            for key, cfg in ba.enabled_capped_actions().items():
                act = test_menu.addAction(self._body_action_label(key, cfg))
                act.triggered.connect(
                    lambda _, k=key, c=cfg: self._fire_body_action(k, c))
            if not test_menu.actions():
                test_menu.addAction("(no enabled actions)")
                test_menu.setEnabled(False)
            menu.addSeparator()

        # ── Face size submenu ─────────────────────────────────────────────────
        size_menu = menu.addMenu("📐 Face size")
        for label, val in [("Small (250)", 250), ("Medium (400)", 400),
                            ("Large (600)", 600), ("Huge (800)", 800)]:
            act = size_menu.addAction(label)
            act.triggered.connect(lambda _, v=val: self._set_face_size(v))
        size_menu.addSeparator()
        size_menu.addAction("✏️  Set size…", self._ask_face_size)
        if self._use_live2d:
            size_menu.addAction("↩  Reset framing", self._reset_framing)
            # Feature 15: the frame editor (window size + model frame).
            size_menu.addAction("🖼  Model frame…", self._open_frame_editor)
            size_menu.addAction("↩  Reset model frame", self._reset_frame)

        vol_menu = menu.addMenu("🔊 Volume")
        for label, val in [("25%", 0.25), ("50%", 0.5), ("75%", 0.75), ("100%", 1.0)]:
            act = vol_menu.addAction(label)
            act.triggered.connect(lambda _, v=val: _companion_ref.speaker.set_volume(v))

        spd_menu = menu.addMenu("⏩ Speed")
        for label, val in [("Slow (0.75×)", 0.75), ("Normal (1.0×)", 1.0), ("Fast (1.5×)", 1.5)]:
            act = spd_menu.addAction(label)
            act.triggered.connect(lambda _, v=val: _companion_ref.speaker.set_speed(v))

        # ── Phase 2e: behavior director on/off + liveliness presets ────────────
        if self._use_live2d:
            bd_act = menu.addAction("Behavior director: on/off")
            bd_act.setCheckable(True)
            bd_act.setChecked(self._behavior_director_user)
            bd_act.toggled.connect(self.set_behavior_director_enabled)

            liv_menu = menu.addMenu(f"⏲  Liveliness  ({self._liveliness:.2f})")
            presets = [("Still (0.00)", 0.0), ("Calm (0.40)", 0.4),
                       ("Normal (0.70)", 0.7), ("Lively (1.00)", 1.0)]
            group = QActionGroup(self)
            group.setExclusive(True)
            for label, val in presets:
                act = liv_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(abs(self._liveliness - val) < 0.001)
                act.triggered.connect(lambda _, v=val: self.set_liveliness(v))
                group.addAction(act)

            menu.addSeparator()
            zone_dbg = menu.addAction("🐾 Show touch zones (debug)")
            zone_dbg.setCheckable(True)
            zone_dbg.setChecked(self._touch_debug)
            zone_dbg.toggled.connect(self._toggle_touch_debug)

            # Feature 14: drag physics (inertia on the model's physics rig)
            dp_act = menu.addAction("🧣 Drag physics: on/off")
            dp_act.setCheckable(True)
            dp_act.setChecked(self._drag_physics_on)
            dp_act.toggled.connect(self._set_drag_physics_enabled)

        menu.addSeparator()
        menu.addAction("🔄 Reload personality", self._do_reload)
        menu.addAction("📊 Stats", self._do_stats)
        menu.addSeparator()
        menu.addAction("❌ Quit", QApplication.quit)

        menu.exec(global_pos)

    @staticmethod
    def _body_action_label(key: str, cfg: dict) -> str:
        """Human label for a body action menu entry (e.g. "fx:blush" → "Blush")."""
        kind, _, name = key.partition(":")
        icons = {"fx": "💢", "look": "👀", "gesture": "🤲", "wink": "😉"}
        icon = icons.get(kind, "•")
        pretty = name.replace("_", " ").capitalize()
        if kind == "wink" and name not in ("left", "right"):
            side = cfg.get("side", "")
            pretty = f"Wink ({side})" if side else "Wink"
        return f"{icon} {pretty}"

    def _fire_body_action(self, key: str, cfg: dict) -> None:
        """Fire a single body action directly (test menu) — no LLM needed.

        Builds a one-shot action list from ``cfg`` (already shaped by
        enabled_capped_actions) and pushes it through the same path the tags use.
        """
        if not (self._use_live2d and getattr(self.face, "_body_enabled", True)):
            return
        tags = []
        if cfg.get("kind") in ("fx", "look", "gesture"):
            from body_tags import Tag
            tags = [Tag(kind=cfg["kind"], name=cfg["name"], raw=f"[{key}]")]
        elif cfg.get("kind") == "wink":
            from body_tags import Tag
            side = cfg.get("side") or body_actions().wink_default_side()
            tags = [Tag(kind="wink", name=side, raw=f"[wink:{side}]")]
        if tags:
            if _BODY_LOG:
                logger.info("[body] test-menu firing: %s -> %s", key, tags)
            # on_body expects a (gen, text, tags) payload; text is empty so the
            # bubble is untouched and only the tags reach the model.
            self.on_body((None, "", tags))

    def _set_face_size(self, new_size: int) -> None:
        new_size = max(self.face_size_min, min(self.face_size_max, new_size))
        if new_size != self.face_size:
            self.face_size = new_size
            self._apply_layout()
            self._save_state_debounced()

    def _ask_face_size(self) -> None:
        """Type an exact face-square size (px)."""
        num, ok = QInputDialog.getInt(
            self, "Pet size", "Face square size (px):", int(self.face_size),
            int(self.face_size_min), int(self.face_size_max))
        if ok:
            self._resize_to(num)

    def _reset_framing(self) -> None:
        """Reset the framing.  Feature 15: the display is driven by the model
        FRAME, so the legacy scale/offset are reset first and the frame is
        re-derived from them."""
        cfg_face = _live2d_cfg.get("face", {})
        self._model_scale    = float(cfg_face.get("scale",    2.2))
        self._model_offset_x = float(cfg_face.get("offset_x", 0.0))
        self._model_offset_y = float(cfg_face.get("offset_y", 0.35))
        if self._use_live2d and self.face is not None:
            self.face.set_model_scale(self._model_scale)
            self.face.set_model_offset(self._model_offset_x,
                                       self._model_offset_y)
        self._reset_frame()
        self._save_state_debounced()

    def _reset_frame(self) -> None:
        """Feature 15: reset the model frame to the configured default
        framing (the legacy scale/offset in config/live2d.json)."""
        cfg_face = _live2d_cfg.get("face", {})
        if not (self._use_live2d and self.face is not None):
            return
        self.face.set_model_scale(float(cfg_face.get("scale", 2.2)))
        self.face.set_model_offset(float(cfg_face.get("offset_x", 0.0)),
                                   float(cfg_face.get("offset_y", 0.35)))
        frame = self.face.visible_frame(float(self._face_size),
                                              float(self._face_height_px))
        if frame is not None:
            self._frame = frame
            self._sync_model_from_frame()
            self._save_frame_debounced()
            if _BODY_LOG:
                logger.info("[frame] reset to defaults: %s", frame.as_dict())

    def _do_reload(self):
        try:
            _companion_ref.config.reload()
            _companion_ref.apply_config()   # Part 3-B: push new values into model+speaker
            # Feature 3: pick up edits to config/emotions.yaml without a restart.
            if getattr(self, "_use_live2d", False) and getattr(self, "face", None) is not None:
                self.face.reload_emotions()
            self.on_status("Personality reloaded!", False)
        except Exception as e:
            self.on_status(f"Reload failed: {e}", False)

    def _do_stats(self):
        c = _companion_ref
        score = c.memory.closeness_score()
        tier = c.memory.tier_name()
        tokens = c.model.get_token_stats()
        lines = [
            f"Closeness: {score} pts  ({tier})",
            f"Facts: {len(c.memory.get_facts())}",
            f"History: {len(c.history)} messages",
            f"Tokens: {tokens['prompt']} prompt / {tokens['completion']} completion",
            f"Backend: {'Cerebras' if c.config.use_cerebras else 'Ollama'} / {c.model.model_name}",
        ]
        report = getattr(c, "last_prompt_report", None)
        if report is not None:
            lines.append("")
            lines.extend(report.summary_lines())
        else:
            lines.append("Prompt: no turn measured yet")
        # Phase 4b: body-control statistics summary.
        if _BODY_CONTROL and _FACE_RENDERER == "live2d":
            s = _BODY_STATS.summary()
            lat = s["tag_to_audio_latency_ms"]
            body_lines = [
                "",
                f"Body tags: {s['replies_with_tags']} replies / {s['total_tags']} tags",
                f"  by kind: {s['tags_by_kind']}",
                f"  unknown dropped: {s['unknown_dropped']}  cap dropped: {s['cap_dropped']}",
                f"  tag->audio latency(ms): last={lat['last']:.0f} avg={lat['avg']:.0f} "
                f"min={lat['min']:.0f} max={lat['max']:.0f} (n={lat['samples']})",
            ]
            lines.extend(body_lines)
        QMessageBox.information(self, "Stats", "\n".join(lines))


# ── Module-level companion ref (needed by right-click menu lambdas) ───────────
_companion_ref: PetCompanion = None  # set in main()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global ART_DIR, _companion_ref

    # First-run: prompt for missing API keys / write .env (before anything loads
    # the SDKs, which read these from the environment).
    ensure_env_keys(ROOT)

    # MUST be called before QApplication to get alpha buffer on the GL surface
    configure_opengl_format()

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    print("Loading Whisper, voice, and model (this may take a few seconds)…")
    config = Config(
        config_dir=Path(args.config_dir) if args.config_dir else None,
        state_dir=Path(args.state_dir) if args.state_dir else ROOT / "state",
    )
    # Phase 2: runtime state lives in state/, not config/.  Keep the module-level
    # paths aligned with the selected state directory for Pet and thread storage.
    global STATE_FILE, RELATIONSHIP_FILE, _THREADS_STATE_PATH
    STATE_FILE = config.state_dir / "pet_state.json"
    RELATIONSHIP_FILE = config.state_dir / "relationship.json"
    _THREADS_STATE_PATH = config.state_dir / "threads_state.json"
    bus = Bus()
    companion = PetCompanion(config, bus)
    _companion_ref = companion

    # ── Restore persisted audio state (volume / mute) ─────────────────────────
    _restore_muted = False
    try:
        _saved = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
        if "volume" in _saved:
            companion.speaker.set_volume(float(_saved["volume"]))
        _restore_muted = bool(_saved.get("muted"))
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not restore audio state: {e}")

    name = re.sub(r"\W+", "_", config.persona["name"].lower())
    # item 6: anchor to ROOT, not CWD
    ART_DIR = Path(args.art) if args.art else ROOT / "characters" / name
    _ui_art.configure(art_dir=ART_DIR)

    pet = Pet(on_click=companion.start_turn)

    # Phase 3b: give the companion read-only access to Pet's immutable per-tick
    # behavior snapshot (never the live BehaviorState).  Ordered after Pet is
    # built so pet._state_snapshot exists; reads happen on the worker thread.
    companion._state_getter = lambda: getattr(pet, "_state_snapshot", None)

    # Part 3-D: start wake-word thread if enabled
    wake_thread = None
    if companion.wake_detector is not None:
        wake_thread = WakeWordThread(companion)
        wake_thread.start()

    # Wire all signals → GUI slots
    bus.start.connect(pet.on_start)
    bus.text.connect(pet.on_text)
    bus.mood.connect(pet.on_mood)
    bus.end.connect(pet.on_end)
    bus.status.connect(pet.on_status)
    bus.vision.connect(pet.on_vision_flash)
    bus.voice_issue.connect(pet.on_voice_issue)
    bus.typing.connect(pet.on_typing)
    bus.proact.connect(pet.on_proactive)
    bus.listening.connect(pet.on_listening)
    # Feature 8B: the text-input hotkey fires on the keyboard thread — the bus
    # marshals it into the GUI thread before any widget is touched.
    bus.textinput.connect(pet.prompt_text_input)

    # Body control: each sentence's clean text + inline tags travel with it in
    # the Speaker queues and are emitted when that sentence's audio actually
    # STARTS (playback thread) → bus → GUI slot.  The bubble text is revealed at
    # audio start, and the tags drive the Live2D model.  The gen check in Speaker
    # discards interrupted generations.
    companion.speaker.on_sentence_start = (
        lambda gen, text, mood, tags, envelope=None, frame_ms=None, t0=None:
            bus.body.emit((gen, text, tags, envelope, frame_ms, t0))
    )
    bus.body.connect(pet.on_body)

    pet.restore_position(app.primaryScreen())
    pet.show()

    # ── Proactive idle timer ──────────────────────────────────────────────────
    proactive = ProactiveTimer(companion, config.proactive_idle_minutes)

    # Reset proactive timer after each completed turn
    def _on_turn_end():
        proactive.reset()

    bus.end.connect(_on_turn_end)
    proactive.reset()  # start counting from launch

    # ── System tray ──────────────────────────────────────────────────────────
    tray = QSystemTrayIcon(QIcon(load_pixmap("idle", False, pet.face_size)), app)

    # Show tray balloon for proactive messages
    def _tray_proact(msg: str):
        tray.showMessage(
            config.persona["name"],
            msg,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    bus.proact.connect(_tray_proact)

    tray_menu = QMenu()
    tray_menu.addAction("Show / Hide", lambda: pet.setVisible(not pet.isVisible()))
    if _TEXT_INPUT_CFG and _TEXT_INPUT_CFG.enabled:
        tray_menu.addAction(_TEXT_INPUT_CFG.menu_label, pet.prompt_text_input)
    mute_act = QAction("Mute voice", checkable=True)
    mute_act.toggled.connect(lambda on: setattr(companion.speaker, "enabled", not on))
    mute_act.setChecked(_restore_muted)
    if _restore_muted:
        companion.speaker.enabled = False
    tray_menu.addAction(mute_act)
    tray_menu.addSeparator()
    tray_menu.addAction("Quit", app.quit)
    tray.setContextMenu(tray_menu)
    tray.show()
    app._keep = (tray, tray_menu)

    # ── Hotkey ────────────────────────────────────────────────────────────────
    def _start_turn_hotkey():
        # Phase 2b: F9 is an attention jolt into the behavior state.  Runs on
        # the keyboard thread; behavior_state is pure (no Qt) so this is safe.
        if pet.behavior_state is not None:
            pet.behavior_state.hotkey()
        companion.start_turn()

    try:
        keyboard.add_hotkey(args.hotkey, _start_turn_hotkey)
    except Exception as e:
        pet.on_status(f"Hotkey '{args.hotkey}' unavailable: {e}", False)

    # Feature 8B: text-input hotkey (config/text_input.yaml, default ctrl+t).
    # Fires on the keyboard thread → bus signal → GUI thread, where it focuses
    # the inline bubble input (or opens the dialog in mode: window).
    _text_hk = (_TEXT_INPUT_CFG.hotkey
                if _TEXT_INPUT_CFG and _TEXT_INPUT_CFG.enabled else "")
    if _text_hk:
        try:
            keyboard.add_hotkey(_text_hk, bus.textinput.emit)
        except Exception as e:
            pet.on_status(f"Text input hotkey '{_text_hk}' unavailable: {e}", False)

    # Scene1 animation hotkey (configurable, default ctrl+shift+s)
    _scene1_hk = _live2d_cfg.get("scene1_hotkey", "")
    _scene1_loops = int(_live2d_cfg.get("scene1_loops", 1))
    if _scene1_hk and pet._use_live2d:
        try:
            def _play_scene1():
                if pet.face.motion_is_playing():
                    pet.face.stop_motion()
                else:
                    pet.face.play_motion("scene1", _scene1_loops)
            keyboard.add_hotkey(_scene1_hk, _play_scene1)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Scene1 hotkey '{_scene1_hk}' unavailable: {e}")

    # ── Feature 4: on-demand screen-vision hotkey (default ctrl+alt+s) ────────
    _vision_hk = (_VISION_CFG.hotkey if _VISION_CFG else "")
    _vision_on = bool(_VISION_CFG and _VISION_CFG.enabled and companion.vision is not None)
    if _vision_on and _vision_hk:
        def _capture_screen():
            # Runs on the keyboard thread.  Capture + arm only — no LLM call here;
            # the shot rides along with the user's next message.
            cap = companion.vision.capture()
            if cap is None:
                bus.vision.emit("Couldn't capture the screen.")
                return
            bus.vision.emit("👀 Got it — I'll look at your screen on your next message.")
        try:
            keyboard.add_hotkey(_vision_hk, _capture_screen)
            if not (_VISION_CFG.vision_model or "").strip():
                logging.getLogger(__name__).warning(
                    "[vision] enabled but no vision_model set in config/vision.yaml — "
                    "the configured model (%s) must accept images or the turn will fail.",
                    companion.model.model_name)
        except Exception as e:
            pet.on_status(f"Vision hotkey '{_vision_hk}' unavailable: {e}", False)

    # ── Feature 19: keyboard SHUSH (default ctrl+alt+x) ───────────────────────
    _shush_hk = str(_interruption_cfg().get("hotkey", "")) \
        if _interruption_cfg is not None else ""
    _shush_on = bool(_shush_hk and _interruption_cfg().get("enabled", True))
    if _shush_on:
        def _shush_her():
            # keyboard thread; the Speaker itself is thread-safe for stop().
            try:
                if companion.speaker.is_playing():
                    companion.speaker.stop()
                    logger.info("[interrupt] keyboard shush")
            except Exception:
                pass
        try:
            keyboard.add_hotkey(_shush_hk, _shush_her)
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"Shush hotkey '{_shush_hk}' unavailable: {e}")

    # Startup check: warn about any duplicated hotkeys across the app.
    from vision.screen_capture import find_hotkey_collisions
    _hk_map = {
        "Turn": args.hotkey,
        "Scene1": _scene1_hk if pet._use_live2d else "",
        "Screen vision": _vision_hk if _vision_on else "",
        "Text input": _text_hk,
        "Shush": _shush_hk if _shush_on else "",
    }
    for _a, _b, _hk in find_hotkey_collisions(_hk_map):
        _msg = f"Hotkey conflict: '{_hk}' is used by both {_a} and {_b}."
        logging.getLogger(__name__).warning("[hotkeys] %s", _msg)
        pet.on_status(_msg, False)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    def on_quit():
        proactive.stop()
        if wake_thread:
            wake_thread.stop()
        pet.save_position()
        # Persist volume / mute alongside pet geometry
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            state["volume"] = round(companion.speaker.volume, 2)
            state["muted"] = not companion.speaker.enabled
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except Exception:
            pass
        keyboard.unhook_all()
        companion.speaker.stop()
        # Dispose Live2D before the GL context is destroyed
        if pet._use_live2d:
            try:
                pet.face.dispose()
            except Exception:
                pass
        companion.shutdown()

    app.aboutToQuit.connect(on_quit)

    # ── Greeting ─────────────────────────────────────────────────────────────
    greeting = config.pick_greeting()
    if greeting:
        def _greet():
            pet.on_start()
            pet.on_text(greeting)
            pet.on_end()
            companion.speaker.say(greeting)
            companion.memory.log_turn("assistant", greeting)
            proactive.reset()

        QTimer.singleShot(500, _greet)

    print(
        f"{config.persona['name']} is ready.  "
        f"Hotkey: {args.hotkey}  |  left-click face  |  right-click for menu  |  tray to quit."
    )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
