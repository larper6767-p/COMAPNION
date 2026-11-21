"""Prompt-size accounting and optional rolling-history budgeting.

Phase 1 (prompt budget/observability) makes each request measurable without
touching persona text.  The report counts characters and estimates tokens per
prompt section, records how many history messages were sent, and (after the
reply) can attach the provider's real prompt/completion usage.

When a hard budget is configured, only the OLDEST messages in the outgoing
copy are dropped.  The in-memory history is never changed, so summarisation,
episode logs, and the next turn still see the whole conversation.  Persona and
feature-owned prompt text is never truncated.

The estimator is deliberately dependency-free: English text is roughly four
characters per token.  Provider usage is logged separately so the ratio can be
checked and the heuristic calibrated later if it drifts.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "prompt_budget.yaml"

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "max_prompt_tokens": 12000,
    # Ollama is pinned to a 2048-token context in core/model.py.  The cloud
    # budget would be unsafe there, so local turns get their own lower cap.
    "ollama_max_prompt_tokens": 1600,
    "warn_ratio": 0.85,
    "min_history_messages": 2,
    "log_every_turn": True,
}

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("1", "true", "yes", "on"):
            return True
        if token in ("0", "false", "no", "off"):
            return False
    return default


def _as_int(value: Any, default: int, minimum: int = 0,
            maximum: int = 1_000_000) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, out))


def _as_float(value: Any, default: float, minimum: float = 0.0,
              maximum: float = 1.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, out))


def load_config(path: Optional[Path] = None,
                force: bool = False) -> Dict[str, Any]:
    """Load ``config/prompt_budget.yaml`` with safe defaults.

    A missing or malformed file means the shipped defaults (observability on,
    12k cloud / 1.6k local hard caps).  The mtime cache makes live edits cheap.
    """
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return dict(DEFAULTS)

    key = str(p)
    cached = _cache.get(key)
    if not force and cached is not None and cached[0] == mtime:
        return dict(cached[1])

    cfg = dict(DEFAULTS)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Prompt budget config unreadable (%s); using defaults", exc)
        raw = {}
    if isinstance(raw, dict):
        cfg["enabled"] = _as_bool(raw.get("enabled"), True)
        cfg["max_prompt_tokens"] = _as_int(
            raw.get("max_prompt_tokens"), DEFAULTS["max_prompt_tokens"],
            0, 1_000_000)
        cfg["ollama_max_prompt_tokens"] = _as_int(
            raw.get("ollama_max_prompt_tokens"),
            DEFAULTS["ollama_max_prompt_tokens"], 0, 1_000_000)
        cfg["warn_ratio"] = _as_float(
            raw.get("warn_ratio"), DEFAULTS["warn_ratio"], 0.0, 1.0)
        cfg["min_history_messages"] = _as_int(
            raw.get("min_history_messages"),
            DEFAULTS["min_history_messages"], 1, 200)
        cfg["log_every_turn"] = _as_bool(
            raw.get("log_every_turn"), True)

    _cache[key] = (mtime, dict(cfg))
    return cfg


def get_config(force: bool = False) -> Dict[str, Any]:
    return load_config(force=force)


def estimate_tokens(text: Any) -> int:
    """Dependency-free token estimate for English prompt text."""
    if text is None:
        return 0
    value = str(text)
    if not value.strip():
        return 0
    return max(1, int(math.ceil(len(value) / CHARS_PER_TOKEN)))


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, Mapping):
                if part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif "text" in part:
                    parts.append(str(part.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")


def _history_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        MESSAGE_OVERHEAD_TOKENS + estimate_tokens(_message_text(message))
        for message in messages
    )


def _section_rows(
    sections: Sequence[Tuple[str, str]],
    system_tokens: int,
) -> Tuple[Tuple[str, int], ...]:
    rows: List[Tuple[str, int]] = []
    covered = 0
    for name, text in sections or ():
        tokens = estimate_tokens(text)
        if tokens <= 0:
            continue
        rows.append((str(name), tokens))
        covered += tokens
    if not rows:
        return (("system", system_tokens),)
    if system_tokens > covered:
        rows.append(("separators", system_tokens - covered))
    return tuple(rows)


def effective_budget(cfg: Optional[Mapping[str, Any]] = None,
                     use_cerebras: bool = True) -> int:
    """Return the active hard cap in estimated tokens (0 = observe only)."""
    c = cfg if cfg is not None else DEFAULTS
    if not _as_bool(c.get("enabled", True), True):
        return 0
    key = "max_prompt_tokens" if use_cerebras else "ollama_max_prompt_tokens"
    default = int(DEFAULTS[key])
    return _as_int(c.get(key), default, 0, 1_000_000)


@dataclass(frozen=True)
class PromptReport:
    """Privacy-safe size report for one outgoing request."""

    system_tokens: int
    history_tokens: int
    total_tokens: int
    system_chars: int
    history_chars: int
    total_chars: int
    sections: Tuple[Tuple[str, int], ...]
    history_available: int
    history_window: int
    history_kept: int
    history_dropped: int
    max_history: int
    budget_tokens: int
    warn: bool
    over_budget: bool
    system_over_budget: bool
    trimmed: bool
    has_image: bool
    actual_prompt_tokens: int = 0
    actual_completion_tokens: int = 0
    actual_finish_reason: str = ""

    @property
    def estimate_ratio(self) -> float:
        if self.actual_prompt_tokens > 0 and self.total_tokens > 0:
            return self.actual_prompt_tokens / self.total_tokens
        return 0.0

    def section_dict(self) -> Dict[str, int]:
        return dict(self.sections)

    def section_text(self) -> str:
        return ",".join(f"{name}:{tokens}" for name, tokens in self.sections)

    def with_usage(self, prompt_tokens: int = 0,
                   completion_tokens: int = 0,
                   finish_reason: str = "") -> "PromptReport":
        return replace(
            self,
            actual_prompt_tokens=max(0, int(prompt_tokens or 0)),
            actual_completion_tokens=max(0, int(completion_tokens or 0)),
            actual_finish_reason=str(finish_reason or self.actual_finish_reason),
        )

    def plan_line(self) -> str:
        if self.budget_tokens > 0:
            percent = int(round(
                100.0 * self.total_tokens / self.budget_tokens))
            budget = f"{self.budget_tokens} pct={percent}"
        else:
            budget = "off"
        return (
            f"total={self.total_tokens} budget={budget} "
            f"sys={self.system_tokens} hist={self.history_tokens} "
            f"msgs={self.history_kept}/{self.history_window} "
            f"all={self.history_available} trimmed={self.history_dropped} "
            f"image={'yes' if self.has_image else 'no'} "
            f"sections={self.section_text()}"
        )

    def usage_line(self) -> str:
        if not (self.actual_prompt_tokens or self.actual_completion_tokens):
            return "usage=unavailable"
        ratio = (f"{self.estimate_ratio:.2f}"
                 if self.estimate_ratio > 0 else "n/a")
        finish = self.actual_finish_reason or "?"
        return (
            f"usage prompt={self.actual_prompt_tokens} "
            f"completion={self.actual_completion_tokens} finish={finish} "
            f"est={self.total_tokens} ratio={ratio}"
        )

    def summary_lines(self) -> List[str]:
        if self.budget_tokens > 0:
            percent = int(round(
                100.0 * self.total_tokens / self.budget_tokens))
            head = (f"Prompt: {self.total_tokens:,} est / "
                    f"{self.budget_tokens:,} budget ({percent}%)")
        else:
            head = f"Prompt: {self.total_tokens:,} est (budget off)"
        lines = [
            head,
            f"  system {self.system_tokens:,} | history "
            f"{self.history_tokens:,} | messages "
            f"{self.history_kept}/{self.history_window} | trimmed "
            f"{self.history_dropped}",
            "  sections: " + ", ".join(
                f"{name} {tokens:,}" for name, tokens in self.sections),
        ]
        if self.actual_prompt_tokens or self.actual_completion_tokens:
            finish = self.actual_finish_reason or "?"
            ratio = (f"{self.estimate_ratio:.2f}"
                     if self.estimate_ratio > 0 else "n/a")
            lines.append(
                f"  usage: {self.actual_prompt_tokens:,} prompt / "
                f"{self.actual_completion_tokens:,} completion "
                f"(finish={finish}, est ratio={ratio})")
        else:
            lines.append("  usage: not reported by provider")
        if self.system_over_budget:
            lines.append("  WARNING: system prompt alone is over budget")
        elif self.over_budget:
            lines.append("  WARNING: minimum history still exceeds budget")
        return lines


def plan_prompt(
    system_content: str,
    history: Sequence[Mapping[str, Any]],
    *,
    max_history: int = 30,
    cfg: Optional[Mapping[str, Any]] = None,
    use_cerebras: bool = True,
    sections: Sequence[Tuple[str, str]] = (),
    has_image: bool = False,
) -> Tuple[List[Dict[str, Any]], PromptReport]:
    """Select the outgoing history window and build a size report.

    ``history`` is not mutated.  The returned list contains the newest
    ``max_history`` messages, then drops the oldest of those until the
    estimated prompt fits the active budget.  At least
    ``min_history_messages`` newest messages are kept, and the system/persona
    text is never changed.
    """
    c = dict(DEFAULTS) if cfg is None else dict(cfg)
    full_history = list(history or [])
    window_size = _as_int(max_history, 30, 0, 1_000_000)
    window = full_history[-window_size:] if window_size else []
    selected = list(window)

    system_tokens = estimate_tokens(system_content)
    budget = effective_budget(c, use_cerebras)
    min_keep = min(
        _as_int(c.get("min_history_messages"),
                int(DEFAULTS["min_history_messages"]), 1, 200),
        len(selected),
    )

    if budget > 0:
        while (selected
               and len(selected) > min_keep
               and system_tokens + _history_tokens(selected) > budget):
            selected.pop(0)

    history_tokens = _history_tokens(selected)
    total_tokens = system_tokens + history_tokens
    history_chars = sum(len(_message_text(m)) for m in selected)
    system_chars = len(system_content or "")
    warn_ratio = _as_float(
        c.get("warn_ratio"), float(DEFAULTS["warn_ratio"]), 0.0, 1.0)
    over_budget = budget > 0 and total_tokens > budget
    system_over_budget = budget > 0 and system_tokens > budget

    report = PromptReport(
        system_tokens=system_tokens,
        history_tokens=history_tokens,
        total_tokens=total_tokens,
        system_chars=system_chars,
        history_chars=history_chars,
        total_chars=system_chars + history_chars,
        sections=_section_rows(sections, system_tokens),
        history_available=len(full_history),
        history_window=len(window),
        history_kept=len(selected),
        history_dropped=len(window) - len(selected),
        max_history=window_size,
        budget_tokens=budget,
        warn=budget > 0 and total_tokens >= budget * warn_ratio,
        over_budget=over_budget,
        system_over_budget=system_over_budget,
        trimmed=len(selected) < len(window),
        has_image=bool(has_image),
    )
    return selected, report


def log_plan(report: PromptReport,
             cfg: Optional[Mapping[str, Any]] = None,
             log: Optional[logging.Logger] = None) -> None:
    """Log a report without any prompt text."""
    log = log or logger
    c = cfg if cfg is not None else DEFAULTS
    if not _as_bool(c.get("enabled", True), True):
        return
    line = "[prompt] " + report.plan_line()
    if report.system_over_budget:
        log.warning("%s system-over-budget", line)
    elif report.over_budget:
        log.warning("%s over-budget", line)
    elif report.warn:
        log.warning("%s near-budget", line)
    elif _as_bool(c.get("log_every_turn", True), True):
        log.info(line)
    else:
        log.debug(line)


def log_usage(report: PromptReport,
              cfg: Optional[Mapping[str, Any]] = None,
              log: Optional[logging.Logger] = None) -> None:
    """Log the provider's actual usage when it was reported."""
    log = log or logger
    c = cfg if cfg is not None else DEFAULTS
    if not _as_bool(c.get("enabled", True), True):
        return
    line = "[prompt] " + report.usage_line()
    if report.actual_prompt_tokens or report.actual_completion_tokens:
        log.info(line)
    else:
        log.debug(line)


__all__ = [
    "CHARS_PER_TOKEN",
    "CONFIG_PATH",
    "DEFAULTS",
    "MESSAGE_OVERHEAD_TOKENS",
    "PromptReport",
    "effective_budget",
    "estimate_tokens",
    "get_config",
    "load_config",
    "log_plan",
    "log_usage",
    "plan_prompt",
]
