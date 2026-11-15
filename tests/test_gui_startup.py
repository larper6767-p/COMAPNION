"""First-run key handling after the startup helpers moved out of app.py."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gui.startup import ensure_env_keys                       # noqa: E402


def _fake_dotenv(monkeypatch, calls):
    module = types.ModuleType("dotenv")
    module.load_dotenv = lambda *args, **kwargs: None
    module.set_key = lambda path, name, value: calls.append((path, name, value))
    monkeypatch.setitem(sys.modules, "dotenv", module)


def test_missing_keys_are_prompted_and_written(monkeypatch, tmp_path, capsys):
    calls = []
    _fake_dotenv(monkeypatch, calls)
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    answers = iter(["fish-key", "cerebras-key"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    ensure_env_keys(tmp_path)

    assert calls == [
        (str(tmp_path / ".env"), "FISH_API_KEY", "fish-key"),
        (str(tmp_path / ".env"), "CEREBRAS_API_KEY", "cerebras-key"),
    ]
    out = capsys.readouterr().out
    assert "First-time setup" in out
    assert "fish-key" not in out


def test_present_keys_do_not_prompt(monkeypatch, tmp_path):
    calls = []
    _fake_dotenv(monkeypatch, calls)
    monkeypatch.setenv("FISH_API_KEY", "present-fish")
    monkeypatch.setenv("CEREBRAS_API_KEY", "present-cerebras")
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )

    ensure_env_keys(tmp_path)

    assert calls == []
