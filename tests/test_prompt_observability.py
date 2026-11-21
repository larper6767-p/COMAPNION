"""Phase 1 prompt observability wiring tests.

These exercise the real ``VoiceCompanion`` assembly without Whisper, Fish,
Qt, or a live model.  They pin three things:

* the existing system prompt is unchanged while section spans are recorded;
* ``build_messages`` keeps the newest history window and only trims the
  outgoing copy under a hard budget;
* provider usage is attached to the report once, after the main reply.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent


def _purge_stubs():
    for name in list(sys.modules):
        if name == "she" or name == "core" or name.startswith("core."):
            sys.modules.pop(name, None)


class _Memory:
    def build_context_block(self, max_facts=0, query=None):
        return ""


def _companion():
    _purge_stubs()
    from core.config import Config
    from she import VoiceCompanion

    obj = object.__new__(VoiceCompanion)
    obj.config = Config()
    obj.history = []
    obj.last_exchange = None
    obj.memory = _Memory()
    obj._prompt_sections = []
    return VoiceCompanion, obj


def _write_budget(path: Path, **values):
    defaults = {
        "enabled": True,
        "max_prompt_tokens": 0,
        "ollama_max_prompt_tokens": 0,
        "warn_ratio": 0.85,
        "min_history_messages": 2,
        "log_every_turn": True,
    }
    defaults.update(values)
    path.write_text(
        "\n".join(f"{key}: {value}" for key, value in defaults.items()) + "\n",
        encoding="utf-8",
    )


# ── system prompt unchanged, sections recorded ───────────────────────────────

def test_system_message_records_sections_in_order():
    VoiceCompanion, obj = _companion()
    msg = VoiceCompanion.system_message(obj, query=None)
    names = [name for name, _text in obj._prompt_sections]
    assert names[0] == "persona"
    assert "reminder" in names
    assert all(isinstance(text, str) for _name, text in obj._prompt_sections)
    # section spans are references into the same assembled text
    assert obj._prompt_sections[0][1] in msg["content"]


def test_build_messages_preserves_default_history_window():
    VoiceCompanion, obj = _companion()
    history = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"message {i}"}
        for i in range(40)
    ]
    obj.history = history
    messages = obj.build_messages(query=None)
    assert messages[0]["role"] == "system"
    assert messages[1:] == history[-obj.config.max_history:]
    assert obj.last_prompt_report.history_kept == obj.config.max_history
    assert obj.last_prompt_report.trimmed is False


def test_web_block_is_appended_at_the_same_place_and_counted():
    VoiceCompanion, obj = _companion()
    obj.build_messages(query=None, web_block="LIVE WEB SNIPPET")
    assert "LIVE WEB SNIPPET" in obj._prompt_sections[-1][1]
    assert obj._prompt_sections[-1][0] == "web"
    assert obj.last_prompt_report.section_dict().get("web", 0) > 0


# ── budget enforcement is per-turn, never destructive ────────────────────────

def test_hard_budget_trims_outgoing_copy_only(tmp_path):
    VoiceCompanion, obj = _companion()
    obj.config.prompt_budget_file = tmp_path / "prompt_budget.yaml"
    _write_budget(
        obj.config.prompt_budget_file,
        max_prompt_tokens=1,
        min_history_messages=2,
    )
    history = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"message {i} " + ("x" * 200)}
        for i in range(10)
    ]
    obj.history = history
    messages = obj.build_messages(query=None)
    report = obj.last_prompt_report
    assert len(messages) == 3                 # system + 2 newest messages
    assert messages[1:] == history[-2:]
    assert report.trimmed is True
    assert report.history_kept == 2
    assert report.history_dropped == 8
    assert obj.history == history             # in-memory history untouched


def test_zero_budget_is_observe_only(tmp_path):
    VoiceCompanion, obj = _companion()
    obj.config.prompt_budget_file = tmp_path / "prompt_budget.yaml"
    _write_budget(obj.config.prompt_budget_file, max_prompt_tokens=0)
    obj.history = [
        {"role": "user", "content": "old " + ("x" * 300)},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new"},
    ]
    messages = obj.build_messages(query=None)
    assert len(messages) == 4
    assert obj.last_prompt_report.budget_tokens == 0
    assert obj.last_prompt_report.trimmed is False


# ── provider usage is attached once ──────────────────────────────────────────

def test_finalize_attaches_provider_usage_once():
    VoiceCompanion, obj = _companion()
    obj.model = SimpleNamespace(
        get_last_usage=lambda: {"prompt": 123, "completion": 17},
        get_last_finish_reason=lambda: "stop",
    )
    obj.build_messages(query=None)
    VoiceCompanion._finalize_prompt_report(obj)
    report = obj.last_prompt_report
    assert report.actual_prompt_tokens == 123
    assert report.actual_completion_tokens == 17
    assert report.actual_finish_reason == "stop"

    # A later internal call must not overwrite the already-finalized turn.
    obj.model = SimpleNamespace(
        get_last_usage=lambda: {"prompt": 999, "completion": 999},
        get_last_finish_reason=lambda: "length",
    )
    VoiceCompanion._finalize_prompt_report(obj)
    assert obj.last_prompt_report.actual_prompt_tokens == 123


# ── model-level usage capture ────────────────────────────────────────────────

def _bare_model():
    _purge_stubs()
    from core.model import ModelInterface

    m = object.__new__(ModelInterface)
    m.model_name = "test-model"
    m.temperature = 0.7
    m.use_cerebras = True
    m.max_tokens = 100
    m.repeat_penalty = 1.1
    m.stop = []
    m.vision_model = ""
    m.total_tokens = {"prompt": 0, "completion": 0}
    m.last_usage = {"prompt": 0, "completion": 0}
    m.last_finish_reason = ""
    return m


def test_cerebras_usage_and_finish_reason_are_captured():
    m = _bare_model()

    class _Usage:
        prompt_tokens = 321
        completion_tokens = 42

    class _Choice:
        finish_reason = "stop"
        delta = SimpleNamespace(content="hello")

    class _Chunk:
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        def create(self, **kwargs):
            return [_Chunk()]

    m.cerebras_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions()))
    chunks = list(m._stream_cerebras([{"role": "user", "content": "hi"}], 0.2))
    assert chunks[0]["message"]["content"] == "hello"
    assert m.last_usage == {"prompt": 321, "completion": 42}
    assert m.last_finish_reason == "stop"


def test_ollama_usage_and_finish_reason_are_captured(monkeypatch):
    m = _bare_model()
    m.use_cerebras = False

    def _fake_chat(**kwargs):
        return [
            {"message": {"content": "hi"}, "done": False},
            {"message": {"content": ""}, "done": True,
             "prompt_eval_count": 111, "eval_count": 7,
             "done_reason": "stop"},
        ]

    import core.model as model_module
    monkeypatch.setattr(model_module.ollama, "chat", _fake_chat)
    list(m._stream_ollama([{"role": "user", "content": "hi"}], 0.2))
    assert m.last_usage == {"prompt": 111, "completion": 7}
    assert m.last_finish_reason == "stop"
    assert m.total_tokens == {"prompt": 111, "completion": 7}
