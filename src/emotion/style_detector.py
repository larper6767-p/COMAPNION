"""Feature 6: emotion-style detection.

Reads the user's last message and turns it into a short "scene mood" block that
is appended to the system prompt.  This is the Python port of the original
Risu-style JS hook, adapted for this app:

* the app has ONE system prompt, so the personality + scenario text from the
  original is merged into a single block;
* it is framed as *the user's* tone ("calibrate, don't mirror") so it cannot
  override Eve's identity;
* a \"Boundary\" match produces explicit stop/give-space guidance;
* matching uses word boundaries (the original's substring scan matched \"mad\"
  inside \"made\", \"sad\" inside \"saddle\", etc.).

Pure stdlib + JSON.  No Qt, no LLM, no network.  Data lives in
``config/emotion_styles.json``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "emotion_styles.json"

# Normalisation removes apostrophes, so negators are stored in their
# apostrophe-free form ("don't" -> "dont").
_NEGATORS: FrozenSet[str] = frozenset({
    "not", "never", "no", "dont", "doesnt", "didnt", "isnt", "wasnt",
    "arent", "werent", "cant", "cannot", "couldnt", "shouldnt", "wouldnt",
    "hardly", "barely", "without", "least",
})

_APOSTROPHE = re.compile(r"['\u2019]")
_NON_WORD = re.compile(r"[^a-z0-9\s]")


# ── data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Style:
    category: str
    keywords: Tuple[str, ...]
    personality: str
    scenario: str
    priority: int
    alt: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    category: str
    personality: str
    scenario: str
    priority: int


@dataclass(frozen=True)
class StyleConfig:
    enabled: bool = False
    max_styles: int = 3
    append_neutral: bool = False
    safety: Tuple[Style, ...] = ()
    hybrids: Tuple[Style, ...] = ()
    emotions: Tuple[Style, ...] = ()
    antonyms: Dict[str, FrozenSet[str]] = field(default_factory=dict)
    idioms: Tuple[Tuple[str, str], ...] = ()


# ── normalisation / matching ──────────────────────────────────────────────────

def normalize(text: Optional[str]) -> str:
    """Lowercase, strip punctuation/apostrophes, collapse spaces.

    The result is padded with one space on each side so a keyword can be
    matched as ``\" keyword \"`` — a cheap, reliable word boundary that also
    handles multi-word phrases.
    """
    t = (text or "").lower().replace("\u2019", "'")
    t = _APOSTROPHE.sub("", t)
    t = _NON_WORD.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return f" {t} " if t else ""


def _needle(keyword: str) -> str:
    return f" {keyword} "


def _is_negated(norm: str, idx: int) -> bool:
    """True when a negator sits within three tokens before ``idx``."""
    before = norm[:idx].strip().split()
    return any(w in _NEGATORS for w in before[-3:])


def _find_all(norm: str, keyword: str) -> Iterable[int]:
    needle = _needle(keyword)
    start = 0
    while True:
        i = norm.find(needle, start)
        if i < 0:
            return
        yield i
        start = i + len(needle) - 1


def _keyword_hit(norm: str, keyword: str, *, allow_negated: bool = False) -> bool:
    for idx in _find_all(norm, keyword):
        if allow_negated or not _is_negated(norm, idx):
            return True
    return False


def _match_style(style: Style, norm: str, forced: FrozenSet[str], *,
                 require_all: bool = False) -> bool:
    allow = style.category in forced
    # alt phrases are OR'd
    for alt in style.alt:
        for idx in _find_all(norm, alt):
            if allow or not _is_negated(norm, idx):
                return True
    if not style.keywords:
        return False
    # Hybrids require every keyword (AND); everything else is OR.
    if require_all and len(style.keywords) > 1:
        return all(_keyword_hit(norm, kw, allow_negated=allow) for kw in style.keywords)
    return any(_keyword_hit(norm, kw, allow_negated=allow) for kw in style.keywords)


def _antonym_pairs(cfg: StyleConfig) -> Dict[str, FrozenSet[str]]:
    pairs: Dict[str, set] = {k: set(v) for k, v in cfg.antonyms.items()}
    for a, others in list(pairs.items()):
        for b in others:
            pairs.setdefault(b, set()).add(a)
    return {k: frozenset(v) for k, v in pairs.items()}


def _select(matches: Sequence[Match], cfg: StyleConfig) -> List[Match]:
    ant = _antonym_pairs(cfg)
    ordered = sorted(matches, key=lambda m: m.priority, reverse=True)
    out: List[Match] = []
    seen = set()
    for m in ordered:
        if m.category in seen:
            continue
        if any(m.category in ant.get(o.category, ()) for o in out):
            continue
        out.append(m)
        seen.add(m.category)
        if len(out) >= max(1, cfg.max_styles):
            break
    return out


def detect(text: Optional[str], cfg: Optional[StyleConfig] = None) -> List[Match]:
    """Return up to ``max_styles`` mood matches (SAFETY → HYBRID → PRIMARY)."""
    cfg = cfg or get_config()
    if not cfg.enabled:
        return []

    norm = normalize(text)
    if not norm:
        return []

    forced: set = set()
    for phrase, category in cfg.idioms:
        if _needle(phrase) in norm:
            forced.add(category)
    forced_f = frozenset(forced)
    by_cat = {s.category: s for s in (*cfg.safety, *cfg.hybrids, *cfg.emotions)}

    def forced_matches(exclude: set) -> List[Match]:
        out = []
        for cat in forced:
            s = by_cat.get(cat)
            if s is not None and cat not in exclude:
                out.append(Match(s.category, s.personality, s.scenario, s.priority))
        return out

    # 1. Safety always wins, and only the boundary is reported.
    for style in cfg.safety:
        if _match_style(style, norm, forced_f):
            return [Match(style.category, style.personality, style.scenario, style.priority)]

    # 2. Hybrids (if any match, primaries are not considered — same as the
    #    original script).
    hybrid_hits = [
        Match(s.category, s.personality, s.scenario, s.priority)
        for s in cfg.hybrids
        if _match_style(s, norm, forced_f, require_all=True)
    ]
    if hybrid_hits:
        return _select(hybrid_hits + forced_matches({h.category for h in hybrid_hits}), cfg)

    # 3. Primary emotions.
    primary = [
        Match(s.category, s.personality, s.scenario, s.priority)
        for s in cfg.emotions
        if _match_style(s, norm, forced_f)
    ]
    primary += forced_matches({m.category for m in primary})
    return _select(primary, cfg)


# ── output ────────────────────────────────────────────────────────────────────

def build_scene_block(text: Optional[str], cfg: Optional[StyleConfig] = None,
                      matches: Optional[Sequence[Match]] = None) -> str:
    """Return the prompt block for ``text`` (or ``\"\"`` when nothing applies).

    ``matches`` may be supplied when the caller already ran :func:`detect`,
    so a single pass can serve both the block and a caller-side decision
    (Feature 7 needs to know whether a boundary was hit).
    """
    cfg = cfg or get_config()
    if not cfg.enabled:
        return ""
    if matches is None:
        matches = detect(text, cfg)
    matches = list(matches)
    if not matches:
        if not cfg.append_neutral:
            return ""
        return (
            "## Scene mood (do not mention this analysis)\n"
            "The mood of the scene is:\n"
            "#1 Neutral: the user's tone is steady. No adjustment needed."
        )

    lines = [
        "## Scene mood (detected from the user's last message — do not mention this analysis)",
        "The mood of the scene is:",
    ]
    for i, m in enumerate(matches, 1):
        lines.append(f"#{i} {m.category}: {m.personality}. {m.scenario}")

    if any(m.category == "Boundary" for m in matches):
        lines.append(
            "The user has asked to stop or step back. Halt the current thread, "
            "acknowledge plainly, give them space, and do not push."
        )
    else:
        lines.append(
            "Let this colour how you respond. Do not mirror it mechanically, "
            "and do not let it override who you are."
        )
    return "\n".join(lines)


# ── config loading (cached, hot-reloads on file change) ───────────────────────

_cached: Optional[StyleConfig] = None
_cached_path: Optional[Path] = None
_cached_mtime: float = -1.0


def _style_from(raw: dict, *, allow_alt: bool) -> Optional[Style]:
    try:
        category = str(raw["category"]).strip()
        keywords = tuple(normalize(k).strip() for k in raw.get("keywords", []) if str(k).strip())
        personality = str(raw["personality"]).strip()
        scenario = str(raw["scenario"]).strip()
        priority = int(raw.get("priority", 4))
    except (KeyError, TypeError, ValueError):
        return None
    if not category or not keywords or not personality:
        return None
    alt: Tuple[str, ...] = ()
    if allow_alt:
        alt = tuple(normalize(a).strip() for a in raw.get("alt", []) if str(a).strip())
    return Style(category, keywords, personality, scenario, priority, alt)


def _load_styles(raw: dict, key: str, *, allow_alt: bool) -> Tuple[Style, ...]:
    out: List[Style] = []
    for item in raw.get(key, []) or []:
        if isinstance(item, dict):
            s = _style_from(item, allow_alt=allow_alt)
            if s is not None:
                out.append(s)
    return tuple(out)


def load_config(path: Optional[Path] = None) -> StyleConfig:
    """Load ``config/emotion_styles.json``.  A bad/missing file disables the feature."""
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        logger.info("emotion_styles.json not found; scene-mood detection disabled")
        return StyleConfig()
    except Exception as e:
        logger.warning("emotion_styles.json unreadable; scene-mood detection disabled: %s", e)
        return StyleConfig()

    idioms = []
    for item in raw.get("idioms", []) or []:
        if isinstance(item, dict) and item.get("phrase") and item.get("category"):
            idioms.append((normalize(item["phrase"]).strip(), str(item["category"])))

    try:
        max_styles = max(1, int(raw.get("max_styles", 3)))
    except (TypeError, ValueError):
        max_styles = 3

    return StyleConfig(
        enabled=bool(raw.get("enabled", False)),
        max_styles=max_styles,
        append_neutral=bool(raw.get("append_neutral", False)),
        safety=_load_styles(raw, "safety", allow_alt=False),
        hybrids=_load_styles(raw, "hybrids", allow_alt=True),
        emotions=_load_styles(raw, "emotions", allow_alt=True),
        antonyms={
            str(k): frozenset(str(v) for v in (vals or []))
            for k, vals in (raw.get("antonyms", {}) or {}).items()
        },
        idioms=tuple(idioms),
    )


def get_config(path: Optional[Path] = None) -> StyleConfig:
    """Cached config; automatically reloads when the file's mtime changes."""
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
    """Clear the cache (used by tests and by config hot-reload)."""
    global _cached, _cached_path, _cached_mtime
    _cached = None
    _cached_path = None
    _cached_mtime = -1.0


__all__ = [
    "Style", "Match", "StyleConfig",
    "normalize", "detect", "build_scene_block",
    "load_config", "get_config", "reload_config",
    "DEFAULT_CONFIG_PATH",
]
