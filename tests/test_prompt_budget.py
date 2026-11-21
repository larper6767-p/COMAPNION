"""Phase 1 prompt budget/observability: pure accounting tests.

No Qt, no LLM, no audio.  These tests pin the estimator, the YAML loader, the
hard-budget trim policy (oldest history only; persona/system never touched),
and the privacy-safe log format.
"""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import prompt_budget as pb  # noqa: E402

ROOT = Path(__file__).parent.parent


def _history(n: int, size: int = 4):
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"m{i}" + ("x" * size),
        }
        for i in range(n)
    ]


# ── estimator ────────────────────────────────────────────────────────────────

def test_estimate_tokens_ignores_blank_and_counts_four_char_units():
    assert pb.estimate_tokens(None) == 0
    assert pb.estimate_tokens("") == 0
    assert pb.estimate_tokens("   \n") == 0
    assert pb.estimate_tokens("hello") == 2          # ceil(5 / 4)
    assert pb.estimate_tokens("x" * 40) == 10


def test_estimate_tokens_is_monotonic():
    base = pb.estimate_tokens("hello")
    assert pb.estimate_tokens("hello world") >= base
    assert pb.estimate_tokens("hello world" * 10) > base


# ── config loader ────────────────────────────────────────────────────────────

def test_live_config_is_enabled_with_sane_bounds():
    cfg = pb.load_config(force=True)
    assert cfg["enabled"] is True
    assert cfg["max_prompt_tokens"] > 0
    assert cfg["ollama_max_prompt_tokens"] > 0
    assert 0.0 < float(cfg["warn_ratio"]) <= 1.0
    assert int(cfg["min_history_messages"]) >= 1
    assert isinstance(cfg["log_every_turn"], bool)


def test_missing_file_uses_shipped_defaults(tmp_path):
    cfg = pb.load_config(tmp_path / "missing.yaml")
    assert cfg == pb.DEFAULTS


def test_malformed_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "prompt_budget.yaml"
    p.write_text("enabled: [unclosed\n: :", encoding="utf-8")
    cfg = pb.load_config(p, force=True)
    assert cfg["enabled"] is True
    assert cfg["max_prompt_tokens"] == pb.DEFAULTS["max_prompt_tokens"]


def test_garbage_numbers_are_clamped_or_defaulted(tmp_path):
    p = tmp_path / "prompt_budget.yaml"
    p.write_text(
        "enabled: true\n"
        "max_prompt_tokens: -50\n"
        "ollama_max_prompt_tokens: banana\n"
        "warn_ratio: 9\n"
        "min_history_messages: 0\n",
        encoding="utf-8",
    )
    cfg = pb.load_config(p, force=True)
    assert cfg["max_prompt_tokens"] == 0
    assert cfg["ollama_max_prompt_tokens"] == pb.DEFAULTS["ollama_max_prompt_tokens"]
    assert cfg["warn_ratio"] == 1.0
    assert cfg["min_history_messages"] == 1


def test_disabled_means_no_budget_and_no_log(caplog, tmp_path):
    p = tmp_path / "prompt_budget.yaml"
    p.write_text("enabled: false\nmax_prompt_tokens: 10\n", encoding="utf-8")
    cfg = pb.load_config(p, force=True)
    out, report = pb.plan_prompt(
        "system", _history(8), max_history=8, cfg=cfg)
    assert len(out) == 8
    assert report.budget_tokens == 0
    assert report.trimmed is False
    with caplog.at_level(logging.DEBUG, logger=pb.__name__):
        pb.log_plan(report, cfg)
    assert caplog.text == ""


# ── budget policy ────────────────────────────────────────────────────────────

def test_zero_budget_observes_without_trimming():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=0, ollama_max_prompt_tokens=0)
    history = _history(10)
    out, report = pb.plan_prompt(
        "system", history, max_history=6, cfg=cfg,
        sections=[("persona", "system")],
    )
    assert out == history[-6:]
    assert report.history_available == 10
    assert report.history_window == 6
    assert report.history_kept == 6
    assert report.history_dropped == 0
    assert report.budget_tokens == 0
    assert report.trimmed is False
    assert report.total_tokens == report.system_tokens + report.history_tokens


def test_budget_drops_oldest_history_only():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=14, min_history_messages=2)
    history = _history(8)
    out, report = pb.plan_prompt("sys", history, max_history=8, cfg=cfg)
    # newest messages survive; the oldest are the ones dropped
    assert out == history[-report.history_kept:]
    assert report.history_kept == 2
    assert report.history_dropped == 6
    assert report.trimmed is True
    assert report.over_budget is False


def test_max_history_window_is_applied_before_budget():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=0)
    history = _history(12)
    out, report = pb.plan_prompt("s", history, max_history=4, cfg=cfg)
    assert out == history[-4:]
    assert report.history_available == 12
    assert report.history_window == 4
    assert report.history_kept == 4


def test_min_history_is_kept_even_when_still_over_budget():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=1, min_history_messages=2)
    history = _history(8)
    out, report = pb.plan_prompt("system", history, max_history=8, cfg=cfg)
    assert len(out) == 2
    assert out == history[-2:]
    assert report.over_budget is True
    assert report.system_over_budget is True


def test_system_prompt_is_never_truncated():
    system = "persona text " * 100
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=5, min_history_messages=1)
    out, report = pb.plan_prompt(system, _history(4), cfg=cfg)
    assert report.system_chars == len(system)
    assert report.system_tokens == pb.estimate_tokens(system)
    assert report.system_over_budget is True
    assert len(out) == 1


def test_local_ollama_uses_the_lower_cap():
    cfg = dict(pb.DEFAULTS)
    assert pb.effective_budget(cfg, use_cerebras=True) == 12000
    assert pb.effective_budget(cfg, use_cerebras=False) == 1600


# ── report / privacy ─────────────────────────────────────────────────────────

def test_sections_are_reported_by_name_without_prompt_text():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=0)
    _out, report = pb.plan_prompt(
        "persona block\n\nmemory block",
        [],
        cfg=cfg,
        sections=[("persona", "persona block"), ("memory", "\n\nmemory block")],
    )
    section_map = report.section_dict()
    assert section_map["persona"] == pb.estimate_tokens("persona block")
    assert section_map["memory"] == pb.estimate_tokens("\n\nmemory block")
    assert "persona block" not in report.plan_line()
    assert "memory block" not in report.plan_line()


def test_plan_log_is_privacy_safe(caplog):
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=0, log_every_turn=True)
    _out, report = pb.plan_prompt(
        "SECRET PERSONA TEXT", [], cfg=cfg,
        sections=[("persona", "SECRET PERSONA TEXT")],
    )
    with caplog.at_level(logging.INFO, logger=pb.__name__):
        pb.log_plan(report, cfg)
    assert "[prompt] total=" in caplog.text
    assert "persona:" in caplog.text
    assert "SECRET PERSONA TEXT" not in caplog.text


def test_usage_attaches_and_keeps_estimate():
    _out, report = pb.plan_prompt("system", _history(2), cfg=dict(pb.DEFAULTS))
    updated = report.with_usage(120, 18, "stop")
    assert updated.actual_prompt_tokens == 120
    assert updated.actual_completion_tokens == 18
    assert updated.actual_finish_reason == "stop"
    assert updated.total_tokens == report.total_tokens
    assert "usage prompt=120" in updated.usage_line()
    assert "finish=stop" in updated.usage_line()
    assert any("usage: 120 prompt" in line
               for line in updated.summary_lines())


def test_warning_flag_tracks_the_configured_ratio():
    cfg = dict(pb.DEFAULTS, max_prompt_tokens=20, min_history_messages=1,
               warn_ratio=0.5)
    _out, report = pb.plan_prompt("system", _history(2), cfg=cfg)
    assert report.total_tokens >= int(20 * 0.5)
    assert report.warn is True
