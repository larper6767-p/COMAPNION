"""Feature 4 tests: on-demand screen vision.

Everything is mocked: no display, no mss, no network, no API keys. The OS seam
(``Platform``) and the PNG encoder are injected, so the capture flow, scope
rules, TTL, message attachment and hotkey-collision logic are all exercised
purely.
"""
import base64
import builtins
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# test_headless.py stubs core.*/cerebras/dotenv at import; drop the fakes so the
# real modules load when both files run in one session.
for _m in ("core", "core.model", "cerebras", "cerebras.cloud", "cerebras.cloud.sdk",
           "dotenv", "vision", "vision.screen_capture", "vision.attach"):
    sys.modules.pop(_m, None)

from vision.screen_capture import (                                  # noqa: E402
    ScreenCapture, VisionConfig, Platform,
    find_hotkey_collisions, load_config, normalize_mode,
    best_monitor_index, monitor_index_for_point, intersect_rect, encode_png,
)
from vision.attach import attach_image, to_b64                       # noqa: E402

MISSING = Path("does/not/exist/vision.yaml")

# Two monitors; work areas (taskbar already excluded).
MON_A = {"left": 0, "top": 0, "width": 1920, "height": 1040}
MON_B = {"left": 1920, "top": 0, "width": 1280, "height": 984}


# ── fakes ─────────────────────────────────────────────────────────────────────

class _Raw:
    def __init__(self, w, h):
        self.size = (w, h)
        self.rgb = bytes([1, 2, 3]) * (w * h)


class _FakeSession:
    def __init__(self, raw, boom=False):
        self.raw = raw
        self.boom = boom
        self.grabbed = []
        self.closed = False

    def grab(self, region):
        if self.boom:
            raise RuntimeError("no display")
        self.grabbed.append(dict(region))
        return self.raw

    def close(self):
        self.closed = True


class _FakePlatform:
    def __init__(self, areas, active=None, cursor=None, session=None):
        self._areas, self._active, self._cursor = areas, active, cursor
        self._session = session

    def work_areas(self):
        return self._areas

    def active_window_rect(self):
        return self._active

    def cursor_pos(self):
        return self._cursor

    def session(self):
        return self._session


def _encoder(raw, max_width):
    return b"PNGDATA", raw.size[0], raw.size[1]


def _capture(areas=(), active=None, cursor=None, *, mode="monitor",
             enabled=True, ttl=30.0, raw=None, boom=False, clock=None,
             encoder=_encoder):
    sess = _FakeSession(raw or _Raw(640, 480), boom=boom)
    plat = _FakePlatform(list(areas), active, cursor, sess)
    cfg = VisionConfig(enabled=enabled, mode=mode, ttl_seconds=ttl)
    sc = ScreenCapture(cfg, platform=plat, encoder=encoder,
                       clock=clock or (lambda: 1000.0))
    return sc, sess


# ── config ────────────────────────────────────────────────────────────────────

def test_missing_config_defaults_to_disabled():
    cfg = load_config(MISSING)
    assert cfg.enabled is False
    assert cfg.hotkey == "ctrl+alt+s"
    assert cfg.resolved_mode == "monitor"
    assert cfg.vision_model == ""


def test_config_overrides(tmp_path):
    p = tmp_path / "vision.yaml"
    p.write_text(
        "enabled: true\nhotkey: 'ctrl+alt+v'\nmode: active_window\n"
        "ttl_seconds: 5\nvision_model: llava\nmax_width: 800\nindicator: false\n",
        encoding="utf-8")
    cfg = load_config(p)
    assert cfg.enabled is True
    assert cfg.hotkey == "ctrl+alt+v"
    assert cfg.resolved_mode == "active_window"
    assert cfg.ttl_seconds == 5.0
    assert cfg.vision_model == "llava"
    assert cfg.max_width == 800
    assert cfg.indicator is False


def test_malformed_config_falls_back_to_disabled(tmp_path):
    p = tmp_path / "vision.yaml"
    p.write_text("enabled: [oops\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.enabled is False


def test_mode_aliases():
    assert normalize_mode("full_screen") == "monitor"
    assert normalize_mode("screen") == "monitor"
    assert normalize_mode("window") == "active_window"
    assert normalize_mode("ACTIVE_WINDOW") == "active_window"
    assert normalize_mode(None) == "monitor"
    assert VisionConfig(mode="full_screen").resolved_mode == "monitor"


def test_shipped_config_is_disabled_and_valid():
    from vision.screen_capture import DEFAULT_CONFIG_PATH
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.enabled is False                       # opt-in feature
    assert cfg.hotkey.lower() != "ctrl+shift+s"       # must not collide with scene1
    assert cfg.resolved_mode in ("monitor", "active_window")


# ── geometry (pure) ───────────────────────────────────────────────────────────

def test_best_monitor_index_picks_overlapping_monitor():
    assert best_monitor_index([MON_A, MON_B], (2000, 50, 600, 400)) == 1
    assert best_monitor_index([MON_A, MON_B], (100, 100, 600, 400)) == 0


def test_best_monitor_index_handles_partial_with_largest_overlap():
    # Straddling the boundary: most of it is on monitor A.
    assert best_monitor_index([MON_A, MON_B], (1600, 100, 400, 400)) == 0
    # ...and mostly on B when it leans the other way.
    assert best_monitor_index([MON_A, MON_B], (1900, 100, 400, 400)) == 1


def test_monitor_index_for_point():
    assert monitor_index_for_point([MON_A, MON_B], 500, 500) == 0
    assert monitor_index_for_point([MON_A, MON_B], 2500, 500) == 1
    assert monitor_index_for_point([MON_A, MON_B], -9999, -9999) == 0   # fallback


def test_intersect_rect_clips_to_work_area():
    # Window hanging off the top-left of monitor A.
    assert intersect_rect((-100, -50, 400, 300), MON_A) == \
        {"left": 0, "top": 0, "width": 300, "height": 250}


def test_intersect_rect_window_off_monitor_returns_whole_area():
    assert intersect_rect((-500, -500, 100, 100), MON_A) == dict(MON_A)


# ── hotkey collisions ─────────────────────────────────────────────────────────

def test_no_collisions():
    assert find_hotkey_collisions(
        {"Turn": "f9", "Scene1": "ctrl+shift+s", "Screen vision": "ctrl+alt+s"}) == []


def test_collision_detected():
    assert find_hotkey_collisions({"Turn": "F9", "Vision": "f9"}) == \
        [("Turn", "Vision", "f9")]


def test_collision_ignores_case_and_spaces_and_blanks():
    assert len(find_hotkey_collisions(
        {"Turn": " ctrl + shift + s ", "Scene1": "ctrl+shift+s"})) == 1
    assert find_hotkey_collisions({"Turn": "", "Scene1": ""}) == []


# ── capture: scope rules ──────────────────────────────────────────────────────

def test_disabled_capture_does_nothing():
    sc, sess = _capture([MON_A, MON_B], active=(2000, 0, 100, 100), enabled=False)
    assert sc.capture() is None
    assert sess.grabbed == []
    assert sc.pending() is None


def test_monitor_mode_uses_active_window_monitor_not_virtual_desktop():
    sc, sess = _capture([MON_A, MON_B], active=(2000, 50, 600, 400))
    cap = sc.capture()
    assert sess.grabbed[0] == MON_B      # the work area of monitor B, not the union
    assert cap.monitor == 1
    assert cap.mode == "monitor"
    assert cap.png == b"PNGDATA"


def test_active_window_mode_clips_window_to_monitor():
    # Window x 2500..3300 but monitor B ends at x=3200 -> clipped to width 700.
    sc, sess = _capture([MON_A, MON_B], active=(2500, 30, 800, 500),
                        mode="active_window")
    cap = sc.capture()
    assert sess.grabbed[0] == {"left": 2500, "top": 30, "width": 700, "height": 500}
    assert cap.mode == "active_window"


def test_active_window_hanging_off_monitor_is_clipped():
    sc, sess = _capture([MON_A], active=(-100, -50, 400, 300), mode="active_window")
    sc.capture()
    assert sess.grabbed[0] == {"left": 0, "top": 0, "width": 300, "height": 250}


def test_falls_back_to_cursor_monitor_when_no_active_window():
    sc, sess = _capture([MON_A, MON_B], active=None, cursor=(2000, 500))
    cap = sc.capture()
    assert cap.monitor == 1
    assert sess.grabbed[0] == MON_B


def test_no_monitors_reports_failure():
    sc, sess = _capture([], active=(0, 0, 10, 10))
    assert sc.capture() is None


def test_grab_failure_is_handled():
    sc, sess = _capture([MON_A], active=(10, 10, 100, 100), boom=True)
    assert sc.capture() is None
    assert sess.closed is True            # session still closed


def test_session_is_closed_after_capture():
    sc, sess = _capture([MON_A], active=(10, 10, 100, 100))
    sc.capture()
    assert sess.closed is True


# ── capture: TTL / arming ─────────────────────────────────────────────────────

def test_pending_respects_ttl():
    now = [100.0]
    sc, _ = _capture([MON_A], active=(10, 10, 100, 100), ttl=30.0,
                     clock=lambda: now[0])
    cap = sc.capture()
    assert sc.pending() is cap
    now[0] = 100.0 + 31.0
    assert sc.pending() is None
    assert sc.pending() is None            # stays cleared


def test_take_returns_and_disarms():
    sc, _ = _capture([MON_A], active=(10, 10, 100, 100))
    cap = sc.capture()
    assert sc.take() is cap
    assert sc.pending() is None


def test_clear():
    sc, _ = _capture([MON_A], active=(10, 10, 100, 100))
    sc.capture()
    sc.clear()
    assert sc.pending() is None


def test_capture_is_never_written_to_disk(monkeypatch):
    writes = []
    real_open = builtins.open

    def _spy(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append((str(file), mode))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _spy)
    sc, _ = _capture([MON_A], active=(10, 10, 100, 100))
    assert sc.capture() is not None
    assert writes == []


# ── real PNG encoding (no display needed) ─────────────────────────────────────

def test_encode_png_produces_png_bytes():
    png, w, h = encode_png(_Raw(20, 20), max_width=0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (w, h) == (20, 20)


def test_encode_png_downscales():
    png, w, h = encode_png(_Raw(20, 20), max_width=10)
    assert (w, h) == (10, 10)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ── message attachment (pure) ─────────────────────────────────────────────────

def _msgs():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what's on my screen?"},
    ]


def test_attach_cerebras_builds_image_url_parts():
    msgs = _msgs()
    out = attach_image(msgs, b"\x89PNG", use_cerebras=True)
    # caller's data is untouched
    assert msgs[1]["content"] == "what's on my screen?"
    assert out[0] == msgs[0]
    parts = out[1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "text" and "what's on my screen?" in parts[0]["text"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert to_b64(b"\x89PNG") in parts[1]["image_url"]["url"]


def test_attach_ollama_uses_images_list():
    out = attach_image(_msgs(), b"\x89PNG", use_cerebras=False)
    assert isinstance(out[1]["content"], str)
    assert out[1]["images"] == [base64.b64encode(b"\x89PNG").decode("ascii")]


def test_attach_without_user_message_appends_one():
    out = attach_image([{"role": "system", "content": "s"}], b"X", use_cerebras=False)
    assert out[-1]["role"] == "user"
    assert out[-1]["images"] == [base64.b64encode(b"X").decode("ascii")]


def test_attach_preserves_existing_images():
    msgs = [{"role": "user", "content": "hi", "images": ["OLD"]}]
    out = attach_image(msgs, b"NEW", use_cerebras=False)
    assert out[0]["images"] == ["OLD", base64.b64encode(b"NEW").decode("ascii")]


# ── model integration (no network) ────────────────────────────────────────────

def test_model_stream_chat_attaches_image_and_uses_vision_model(monkeypatch):
    from core.model import ModelInterface

    m = object.__new__(ModelInterface)
    m.model_name = "base-model"
    m.temperature = 0.7
    m.use_cerebras = True
    m.max_tokens = 100
    m.repeat_penalty = 1.1
    m.stop = []
    m.vision_model = "vision-model"
    m.total_tokens = {"prompt": 0, "completion": 0}

    seen = {}

    def _fake(self, messages, temp, model=None):
        seen["messages"] = messages
        seen["model"] = model
        yield {"message": {"content": "ok"}, "done": True}

    monkeypatch.setattr(ModelInterface, "_stream_cerebras", _fake)

    chunks = list(m.stream_chat([{"role": "user", "content": "look"}],
                                image_png=b"\x89PNG"))
    assert chunks[0]["message"]["content"] == "ok"
    assert seen["model"] == "vision-model"
    parts = seen["messages"][0]["content"]
    assert any(p.get("type") == "image_url" for p in parts)


def test_model_stream_chat_without_image_is_unchanged(monkeypatch):
    from core.model import ModelInterface

    m = object.__new__(ModelInterface)
    m.model_name = "base-model"
    m.temperature = 0.7
    m.use_cerebras = True
    m.max_tokens = 100
    m.repeat_penalty = 1.1
    m.stop = []
    m.vision_model = ""
    m.total_tokens = {"prompt": 0, "completion": 0}

    seen = {}

    def _fake(self, messages, temp, model=None):
        seen["messages"] = messages
        seen["model"] = model
        yield {"message": {"content": "ok"}, "done": True}

    monkeypatch.setattr(ModelInterface, "_stream_cerebras", _fake)

    msgs = [{"role": "user", "content": "hi"}]
    list(m.stream_chat(msgs))
    assert seen["messages"] == msgs          # untouched, no attach
    assert seen["model"] is None             # normal model


def test_platform_seam_defaults_exist():
    # The real Platform exposes the same methods the fake does.
    for name in ("session", "work_areas", "active_window_rect", "cursor_pos"):
        assert hasattr(Platform, name)


def test_app_screen_capture_uses_the_defined_vision_constant():
    src = (Path(__file__).parent.parent / "src" / "app.py").read_text(
        encoding="utf-8")
    assert "_VISION_CFG_FILE" not in src
    assert "load_config(VISION_CFG_FILE)" in src
