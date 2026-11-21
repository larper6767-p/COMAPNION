"""Tests for core/commands.py — command parsing and execution."""
import sys
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

# Ensure src is on sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.commands import CommandHandler, CommandResult


@pytest.fixture
def mock_config():
    """Create a mock Config."""
    cfg = MagicMock()
    cfg.persona = {"name": "Test", "max_reply_length": 300}
    cfg.apps = {"notepad": "notepad.exe", "calculator": "calc.exe"}
    cfg.sites = {"youtube": "https://youtube.com"}
    cfg.use_cerebras = True
    return cfg


@pytest.fixture
def mock_memory():
    """Create a mock MemoryManager."""
    mem = MagicMock()
    mem.get_facts.return_value = []
    mem.closeness_score.return_value = 10
    mem.tier_name.return_value = "warming up"
    return mem


@pytest.fixture
def mock_model():
    """Create a mock ModelInterface."""
    model = MagicMock()
    model.model_name = "test-model"
    model.get_token_stats.return_value = {"prompt": 100, "completion": 50}
    return model


@pytest.fixture
def mock_speaker():
    """Create a mock Speaker."""
    speaker = MagicMock()
    speaker.enabled = True
    speaker.volume = 0.7
    speaker.speed = 1.0
    return speaker


@pytest.fixture
def handler(mock_config, mock_memory, mock_model, mock_speaker):
    """Create a CommandHandler with mocked dependencies."""
    return CommandHandler(
        config=mock_config,
        memory=mock_memory,
        model=mock_model,
        speaker=mock_speaker,
        history=[],
    )


class TestCommandParsing:
    """Test command parsing."""
    
    def test_slash_remember_with_fact(self, handler):
        result = handler.try_command("/remember I like pizza")
        assert result is not None
        assert result.success
        assert "pizza" in result.message
    
    def test_slash_remember_without_fact(self, handler):
        result = handler.try_command("/remember")
        assert result is not None
        assert not result.success
        assert "Usage" in result.message
    
    def test_slash_forget(self, handler):
        result = handler.try_command("/forget")
        assert result is not None
        assert result.success
        assert "cleared" in result.message.lower()
    
    def test_slash_mute(self, handler, mock_speaker):
        assert mock_speaker.enabled  # starts enabled
        result = handler.try_command("/mute")
        assert result is not None
        assert result.success
        assert not mock_speaker.enabled  # now muted
        assert "off" in result.message.lower()
        
        # Toggle back
        result = handler.try_command("/mute")
        assert mock_speaker.enabled  # now unmuted
        assert "on" in result.message.lower()
    
    def test_slash_volume_valid(self, handler, mock_speaker):
        result = handler.try_command("/volume 0.5")
        assert result is not None
        assert result.success
        mock_speaker.set_volume.assert_called_once_with(0.5)
    
    def test_slash_volume_invalid(self, handler):
        result = handler.try_command("/volume abc")
        assert result is not None
        assert not result.success
        assert "Usage" in result.message
    
    def test_slash_speed_valid(self, handler, mock_speaker):
        result = handler.try_command("/speed 1.5")
        assert result is not None
        assert result.success
        mock_speaker.set_speed.assert_called_once_with(1.5)
    
    def test_slash_speed_invalid(self, handler):
        result = handler.try_command("/speed abc")
        assert result is not None
        assert not result.success
        assert "Usage" in result.message
    
    def test_slash_timer_valid(self, handler):
        result = handler.try_command("/t 30")
        assert result is not None
        assert result.success
        assert handler.max_record_seconds == 30.0
    
    def test_slash_timer_invalid(self, handler):
        result = handler.try_command("/t abc")
        assert result is not None
        assert not result.success
    
    def test_unknown_slash_command(self, handler):
        result = handler.try_command("/unknown")
        assert result is None
    
    def test_empty_text(self, handler):
        result = handler.try_command("")
        assert result is None
    
    def test_whitespace_only(self, handler):
        result = handler.try_command("   ")
        assert result is None


class TestNaturalLanguageCommands:
    """Test natural language command parsing."""
    
    def test_what_time_is_it(self, handler):
        result = handler.try_command("what time is it")
        assert result is not None
        assert result.success
        assert "It's" in result.message
    
    def test_whats_the_time(self, handler):
        result = handler.try_command("what's the time")
        assert result is not None
        assert result.success
        assert "It's" in result.message
    
    def test_timer_seconds(self, handler):
        with patch('threading.Timer') as mock_timer:
            result = handler.try_command("timer for 30 seconds")
            assert result is not None
            assert result.success
            assert "30" in result.message
            assert "second" in result.message
            mock_timer.assert_called_once()
            # Extract the timer duration
            args, kwargs = mock_timer.call_args
            assert args[0] == 30  # 30 seconds
    
    def test_timer_minutes(self, handler):
        with patch('threading.Timer') as mock_timer:
            result = handler.try_command("timer for 5 minutes")
            assert result is not None
            assert result.success
            assert "5" in result.message
            assert "minute" in result.message
            mock_timer.assert_called_once()
            args, kwargs = mock_timer.call_args
            assert args[0] == 300  # 5 minutes in seconds
    
    def test_timer_hours(self, handler):
        with patch('threading.Timer') as mock_timer:
            result = handler.try_command("timer for 1 hour")
            assert result is not None
            assert result.success
            mock_timer.assert_called_once()
            args, kwargs = mock_timer.call_args
            assert args[0] == 3600  # 1 hour in seconds
    
    def test_open_app_known(self, handler, mock_config):
        with patch('subprocess.Popen') as mock_popen:
            result = handler.try_command("open notepad")
            assert result is not None
            assert result.success
            assert "Opening" in result.message
            mock_popen.assert_called_once_with("notepad.exe")
    
    def test_open_site_known(self, handler):
        with patch('webbrowser.open') as mock_open:
            result = handler.try_command("open youtube")
            assert result is not None
            assert result.success
            assert "Opening" in result.message
            mock_open.assert_called_once_with("https://youtube.com")
    
    def test_open_unknown_app(self, handler):
        result = handler.try_command("open unknownapp")
        assert result is None  # Not a recognized app/site, not a command


class TestCommandResult:
    """Test CommandResult dataclass."""
    
    def test_default_values(self):
        result = CommandResult()
        assert result.success
        assert result.message == ""
        assert result.speak
        assert result.data == {}
    
    def test_custom_values(self):
        result = CommandResult(
            success=False,
            message="Error",
            speak=False,
            data={"key": "value"}
        )
        assert not result.success
        assert result.message == "Error"
        assert not result.speak
        assert result.data == {"key": "value"}


class TestHelperMethods:
    """Test helper methods."""
    
    def test_get_missed_phrase(self, handler):
        phrase = handler.get_missed_phrase()
        assert isinstance(phrase, str)
        assert len(phrase) > 0
    
    def test_get_help_text(self):
        help_text = CommandHandler.get_help_text()
        assert "Commands:" in help_text
        assert "/remember" in help_text
        assert "/forget" in help_text
        assert "/mute" in help_text
