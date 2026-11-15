"""Feature 4: on-demand screen vision (capture only; nothing hits disk)."""
from vision.screen_capture import (                                  # noqa: F401
    Capture,
    ScreenCapture,
    VisionConfig,
    find_hotkey_collisions,
    load_config,
    monitor_work_areas,
    active_window_rect,
    cursor_pos,
    normalize_mode,
    DEFAULT_CONFIG_PATH,
)

__all__ = [
    "Capture",
    "ScreenCapture",
    "VisionConfig",
    "find_hotkey_collisions",
    "load_config",
    "monitor_work_areas",
    "active_window_rect",
    "cursor_pos",
    "normalize_mode",
    "DEFAULT_CONFIG_PATH",
]
