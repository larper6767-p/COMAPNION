"""Config-driven emotion/expression mapping (Feature 3)."""
from emotion.controller import (          # noqa: F401
    EmotionsConfig,
    load_config,
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENABLED,
    DEFAULT_EXPRESSIONS,
    DEFAULT_MOOD_EXPRESSIONS,
)

__all__ = [
    "EmotionsConfig",
    "load_config",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENABLED",
    "DEFAULT_EXPRESSIONS",
    "DEFAULT_MOOD_EXPRESSIONS",
]
