"""Command parsing and execution for the companion CLI and GUI.

This module extracts the shared command-handling logic from she.py and app.py,
making it testable and reusable across both interfaces.

Commands include:
- /remember <fact> — save a fact to memory
- /forget — clear all memory facts
- /mute — toggle voice on/off
- /volume <0-1> — set playback volume
- /speed <0.5-2.0> — set TTS speed
- /reload — hot-reload personality.json
- /stats — show token/memory/relationship stats
- /t <seconds> — change max recording duration
- Natural language: "what time is it", "open <app>", "timer for <n> <unit>"
"""
from __future__ import annotations

import datetime
import logging
import random
import re
import subprocess
import threading
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from core.config import Config
    from core.memory import MemoryManager
    from core.model import ModelInterface
    from audio import Speaker

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of executing a command."""
    success: bool = True
    message: str = ""
    speak: bool = True  # Whether the message should be spoken
    data: Dict[str, Any] = field(default_factory=dict)


class CommandHandler:
    """Parse and execute companion commands.
    
    This class extracts command-handling logic from both VoiceCompanion (CLI)
    and PetCompanion (GUI), providing a unified, testable interface.
    """
    
    _MISSED_PHRASES = [
        "Hmm, didn't quite catch that.",
        "Sorry, could you say that again?",
        "I didn't hear anything.",
    ]
    
    def __init__(
        self,
        config: "Config",
        memory: "MemoryManager",
        model: "ModelInterface",
        speaker: "Speaker",
        history: List[Dict[str, str]],
        on_reload: Optional[Callable[[], None]] = None,
        on_stats: Optional[Callable[[], None]] = None,
    ):
        self.config = config
        self.memory = memory
        self.model = model
        self.speaker = speaker
        self.history = history
        self.on_reload = on_reload
        self.on_stats = on_stats
        self.max_record_seconds = 20.0
    
    def try_command(self, text: str) -> Optional[CommandResult]:
        """Try to parse and execute a command from user text.
        
        Returns CommandResult if text was a command, None otherwise.
        The caller should handle the result (print/speak as appropriate).
        """
        text = text.strip()
        if not text:
            return None
        
        # Slash commands
        if text.startswith("/"):
            return self._try_slash_command(text)
        
        # Natural language commands
        return self._try_natural_command(text)
    
    def _try_slash_command(self, text: str) -> Optional[CommandResult]:
        """Handle slash commands (/remember, /forget, etc.)."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        
        if cmd == "/remember":
            return self.cmd_remember(arg)
        elif cmd == "/forget":
            return self.cmd_forget()
        elif cmd == "/mute":
            return self.cmd_mute()
        elif cmd == "/volume":
            return self.cmd_volume(arg)
        elif cmd == "/speed":
            return self.cmd_speed(arg)
        elif cmd in ("/t", "/timer"):
            return self.cmd_timer(arg)
        elif cmd == "/reload":
            return self.cmd_reload()
        elif cmd == "/stats":
            return self.cmd_stats()
        elif cmd == "/quit":
            return CommandResult(success=True, message="Goodbye!", speak=True)
        elif cmd in ("/long", "/short"):
            # Handled by _verb_command in the companion
            return None
        
        return None
    
    def _try_natural_command(self, text: str) -> Optional[CommandResult]:
        """Handle natural language commands (time, timer, open apps)."""
        t = text.lower()
        
        # What time is it?
        if re.search(r"what time is it|what('s| is) the time", t):
            now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
            return CommandResult(
                success=True,
                message=f"It's {now}.",
                speak=True
            )
        
        # Timer
        m = re.search(r"timer for (\d+)\s*(second|minute|hour)", t)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            secs = n * {"second": 1, "minute": 60, "hour": 3600}[unit]
            
            def _fire():
                msg = f"Your {n} {unit} timer is up!"
                self.speaker.say(msg)
                logger.info(f"Timer fired: {msg}")
            
            timer = threading.Timer(secs, _fire)
            timer.daemon = True
            timer.start()
            plural = 's' if n != 1 else ''
            return CommandResult(
                success=True,
                message=f"Okay, timer set for {n} {unit}{plural}.",
                speak=True
            )
        
        # Open app/site
        m = re.search(r"\bopen (\w+)", t)
        if m:
            name = m.group(1)
            if name in self.config.apps:
                try:
                    subprocess.Popen(self.config.apps[name])
                    return CommandResult(
                        success=True,
                        message=f"Opening {name}.",
                        speak=True
                    )
                except OSError as e:
                    logger.error(f"Could not open app '{name}': {e}")
                    return CommandResult(
                        success=False,
                        message=f"Sorry, I couldn't open {name}.",
                        speak=True
                    )
            if name in self.config.sites:
                webbrowser.open(self.config.sites[name])
                return CommandResult(
                    success=True,
                    message=f"Opening {name}.",
                    speak=True
                )
        
        return None
    
    # ── Individual slash commands ─────────────────────────────────────────
    
    def cmd_remember(self, fact: str) -> CommandResult:
        """Add a fact to memory."""
        if not fact:
            return CommandResult(
                success=False,
                message="Usage: /remember <fact>",
                speak=False
            )
        self.memory.add_fact(fact)
        self.config.save_memory(self.memory.get_facts())
        return CommandResult(
            success=True,
            message=f"✓ Remembered: {fact}",
            speak=False
        )
    
    def cmd_forget(self) -> CommandResult:
        """Clear all memory facts."""
        self.memory.clear_facts()
        self.config.save_memory([])
        return CommandResult(
            success=True,
            message="✓ Memory facts cleared",
            speak=False
        )
    
    def cmd_mute(self) -> CommandResult:
        """Toggle voice on/off."""
        self.speaker.enabled = not self.speaker.enabled
        state = "off" if not self.speaker.enabled else "on"
        return CommandResult(
            success=True,
            message=f"🔊 Voice {state}",
            speak=False
        )
    
    def cmd_volume(self, arg: str) -> CommandResult:
        """Set playback volume (0.0–1.0)."""
        try:
            vol = float(arg)
            self.speaker.set_volume(vol)
            return CommandResult(
                success=True,
                message=f"🔊 Volume: {self.speaker.volume:.0%}",
                speak=False
            )
        except ValueError:
            return CommandResult(
                success=False,
                message="Usage: /volume <0.0–1.0>",
                speak=False
            )
    
    def cmd_speed(self, arg: str) -> CommandResult:
        """Set TTS speed (0.5–2.0)."""
        try:
            speed = float(arg)
            self.speaker.set_speed(speed)
            return CommandResult(
                success=True,
                message=f"⚡ Speed: {self.speaker.speed:.1f}x",
                speak=False
            )
        except ValueError:
            return CommandResult(
                success=False,
                message="Usage: /speed <0.5–2.0>",
                speak=False
            )
    
    def cmd_timer(self, arg: str) -> CommandResult:
        """Set max recording duration."""
        try:
            secs = float(arg)
            self.max_record_seconds = secs
            return CommandResult(
                success=True,
                message=f"⏱️ Max recording: {secs}s",
                speak=False
            )
        except ValueError:
            return CommandResult(
                success=False,
                message="Usage: /t <seconds>",
                speak=False
            )
    
    def cmd_reload(self) -> CommandResult:
        """Hot-reload personality.json."""
        try:
            self.config.reload()
            if self.on_reload:
                self.on_reload()
            return CommandResult(
                success=True,
                message=f"✓ Personality reloaded: {self.config.persona['name']}",
                speak=False
            )
        except Exception as e:
            return CommandResult(
                success=False,
                message=f"✗ Reload failed: {e}",
                speak=False
            )
    
    def cmd_stats(self) -> CommandResult:
        """Show statistics."""
        # If there's a custom stats handler, use it
        if self.on_stats:
            self.on_stats()
            return CommandResult(success=True, message="", speak=False)
        
        # Default stats output
        tokens = self.model.get_token_stats()
        lines = [
            "\n📊 Statistics:",
            f"  Closeness: {self.memory.closeness_score()} pts ({self.memory.tier_name()})",
            f"  Memory facts: {len(self.memory.get_facts())}",
            f"  History: {len(self.history)} messages",
            f"  Tokens: {tokens['prompt']} prompt, {tokens['completion']} completion",
        ]
        
        backend = "Cerebras" if self.config.use_cerebras else "Ollama"
        lines.append(f"  Backend: {backend} / {self.model.model_name}")
        
        return CommandResult(
            success=True,
            message="\n".join(lines),
            speak=False
        )
    
    # ── Helper methods ─────────────────────────────────────────────────────
    
    def get_missed_phrase(self) -> str:
        """Get a random phrase for when transcription fails."""
        return random.choice(self._MISSED_PHRASES)
    
    @staticmethod
    def get_help_text() -> str:
        """Get help text for available commands."""
        return """
Commands:
  Enter              record voice
  text + Enter       text message
  /remember <fact>   save a fact to memory
  /forget            clear all memory facts
  /mute              toggle voice on/off
  /volume <0-1>      set playback volume
  /speed <0.5-2.0>   set TTS speed
  /reload            hot-reload personality.json
  /stats             show statistics
  /t <seconds>       change max recording duration
  /quit              exit
"""
