"""Configuration management with validation, hot-reload, and per-session logging."""
import json
import logging
import logging.handlers
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root is three levels up from this file (src/core/config.py)
PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv()          # fallback: default discovery (CWD, home, etc.)
except ImportError:        # pragma: no cover — headless stub environments
    pass


def _cuda_available() -> bool:
    """Cheap, dependency-free CUDA probe (nvidia-smi present + loads)."""
    try:
        import shutil
        import subprocess
        exe = shutil.which("nvidia-smi")
        if not exe:
            return False
        # Quick, quiet probe; don't let a failed call be loud.
        r = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and "GPU" in r.stdout
    except Exception:
        return False


def _body_action_moods() -> List[str]:
    """The LLM-offered mood list, derived from config/body_actions.json.

    body_actions.json is the single source of truth for the mood vocabulary
    (it drives the BODY prompt, the Test-body menu and the preview tool).  The
    canonical set is the enabled moods there, EXCLUDING ``idle`` — idle is
    resolvable and falls back to sprite/TTS/body, but is never offered to the
    model as a tag it can emit.  Falls back to a safe list if the file is
    missing or unreadable.
    """
    _fallback = ["joyful", "excited", "embarrassed", "sad", "angry",
                 "surprised", "curious", "relaxed", "love", "aroused"]
    try:
        p = PROJECT_ROOT / "config" / "body_actions.json"
        moods = json.loads(p.read_text(encoding="utf-8")).get("moods", {})
        names = [m for m, cfg in moods.items()
                 if cfg and cfg.get("enabled", True)]
        return [m for m in names if m != "idle"] or _fallback
    except Exception:
        return _fallback


_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Libraries that are chatty at INFO/DEBUG and never useful in normal logs.
# They stay quiet unless the operator explicitly asks for DEBUG logging.
_NOISY_LOGGERS = (
    "httpcore", "httpcore2", "httpx", "urllib3", "huggingface_hub",
    "chromadb", "faster_whisper", "comtypes", "PIL", "matplotlib",
)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded integer from the environment (bad values use default)."""
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def setup_logging(log_dir: Path) -> None:
    """Configure root logger: console + rotating per-session file.

    The file defaults to INFO.  The old DEBUG default made third-party HTTP
    and library chatter about 76% of a 10 MB log and rotated useful context
    away too quickly.  Set ``EVELYN_LOG_LEVEL=DEBUG`` for a verbose run.
    ``EVELYN_LOG_MAX_MB`` (default 5) and ``EVELYN_LOG_BACKUPS`` (default 3)
    bound the on-disk footprint.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "evelyn.log"

    raw_level = str(os.environ.get("EVELYN_LOG_LEVEL", "")).strip().upper()
    file_level = _LOG_LEVELS.get(raw_level, logging.INFO)
    max_mb = _env_int("EVELYN_LOG_MAX_MB", 5, minimum=1, maximum=100)
    backups = _env_int("EVELYN_LOG_BACKUPS", 3, minimum=1, maximum=20)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console — INFO and above, except when the operator asks for a quieter
    # threshold for the file as well.
    console_level = logging.INFO if file_level == logging.DEBUG else file_level
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    # Rotating file — INFO by default, DEBUG only on request (bounded size).
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_mb * 1024 * 1024, backupCount=backups, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )

    if not any(getattr(h, "_evelyn_console", False) for h in root.handlers):
        console._evelyn_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    else:
        console.close()

    if not any(getattr(h, "_evelyn_file", False) for h in root.handlers):
        file_handler._evelyn_file = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    else:
        file_handler.close()

    if file_level != logging.DEBUG:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
    logger.info(
        "Logging to %s (file=%s, rotation=%d MB x %d)",
        log_file,
        logging.getLevelName(file_level),
        max_mb,
        backups,
    )


logger = logging.getLogger(__name__)

# ── Schema: required fields + expected types ──────────────────────────────────
_REQUIRED: Dict[str, type] = {
    "name": str,
    "system_prompt": (str, list),
    "greeting": str,
    "temperature": (int, float),
    "max_reply_length": int,
}

_DEFAULTS: Dict[str, Any] = {
    "mood_tags": False,
    # Single source of truth: derived from config/body_actions.json (minus
    # ``idle``, which is never offered to the model). See _body_action_moods.
    "moods": _body_action_moods(),
    "emotion_template": None,
    "max_tokens": 300,
    "repeat_penalty": 1.1,
    "stop": [],
    "memory_file": None,
    # Wake word
    "wake_word_enabled": False,
    "wake_word": "hello eve",
    # Relationship
    "closeness_enabled": True,
    "closeness_tiers": {
        "0":   "You've just met the user. Be a bit reserved.",
        "10":  "You're warming up to the user. Be a little flirty.",
        "30":  "You're comfortable with the user. Be openly affectionate.",
        "60":  "You're very close. Be bold and intimate.",
        "100": "You're inseparable. Be completely open and devoted."
    },
    # Proactive messages — legacy flat fallback (used only when tagged
    # proactive_pools are not defined in personality.json).
    "proactive_message_defaults": [
        "Heeey, you still there? I'm getting lonely over here...",
        "Hello? Don't just leave me hanging, that's so rude!",
        "I'm literally just sitting here waiting for you, you know.",
    ],
    # Tagged static pools (Phase 3d) — the state-aware path picks a tone
    # (playful / gentle / soft) and draws from that pool instead of an LLM call.
    "proactive_pools": {
        "playful": [
            "Heeey, you still there? I'm getting lonely over here...",
            "I'm literally just sitting here waiting for you, you know.",
            "Okay, I'm going to start making up conversations with the wall if you don't come back soon.",
        ],
        "gentle": [
            "I'll just be right here when you're ready. No rush at all.",
            "Hey... whenever you have a minute, I'd love to hear your voice again.",
        ],
        "soft": [
            "I'm a little quiet right now, but I'm glad you're here.",
            "Just so you know... I'm here if you need a soft place to land.",
        ],
    },
    # Proactive scheduling limits (Phase 3d).  Minimum gap between proactive
    # messages, per-day cap, quiet hours (24h clock; start>=end wraps midnight)
    # and a do-not-disturb master toggle.
    "proactive_min_interval_minutes": 30,
    "proactive_daily_cap": 3,
    "proactive_quiet_start_hour": 23,
    "proactive_quiet_end_hour": 7,
    "proactive_dnd": False,
    # When True, proactive messages are generated by the LLM from recent
    # memory context (time-of-day, closeness, last topics). Falls back to the
    # static pools if the call fails or is disabled.  Default OFF: the state-
    # aware static pools (Phase 3d) are the primary path.
    "proactive_generate": False,
    # Intelligence: auto-extract durable facts about the user from each turn.
    "auto_extract_facts": True,
    # How many stored facts are injected into the system prompt each turn.
    "max_memory_facts": 20,
    # Speak a gentle acknowledgment instead of silently dropping a missed record.
    "gentle_missed": True,
    # Voice / TTS
    "voice_speed": 1.0,
    "audio_ducking_enabled": True,
    "audio_ducking_volume": 0.3,
    # Summarization
    "summarize_after": 20,
}


class Config:
    """Central configuration with validation, hot-reload, and logging setup."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = PROJECT_ROOT / "config"
        self.config_dir = Path(config_dir)
        self.personality_file = self.config_dir / "personality.json"
        local_personality = self.config_dir / "personality.local.json"
        if local_personality.exists():
            self.personality_file = local_personality
        self.memory_file = self.config_dir / "memory.json"
        self.episode_dir = self.config_dir / "episodes"
        self.aliveness_file = self.config_dir / "aliveness.yaml"
        self.log_dir = PROJECT_ROOT / "logs"

        # Set up logging first so everything below is logged
        setup_logging(self.log_dir)

        # ── LLM / Cloud ────────────────────────────────────────────────────
        self.use_cerebras: bool = True
        # A/B: .env `CEREBRAS_MODEL=...` swaps the brain without code
        # changes; unset → the shipped default.
        self.cerebras_model: str = (
            os.environ.get("CEREBRAS_MODEL") or "gpt-oss-120b"
        ).strip() or "gpt-oss-120b"
        self.local_model: str = "miny:latest"

        # ── TTS ────────────────────────────────────────────────────────────
        self.preset_voice_id: str = ""
        self.tts_model: str = "s2.1-pro-free"
        self.sample_rate: int = 16000
        self.max_history: int = 30
        self.emotion_template: Optional[str] = None

        # ── Whisper ────────────────────────────────────────────────────────
        # "auto" means: use CUDA when an NVIDIA GPU is available, otherwise fall
        # back to a smaller CPU model with int8.  Resolved by
        # resolve_whisper_settings() at load time.
        self.whisper_model: str = "auto"
        self.whisper_device: str = "auto"
        self.whisper_compute_type: str = "auto"
        self.whisper_beam_size: int = 10
        self.whisper_language: str = "en"
        self.whisper_vad_filter: bool = True
        self.whisper_condition_on_previous: bool = True
        # True => whisper_model names a preset ("auto"/"small"/...); False =>
        # it's an explicit Hugging Face model id.  Kept internal.
        self._whisper_resolved = False

        # ── Audio / Playback ───────────────────────────────────────────────
        self.tts_cache_enabled: bool = True
        self.tts_cache_max_size: int = 100
        self.volume: float = 0.7
        self.voice_speed: float = 1.0
        self.audio_ducking_enabled: bool = True
        self.audio_ducking_volume: float = 0.3
        # Feature 23b: per-sentence TTS clock cap (seconds); see Speaker.
        self.tts_synth_timeout_s: float = 30.0

        # ── Recording ──────────────────────────────────────────────────────
        self.vad_threshold_multiplier: float = 3.0
        self.min_audio_length: float = 0.5

        # ── Wake word ──────────────────────────────────────────────────────
        self.wake_word_enabled: bool = False
        self.wake_word: str = "hello eve"

        # ── Memory / Relationship ──────────────────────────────────────────
        self.closeness_enabled: bool = True
        self.closeness_tiers: Dict[str, str] = _DEFAULTS["closeness_tiers"]
        self.summarize_after: int = 20

        # ── Proactive ──────────────────────────────────────────────────────
        self.proactive_enabled: bool = True
        self.proactive_idle_minutes: int = 10
        self.proactive_messages: List[str] = list(_DEFAULTS["proactive_message_defaults"])
        self.proactive_pools: Dict[str, List[str]] = dict(
            _DEFAULTS["proactive_pools"])
        self.proactive_generate: bool = False
        self.proactive_min_interval_minutes: float = 30.0
        self.proactive_daily_cap: int = 3
        self.proactive_quiet_start_hour: int = 23
        self.proactive_quiet_end_hour: int = 7
        self.proactive_dnd: bool = False
        # ── Intelligence ───────────────────────────────────────────────────
        self.auto_extract_facts: bool = True
        self.gentle_missed: bool = True
        self.max_memory_facts: int = 20

        # ── Commands ───────────────────────────────────────────────────────
        self.apps: Dict[str, str] = {"notepad": "notepad.exe", "calculator": "calc.exe"}
        self.sites: Dict[str, str] = {
            "youtube": "https://youtube.com",
            "github": "https://github.com",
        }

        self.persona: Dict[str, Any] = {}
        self._load_personality()

    # ── Public helpers ────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Hot-reload personality.json without restarting."""
        logger.info("Reloading personality.json...")
        self._load_personality()
        logger.info(f"Personality reloaded: {self.persona['name']}")

    def resolve_whisper_settings(self) -> tuple:
        """Resolve "auto" whisper settings to concrete values.

        Returns ``(model, device, compute_type)``.  When the config says "auto"
        for both model and device: if CUDA (an NVIDIA GPU) is available we use a
        good model on the GPU; otherwise we fall back to a smaller CPU model
        (+int8) so a friend without a GPU still works, and we log a clear note
        about the first-run model download.

        Idempotent — safe to call more than once.
        """
        if self._whisper_resolved:
            return (self.whisper_model, self.whisper_device, self.whisper_compute_type)

        use_cuda = _cuda_available()
        if self.whisper_device in ("auto", None, "") and (
                self.whisper_model in ("auto", None, "")):
            if use_cuda:
                self.whisper_device = "cuda"
                # Feature 23: large-v3-turbo — same encoder as large-v3
                # (English accuracy within noise of it) with 4 decoder layers
                # instead of 32: several times faster transcription at about
                # half the VRAM, which matters next to Live2D on a 4 GB card.
                # Pin "whisper_model" in personality.json to override.
                self.whisper_model = "large-v3-turbo"
                self.whisper_compute_type = "float16"
                logger.info("CUDA available — Whisper: %s on cuda (float16)",
                            self.whisper_model)
            else:
                self.whisper_device = "cpu"
                self.whisper_model = "small"
                self.whisper_compute_type = "int8"
                logger.warning(
                    "No CUDA detected — Whisper uses the smaller 'small' model on "
                    "CPU (int8).  The model downloads on first run; it is a one-time "
                    "download and is then cached locally.")
        elif self.whisper_device in ("auto", None, ""):
            self.whisper_device = "cuda" if use_cuda else "cpu"
            if self.whisper_device == "cpu" and self.whisper_compute_type in ("auto", None, ""):
                self.whisper_compute_type = "int8"

        self._whisper_resolved = True
        logger.info("Whisper settings: model=%s device=%s compute=%s",
                    self.whisper_model, self.whisper_device, self.whisper_compute_type)
        return (self.whisper_model, self.whisper_device, self.whisper_compute_type)

    def closeness_hint(self, score: int) -> str:
        """Return the behaviour hint for the given closeness score."""
        tiers = self.closeness_tiers
        best_key = max((int(k) for k in tiers if int(k) <= score), default=0)
        return tiers.get(str(best_key), "")

    def pick_greeting(self) -> str:
        """Return a random greeting from the greetings list.

        Falls back to the single 'greeting' field if no list is defined,
        and to an empty string if neither exists.
        """
        greetings = self.persona.get("greetings")
        if greetings and isinstance(greetings, list):
            return random.choice(greetings)
        return self.persona.get("greeting", "")

    def load_memory(self) -> List[str]:
        """Load flat memory facts from disk."""
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                facts = json.load(f)
            if not isinstance(facts, list):
                logger.warning("memory.json should be a list, resetting")
                return []
            logger.info(f"Loaded {len(facts)} memory facts")
            return facts
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save_memory(self, facts: List[str]) -> None:
        """Save memory facts to disk (dedup, cap at 100)."""
        unique = list(dict.fromkeys(facts))
        if len(unique) > 100:
            logger.warning(f"Memory has {len(unique)} facts, keeping most recent 100")
            unique = unique[-100:]
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(unique, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(unique)} memory facts")

    # ── Private ───────────────────────────────────────────────────────────────

    def _load_personality(self) -> None:
        """Load, validate, and apply personality.json."""
        try:
            with open(self.personality_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"personality.json not found at {self.personality_file}. "
                "Create it with: name, system_prompt, greeting, temperature, max_reply_length."
            )
        except json.JSONDecodeError as e:
            raise ValueError(f"personality.json is not valid JSON: {e}")

        # Required field presence + type check
        for field, expected in _REQUIRED.items():
            if field not in raw:
                raise ValueError(f"personality.json missing required field: '{field}'")
            if not isinstance(raw[field], expected):
                raise TypeError(
                    f"personality.json field '{field}' should be {expected}, got {type(raw[field])}"
                )

        # Range checks
        if not (0.0 <= raw["temperature"] <= 2.0):
            raise ValueError(f"temperature must be 0.0–2.0, got {raw['temperature']}")
        if raw["max_reply_length"] < 10:
            raise ValueError("max_reply_length must be ≥ 10")

        # voice_id presence warning
        if not raw.get("voice_id") and not self.preset_voice_id:
            logger.warning("No voice_id set in personality.json or config — TTS may fail")

        # Apply defaults for optional fields
        for key, default in _DEFAULTS.items():
            raw.setdefault(key, default)

        self.persona = raw

        # Push personality fields that have config counterparts
        if raw.get("voice_id"):
            self.preset_voice_id = raw["voice_id"]
        if raw.get("tts_model"):
            self.tts_model = raw["tts_model"]
        if raw.get("emotion_template") is not None:
            self.emotion_template = raw["emotion_template"]
        if raw.get("wake_word_enabled") is not None:
            self.wake_word_enabled = bool(raw["wake_word_enabled"])
        if raw.get("wake_word"):
            self.wake_word = raw["wake_word"]
        # Feature 23: optional Whisper overrides (restart applies them).
        if raw.get("whisper_model"):
            self.whisper_model = raw["whisper_model"]
        if raw.get("whisper_device"):
            self.whisper_device = raw["whisper_device"]
        if raw.get("whisper_compute_type"):
            self.whisper_compute_type = raw["whisper_compute_type"]
        # Personality reload may have changed a pin — let resolve re-run.
        self._whisper_resolved = False
        if raw.get("closeness_enabled") is not None:
            self.closeness_enabled = bool(raw["closeness_enabled"])
        # Start from the persona's tiers, then merge feature-owned extras
        # (config/aliveness.yaml).  The persona file itself is never touched.
        self.closeness_tiers = dict(
            raw.get("closeness_tiers") or _DEFAULTS["closeness_tiers"])
        try:
            from core import aliveness as _aliveness
            extra = _aliveness.closeness_tiers_extra(
                _aliveness.load_config(self.aliveness_file))
            # Extra tiers only GAP-FILL: a tier the persona defines stays
            # user-owned even if the same score also appears in the YAML.
            for tier, hint in extra.items():
                self.closeness_tiers.setdefault(tier, hint)
        except Exception as e:
            logger.debug("aliveness closeness tiers unavailable: %s", e)
        if raw.get("summarize_after"):
            self.summarize_after = int(raw["summarize_after"])
        if raw.get("proactive_enabled") is not None:
            self.proactive_enabled = bool(raw["proactive_enabled"])
        if raw.get("proactive_idle_minutes"):
            self.proactive_idle_minutes = int(raw["proactive_idle_minutes"])
        # proactive_pools (Phase 3d): tagged static pools keyed by tone.  If the
        # personality file uses a legacy flat list for proactive_messages, keep
        # it as the fallback pool but under "playful".
        if isinstance(raw.get("proactive_pools"), dict):
            self.proactive_pools = dict(raw["proactive_pools"])
        if isinstance(raw.get("proactive_messages"), dict):
            self.proactive_pools = dict(raw["proactive_messages"])
        elif isinstance(raw.get("proactive_messages"), list) and raw["proactive_messages"]:
            self.proactive_messages = list(raw["proactive_messages"])
            self.proactive_pools.setdefault("playful", list(raw["proactive_messages"]))
        if raw.get("proactive_generate") is not None:
            self.proactive_generate = bool(raw["proactive_generate"])
        if raw.get("proactive_min_interval_minutes") is not None:
            self.proactive_min_interval_minutes = float(raw["proactive_min_interval_minutes"])
        if raw.get("proactive_daily_cap") is not None:
            self.proactive_daily_cap = int(raw["proactive_daily_cap"])
        if raw.get("proactive_quiet_start_hour") is not None:
            self.proactive_quiet_start_hour = int(raw["proactive_quiet_start_hour"])
        if raw.get("proactive_quiet_end_hour") is not None:
            self.proactive_quiet_end_hour = int(raw["proactive_quiet_end_hour"])
        if raw.get("proactive_dnd") is not None:
            self.proactive_dnd = bool(raw["proactive_dnd"])
        if raw.get("auto_extract_facts") is not None:
            self.auto_extract_facts = bool(raw["auto_extract_facts"])
        if raw.get("gentle_missed") is not None:
            self.gentle_missed = bool(raw["gentle_missed"])
        if raw.get("max_memory_facts") is not None:
            self.max_memory_facts = max(1, int(raw["max_memory_facts"]))
        if raw.get("voice_speed") is not None:
            self.voice_speed = float(raw["voice_speed"])
        if raw.get("audio_ducking_enabled") is not None:
            self.audio_ducking_enabled = bool(raw["audio_ducking_enabled"])
        if raw.get("audio_ducking_volume") is not None:
            self.audio_ducking_volume = float(raw["audio_ducking_volume"])
        # Feature 23b: per-sentence TTS clock cap (seconds).  A hung Fish
        # Audio read once muted her for the provider's whole 240 s read
        # timeout; both the HTTP client and the synth watchdog use this.
        if raw.get("tts_synth_timeout_s") is not None:
            self.tts_synth_timeout_s = max(10.0, float(raw["tts_synth_timeout_s"]))

        logger.info(f"Loaded personality: {self.persona['name']}")
