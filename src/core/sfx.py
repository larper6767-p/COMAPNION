"""Feature 12: real SFX audio.

Feature 7 lets her pepper speech with onomatopoeia and the four symbols
``~ ♥ ♡ ♪``: the bubble shows them and ``core.audio.clean_for_speech``
strips them from the TTS text, so until now they were silent dead weight.

This module plays REAL sound clips at the moment such a sentence's audio
starts (wired into ``core.audio.Speaker``):

Feature 12c (``splice: true``) goes further: the mapped word/symbol is
REMOVED from the spoken text and its cue plays at that exact position, so
"mwah" is never read aloud as a word — the sound replaces it (see
``core/sound_splice.py``).

* ``config/sfx.yaml`` maps each marker/symbol to one or more **clip stems**;
  the player looks up files named ``<stem><digits>.<ext>`` (``wav`` ``ogg``
  ``mp3``) in the configured folder, e.g. ``breath_1.wav``, ``breath2.ogg``.
* several stems per category → one is picked at random; a per-category
  cooldown and a global gap keep clips from machine-gunning.
* pygame is imported lazily and every failure path is a silent no-op, so a
  bad config, a missing clip, or headless runs can never break the TTS
  pipeline.

No Qt, no network, no disk writes.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "sfx.yaml"
CLIP_EXTENSIONS = (".wav", ".ogg", ".mp3", ".flac")

DEFAULTS: Dict[str, object] = {
    "enabled": True,
    "folder": "assets/sfx",
    "volume": 0.5,
    "cooldown": 2.0,        # seconds between two plays of the SAME category
    "gap": 0.4,             # min seconds between ANY two plays
    "max_per_sentence": 4,  # never turn one sentence into a machine-gun
    "splice": True,         # Feature 12c: replace the spoken word with its cue
    "cue_beat_s": 0.25,     # beat after a spliced cue before the next words
}

# Built-in mapping (also shipped in config/sfx.yaml — mirrors the Feature 7
# system prompt's symbol semantics).
DEFAULT_TRIGGERS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("♥", ("heart",)),      # deep pleasure / affection
    ("♡", ("tender",)),     # soft, tender warmth
    ("♪", ("giggle",)),     # playful, sing-song
    ("~", ("breath",)),     # blissful, elongated sound
)

DEFAULT_WORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("mwah|mmwah|smooch|\\bchu\\b", ("kiss",)),
    ("slurp|schlick|schlrp|glrk|glurk", ("suck",)),
    ("gasp|huff", ("gasp",)),
    # Feature 12d: the rest of the sound-script library — moans, whimpers,
    # sighs, laughs, purrs, heartbeats, sniffs and gulps.
    ("\\bm{3,}h?\\b|\\bmmh+\\b|\\bm{2,}p+[fh]\\b", ("moan",)),
    ("\\bhn+ng+\\b|\\bnn+gh?\\b|\\bhnn+h\\b|\\bnn+h\\b", ("whimper",)),
    ("\\bsighs?\\b|\\bhaa+h+\\b|\\baa+h+\\b", ("sigh",)),
    ("\\b(?:a?ha){2,}h?\\b|\\b(?:he){2,}h?\\b|\\bteehee+\\b", ("laugh",)),
    ("\\bprrr+\\b|\\bmrrr+\\b|\\bmrrp+\\b", ("purr",)),
    ("\\bba[-\\s]?dum+\\b|\\bthump[-\\s]?thump\\b", ("heartbeat",)),
    ("\\bsniff+\\b|\\bsnff\\b", ("sniff",)),
    ("\\bgulp\\b|\\bglp+\\b", ("gulp",)),
)


@dataclass(frozen=True)
class SfxConfig:
    enabled: bool = bool(DEFAULTS["enabled"])
    folder: str = str(DEFAULTS["folder"])
    volume: float = float(DEFAULTS["volume"])
    cooldown: float = float(DEFAULTS["cooldown"])
    gap: float = float(DEFAULTS["gap"])
    max_per_sentence: int = int(DEFAULTS["max_per_sentence"])
    # Feature 12c: strip the word from TTS and play its cue at that spot.
    splice: bool = bool(DEFAULTS["splice"])
    cue_beat_s: float = float(DEFAULTS["cue_beat_s"])
    # marker/symbol (or regex) -> candidate clip stems
    triggers: Tuple[Tuple[str, Tuple[str, ...]], ...] = DEFAULT_TRIGGERS
    words: Tuple[Tuple[Pattern[str], Tuple[str, ...]], ...] = ()


def _stems(raw) -> Tuple[str, ...]:
    """Normalise a mapping value (str or list) into a tuple of stem names."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return ()
    out = []
    for item in raw:
        s = str(item).strip()
        if s:
            out.append(s)
    return tuple(out)


def _compile_words(raw) -> Tuple[Tuple[Pattern[str], Tuple[str, ...]], ...]:
    """Compile ``{regex: stems}`` (yaml) and ``(regex, stems)`` pair lists."""
    try:
        pairs = list(raw.items() if isinstance(raw, dict) else raw)
    except Exception:
        return ()
    out: List[Tuple[Pattern[str], Tuple[str, ...]]] = []
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        p = str(pair[0]).strip()
        s = _stems(pair[1])
        if not p or not s:
            continue
        try:
            out.append((re.compile(p, re.IGNORECASE), s))
        except re.error as e:
            logger.warning("sfx.yaml: bad word pattern %r (%s)", p, e)
    return tuple(out)


def load_config(path: Optional[Path] = None) -> SfxConfig:
    """Load ``config/sfx.yaml`` over the built-in defaults.

    A missing/malformed file yields the defaults — a bad config can never
    break the TTS pipeline.  Non-finite numbers are clamped.
    """
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    raw: object = None
    try:
        import yaml
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except Exception as e:
        logger.warning("sfx.yaml unreadable; SFX defaults used: %s", e)
        raw = None
    if not isinstance(raw, dict):
        raw = {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
        if v != v:                       # NaN guard
            return default
        return max(lo, min(hi, v))

    folder = str(raw.get("folder") or DEFAULTS["folder"]).strip() or str(DEFAULTS["folder"])
    try:
        max_per = int(raw.get("max_per_sentence", DEFAULTS["max_per_sentence"]))
    except (TypeError, ValueError):
        max_per = int(DEFAULTS["max_per_sentence"])
    max_per = max(1, min(12, max_per))

    triggers: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    if isinstance(raw.get("triggers"), dict):
        triggers = tuple(
            (str(marker), _stems(stems))
            for marker, stems in raw["triggers"].items()
            if str(marker).strip() and _stems(stems)
        )
    words = _compile_words(raw.get("words"))

    return SfxConfig(
        enabled=bool(raw.get("enabled", DEFAULTS["enabled"])),
        folder=folder,
        volume=num("volume", float(DEFAULTS["volume"]), 0.0, 1.0),
        cooldown=num("cooldown", float(DEFAULTS["cooldown"]), 0.0, 60.0),
        gap=num("gap", float(DEFAULTS["gap"]), 0.0, 10.0),
        max_per_sentence=max_per,
        splice=bool(raw.get("splice", DEFAULTS["splice"])),
        cue_beat_s=num("cue_beat_s", float(DEFAULTS["cue_beat_s"]), 0.0, 1.0),
        triggers=triggers or DEFAULT_TRIGGERS,
        words=words or _compile_words(DEFAULT_WORDS),
    )


# ── config cache (hot-reloads when the file changes) ─────────────────────────

_cached: Optional[SfxConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def get_config(path: Optional[Path] = None) -> SfxConfig:
    global _cached, _cached_path, _cached_mtime
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _cached is not None and p == _cached_path and mtime == _cached_mtime:
        return _cached
    _cached = load_config(p)
    _cached_path = p
    _cached_mtime = mtime
    return _cached


def reload_config() -> None:
    global _cached, _cached_path, _cached_mtime
    _cached = None
    _cached_path = None
    _cached_mtime = -1.0


# ── detection (pure) ─────────────────────────────────────────────────────────

def detect_sfx(
    text: Optional[str], cfg: Optional[SfxConfig] = None
) -> List[Tuple[str, Tuple[str, ...]]]:
    """Sound-script hits in her RAW text — call this BEFORE
    ``clean_for_speech`` strips the symbols.

    Returns ``(category, stems)`` pairs in the config's trigger order,
    deduped, capped at ``max_per_sentence``.  Empty list = nothing to play.
    """
    cfg = cfg if cfg is not None else get_config()
    if not cfg.enabled or not text:
        return []
    t = str(text)
    out: List[Tuple[str, Tuple[str, ...]]] = []
    seen = set()
    for marker, stems in cfg.triggers:
        if marker and marker in t and marker not in seen:
            seen.add(marker)
            out.append((marker, stems))
    for pat, stems in cfg.words:
        if pat.pattern not in seen and pat.search(t):
            seen.add(pat.pattern)
            out.append((pat.pattern, stems))
    return out[: cfg.max_per_sentence]


def clip_for(stem: str, files) -> Optional[str]:
    """Pure file-name matcher: files named ``<stem><optional _-digits><ext>``.

    ``breath`` matches ``breath.wav`` ``breath_1.wav`` ``breath2.ogg``
    ``BREATH_03.MP3`` but NOT ``suckle_1.wav``.  Case-insensitive; the FIRST
    match in the provided order wins (the player's scan is sorted).
    """
    stem_l = str(stem).strip().lower()
    if not stem_l:
        return None
    matches = []
    for f in files:
        name = f.lower()
        if not any(name.endswith(ext) for ext in CLIP_EXTENSIONS):
            continue
        base = name[: -4] if name.endswith((".wav", ".ogg", ".mp3")) else name[: -5]
        if not base.startswith(stem_l):
            continue
        rest = base[len(stem_l):]
        if rest and not (rest[0].isdigit() or rest[0] in "_-."):
            continue
        matches.append(f)
    return matches[0] if matches else None


# ── player ────────────────────────────────────────────────────────────────────

class SfxPlayer:
    """One ``pygame.mixer.Sound`` at a time, gated by config and cooldowns.

    Safe to call from the TTS playback thread.  Every failure path is a
    silent no-op: disabled config, pygame missing, mixer not initialised,
    folder missing, no clip for the stem, all mixer channels busy.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = config_path
        self._lock = threading.Lock()
        self._last_cat: Dict[str, float] = {}
        self._last_any: Optional[float] = None
        self._scan_cache: Optional[List[Path]] = None
        self._channels: list = []

    # ── cooldown gate (pure, injectable clock for tests) ─────────────────────
    def _allowed(self, cfg: SfxConfig, category: str, now: float) -> bool:
        last_cat = self._last_cat.get(category)
        if last_cat is not None and now - last_cat < cfg.cooldown:
            return False
        if self._last_any is not None and now - self._last_any < cfg.gap:
            return False
        return True

    def _scan(self, folder: str) -> List[Path]:
        """The clip folder, scanned once per player (add clips → restart)."""
        if self._scan_cache is not None:
            return self._scan_cache
        try:
            base = Path(folder)
            if not base.is_absolute():
                base = PROJECT_ROOT / base
            self._scan_cache = sorted(
                p for p in base.iterdir()
                if p.is_file() and p.suffix.lower() in CLIP_EXTENSIONS
            )
        except OSError:
            self._scan_cache = []
            logger.debug("[sfx] clip folder not found: %r", folder)
        return self._scan_cache

    def _resolve(self, stems: Tuple[str, ...]) -> Optional[Path]:
        pool = [f for s in stems for f in [self._find(s)] if f is not None]
        if not pool:
            return None
        return random.choice(pool)

    def _find(self, stem: str) -> Optional[Path]:
        cfg = get_config(self._config_path) if self._config_path else get_config()
        hit = clip_for(stem, [p.name for p in self._scan(cfg.folder)])
        if hit is None:
            return None
        base = Path(cfg.folder)
        if not base.is_absolute():
            base = PROJECT_ROOT / base
        return base / hit

    def _start(self, clip: Path, cfg: SfxConfig) -> bool:
        """Actually play one clip.  False = mixer absent/unavailable/busy."""
        try:
            import pygame                     # lazy: headless runs never pay
            if not pygame.mixer.get_init():
                logger.debug("[sfx] mixer not initialised; dropping clip")
                return False
            sound = pygame.mixer.Sound(str(clip))
            sound.set_volume(cfg.volume)
            channel = sound.play()
            if channel is None:
                return False                   # all channels busy
            with self._lock:
                self._channels.append(channel)
            logger.debug("[sfx] %s", clip.name)
            return True
        except Exception as e:
            logger.debug("[sfx] cannot play %s: %s", clip, e)
            return False

    def play(self, hits: List[Tuple[str, Tuple[str, ...]]],
             now: Optional[float] = None) -> bool:
        """Play AT MOST ONE clip for this sentence: the first hit that passes
        its cooldown and resolves to an existing clip file.

        Returns True when a clip actually started.  Silently False for the
        no-SFX paths — the TTS pipeline is never touched.
        """
        cfg = get_config(self._config_path) if self._config_path else get_config()
        if not hits or not cfg.enabled:
            return False
        now = time.monotonic() if now is None else now
        for category, stems in hits:
            with self._lock:
                allowed = self._allowed(cfg, category, now)
            if not allowed:
                continue
            clip = self._resolve(stems)
            if clip is None:
                continue
            if self._start(clip, cfg):
                with self._lock:
                    self._last_cat[category] = now
                    self._last_any = now
                return True
        return False

    def stop_all(self) -> None:
        """Fade nothing, stop everything: called on Speaker.stop() so an SFX
        clip never rings over an interruption."""
        with self._lock:
            channels, self._channels = list(self._channels), []
        for ch in channels:
            try:
                ch.stop()
            except Exception:
                pass

    def has_clip(self, stems: Tuple[str, ...]) -> bool:
        """True when at least one existing clip resolves for these stems.

        Feature 12c: the speech pipeline only strips a word from the TTS
        text when its cue can actually play — a missing file means the word
        is spoken normally instead of vanishing.
        """
        cfg = get_config(self._config_path) if self._config_path else get_config()
        if not cfg.enabled:
            return False
        try:
            return any(self._find(s) is not None for s in stems)
        except Exception:
            return False


__all__ = [
    "SfxConfig", "SfxPlayer",
    "load_config", "get_config", "reload_config",
    "detect_sfx", "clip_for",
    "DEFAULT_CONFIG_PATH", "DEFAULTS", "DEFAULT_TRIGGERS", "DEFAULT_WORDS",
]
