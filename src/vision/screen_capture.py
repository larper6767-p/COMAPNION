"""Feature 4: on-demand screen capture.

Grabs a single frame only when explicitly asked, encodes it to PNG **in memory**
(never written to disk), and keeps it for a short TTL so it can be attached to
the next turn.

Scope rules (project guardrails)
--------------------------------
* Capture the **work area** of the monitor containing the *active window* —
  taskbar excluded — never the merged virtual desktop.
* ``active_window`` mode intersects the window rect with that monitor's work area.
* Nothing is captured unless :meth:`ScreenCapture.capture` is called.

The OS bits (mss, the foreground-window rect, per-monitor work areas) sit behind
a small ``Platform`` seam so the whole thing is testable without a display.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "vision.yaml"

# ── Config defaults (mirror config/vision.yaml) ───────────────────────────────
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "hotkey": "ctrl+alt+s",     # moved off ctrl+shift+s (used by scene1_hotkey)
    "mode": "monitor",          # "monitor" | "active_window"
    "ttl_seconds": 30.0,
    "vision_model": "",         # "" = reuse the normal model
    "max_width": 1280,          # downscale before sending (0 = no resize)
    "indicator": True,
}

# Human aliases so older docs/spec names keep working.
MODE_ALIASES = {
    "full_screen": "monitor",
    "screen": "monitor",
    "monitor": "monitor",
    "window": "active_window",
    "active_window": "active_window",
}

Rect = Tuple[int, int, int, int]     # left, top, width, height


def normalize_mode(mode: Optional[str]) -> str:
    """Map a config/spec mode name to ``"monitor"`` or ``"active_window"``."""
    m = str(mode or "").strip().lower()
    return MODE_ALIASES.get(m, "monitor")


@dataclass(frozen=True)
class VisionConfig:
    enabled: bool = False
    hotkey: str = "ctrl+alt+s"
    mode: str = "monitor"
    ttl_seconds: float = 30.0
    vision_model: str = ""
    max_width: int = 1280
    indicator: bool = True

    @property
    def resolved_mode(self) -> str:
        return normalize_mode(self.mode)


def load_config(path: Optional[Path] = None) -> VisionConfig:
    """Load ``config/vision.yaml`` over the built-in defaults.

    A missing/empty/malformed file yields the defaults (vision off) — a bad
    config can never break startup or turn vision on by accident.
    """
    p = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data: Any = None
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except Exception as e:
        logger.warning("vision.yaml unreadable; using defaults (disabled): %s", e)
        data = None

    if not isinstance(data, dict):
        return VisionConfig()

    d = dict(DEFAULTS)
    for k in ("enabled", "indicator"):
        if k in data:
            d[k] = bool(data[k])
    for k in ("hotkey", "vision_model", "mode"):
        if k in data and data[k] is not None:
            d[k] = str(data[k])
    for k in ("ttl_seconds", "max_width"):
        if k in data and data[k] is not None:
            try:
                d[k] = float(data[k]) if k == "ttl_seconds" else int(data[k])
            except (TypeError, ValueError):
                pass

    return VisionConfig(
        enabled=bool(d["enabled"]),
        hotkey=str(d["hotkey"]),
        mode=normalize_mode(d["mode"]),
        ttl_seconds=float(d["ttl_seconds"]),
        vision_model=str(d["vision_model"]).strip(),
        max_width=int(d["max_width"]),
        indicator=bool(d["indicator"]),
    )


# ── OS probing (Windows; degrade gracefully elsewhere) ────────────────────────

def active_window_rect() -> Optional[Rect]:
    """Foreground window as ``(left, top, width, height)``, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w = int(rect.right - rect.left)
        h = int(rect.bottom - rect.top)
        if w <= 0 or h <= 0:
            return None
        return (int(rect.left), int(rect.top), w, h)
    except Exception:
        return None


def cursor_pos() -> Optional[Tuple[int, int]]:
    """Current cursor position ``(x, y)`` in virtual-screen coords, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        pt = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return None
        return (int(pt.x), int(pt.y))
    except Exception:
        return None


def _enum_monitor_work_areas() -> List[Dict[str, int]]:
    """Per-monitor **work areas** (taskbar excluded) via ``GetMonitorInfoW``."""
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        out: List[Dict[str, int]] = []
        proc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_double)

        def _cb(hmon, hdc, lprc, data):  # noqa: ANN001
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                out.append({"left": int(r.left), "top": int(r.top),
                            "width": int(r.right - r.left),
                            "height": int(r.bottom - r.top)})
            return 1

        user32.EnumDisplayMonitors(None, None, proc(_cb), 0)
        return out
    except Exception:
        return []


def monitor_work_areas() -> List[Dict[str, int]]:
    """Work areas of all monitors; falls back to mss monitor rects."""
    areas = _enum_monitor_work_areas()
    if areas:
        return areas
    try:
        import mss

        with mss.mss() as sct:
            mons = [dict(m) for m in sct.monitors[1:]]
            return mons or [dict(sct.monitors[0])]
    except Exception:
        return []


# ── Pure geometry helpers ─────────────────────────────────────────────────────

def _overlap_area(rect: Rect, area: Dict[str, int]) -> int:
    left, top, w, h = rect
    ox = max(0, min(left + w, area["left"] + area["width"]) - max(left, area["left"]))
    oy = max(0, min(top + h, area["top"] + area["height"]) - max(top, area["top"]))
    return ox * oy


def best_monitor_index(areas: List[Dict[str, int]], rect: Rect) -> int:
    """Index of the monitor with the largest overlap with ``rect`` (0 fallback)."""
    best_i, best_area = 0, -1
    for i, area in enumerate(areas):
        a = _overlap_area(rect, area)
        if a > best_area:
            best_i, best_area = i, a
    return best_i


def monitor_index_for_point(areas: List[Dict[str, int]], x: int, y: int) -> int:
    """Index of the monitor containing ``(x, y)``, else 0."""
    for i, area in enumerate(areas):
        if (area["left"] <= x < area["left"] + area["width"]
                and area["top"] <= y < area["top"] + area["height"]):
            return i
    return 0


def intersect_rect(rect: Rect, area: Dict[str, int]) -> Dict[str, int]:
    """Clip ``rect`` to ``area`` and return an mss-style grab region."""
    left, top, w, h = rect
    x0 = max(left, area["left"])
    y0 = max(top, area["top"])
    x1 = min(left + w, area["left"] + area["width"])
    y1 = min(top + h, area["top"] + area["height"])
    if x1 <= x0 or y1 <= y0:                      # window off this monitor
        return dict(area)
    return {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}


def encode_png(raw: Any, max_width: int = 0) -> Tuple[bytes, int, int]:
    """Encode an mss-style grab (``.rgb`` + ``.size``) to PNG bytes, in memory."""
    from PIL import Image

    w, h = raw.size
    img = Image.frombytes("RGB", (w, h), raw.rgb)
    if max_width and w > max_width:
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:                       # older Pillow
            resample = Image.LANCZOS
        new_h = max(1, int(round(h * max_width / w)))
        img = img.resize((max_width, new_h), resample)
        w, h = img.size
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), w, h


# ── Platform seam (injectable for tests) ──────────────────────────────────────

class Platform:
    """Real OS access; tests substitute a fake with the same methods."""

    def session(self) -> Any:
        import mss

        return mss.mss()

    def work_areas(self) -> List[Dict[str, int]]:
        return monitor_work_areas()

    def active_window_rect(self) -> Optional[Rect]:
        return active_window_rect()

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        return cursor_pos()


# ── Capture ───────────────────────────────────────────────────────────────────

@dataclass
class Capture:
    """A single in-memory screenshot."""

    png: bytes
    width: int
    height: int
    mode: str
    monitor: int
    captured_at: float

    def age(self, now: Optional[float] = None) -> float:
        return (now if now is not None else time.monotonic()) - self.captured_at


class ScreenCapture:
    """Grabs one frame on request and remembers it for a short TTL."""

    def __init__(
        self,
        cfg: Optional[VisionConfig] = None,
        *,
        platform: Optional[Platform] = None,
        encoder: Callable[[Any, int], Tuple[bytes, int, int]] = encode_png,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg or VisionConfig()
        self._platform = platform or Platform()
        self._encode = encoder
        self._clock = clock
        self._last: Optional[Capture] = None

    # -- state -----------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    @property
    def mode(self) -> str:
        return self.cfg.resolved_mode

    def pending(self) -> Optional[Capture]:
        """The armed capture if still within TTL, else None."""
        cap = self._last
        if cap is None:
            return None
        if cap.age(self._clock()) > float(self.cfg.ttl_seconds):
            self._last = None
            return None
        return cap

    def take(self) -> Optional[Capture]:
        """Return and disarm the pending capture (used when a turn consumes it)."""
        cap = self.pending()
        if cap is not None:
            self._last = None
        return cap

    def clear(self) -> None:
        self._last = None

    # -- capture ---------------------------------------------------------------
    def target_region(self, mode: Optional[str] = None) -> Tuple[Optional[Dict[str, int]], int]:
        """Resolve the grab region for ``mode`` (pure w.r.t. the platform seam).

        Returns ``(region, monitor_index)``; region is None when no monitor is
        known (caller then reports failure).
        """
        mode = normalize_mode(mode or self.mode)
        areas = self._platform.work_areas()
        if not areas:
            return None, 0

        rect = self._platform.active_window_rect()
        if rect is not None:
            idx = best_monitor_index(areas, rect)
        else:
            pos = self._platform.cursor_pos()
            idx = monitor_index_for_point(areas, pos[0], pos[1]) if pos else 0

        area = areas[idx]
        if mode == "active_window" and rect is not None:
            return intersect_rect(rect, area), idx
        return dict(area), idx

    def capture(self, mode: Optional[str] = None) -> Optional[Capture]:
        """Grab one frame now. Returns None when disabled or on failure."""
        if not self.enabled:
            logger.debug("[vision] capture ignored (disabled)")
            return None

        mode = normalize_mode(mode or self.mode)
        region, mon_idx = self.target_region(mode)
        if region is None or region.get("width", 0) <= 0 or region.get("height", 0) <= 0:
            logger.warning("[vision] no usable monitor/region to capture")
            return None

        session = None
        try:
            session = self._platform.session()
            raw = session.grab(region)
            png, w, h = self._encode(raw, int(self.cfg.max_width))
        except Exception as e:
            logger.warning("[vision] capture failed: %s", e)
            return None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

        cap = Capture(png=png, width=w, height=h, mode=mode,
                      monitor=mon_idx, captured_at=self._clock())
        self._last = cap
        # Exactly one log line per capture (guardrail).
        logger.info("[vision] captured %s monitor=%d %dx%d (%d bytes)",
                    mode, mon_idx, w, h, len(png))
        return cap


# ── Startup hotkey-collision check ────────────────────────────────────────────

def find_hotkey_collisions(hotkeys: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """Find labels that share a hotkey.

    ``hotkeys`` maps a human label ("Turn", "Scene1", "Screen vision") to a
    hotkey string. Returns ``[(label_a, label_b, hotkey), ...]`` for collisions;
    comparison is case- and space-insensitive; blanks are ignored.
    """
    seen: Dict[str, str] = {}
    out: List[Tuple[str, str, str]] = []
    for label, hk in hotkeys.items():
        key = str(hk or "").strip().lower().replace(" ", "")
        if not key:
            continue
        if key in seen:
            out.append((seen[key], label, str(hk)))
        else:
            seen[key] = label
    return out


__all__ = [
    "Capture",
    "ScreenCapture",
    "VisionConfig",
    "Platform",
    "find_hotkey_collisions",
    "load_config",
    "normalize_mode",
    "monitor_work_areas",
    "active_window_rect",
    "cursor_pos",
    "best_monitor_index",
    "monitor_index_for_point",
    "intersect_rect",
    "encode_png",
    "DEFAULT_CONFIG_PATH",
    "DEFAULTS",
]
