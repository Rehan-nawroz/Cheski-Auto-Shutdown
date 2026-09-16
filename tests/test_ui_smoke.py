"""GUI integration tests.

These drive the real :class:`CheskiApp` against a fake window source and an
injected command runner, so the entire arm -> fire -> abort path is exercised
without a desktop, a download, or a real ``shutdown.exe``.

Modal dialogs are replaced: a real ``messagebox`` would block the suite
forever, and the Tk root is withdrawn so nothing pops up on screen.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import pytest

from cheski.config import Settings
from cheski.monitor import KIND_TARGET_REATTACHED, MonitorEvent
from cheski.power import PowerController
from cheski.ui import app as app_module
from cheski.ui.app import CheskiApp
from cheski.ui.state import (
    STATE_ABORTED,
    STATE_IDLE,
    STATE_MONITORING,
    STATE_TRIGGERED,
    STATE_WARMING,
)
from cheski.windows import FakeWindowSource, WindowInfo


@dataclass
class BoxRecorder:
    """Captures messagebox calls instead of showing them."""

    calls: list[tuple[str, tuple]]

    def __init__(self) -> None:
        self.calls = []

    def _record(self, kind, args):
        self.calls.append((kind, args))

    def showerror(self, *args, **kwargs):
        self._record("error", args)

    def showinfo(self, *args, **kwargs):
        self._record("info", args)

    def showwarning(self, *args, **kwargs):
        self._record("warning", args)

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


@pytest.fixture(autouse=True)
def patched_app_environment(monkeypatch):
    """No modal dialogs, no sound, no writes to the real config file."""
    boxes = BoxRecorder()
    monkeypatch.setattr(app_module, "messagebox", boxes)
    monkeypatch.setattr(app_module, "save_settings", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.CheskiApp, "_beep", lambda self: None)
    return boxes


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless environments
        pytest.skip(f"Tk is not available here: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:  # pragma: no cover - already destroyed by the app
            pass


def spin(root, predicate, timeout: float = 8.0) -> bool:
    """Pump the Tk event loop until ``predicate`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def build_app(root, source, recorder, *, choose: bool = True, **overrides) -> CheskiApp:
    options = {
        "triggers": ("100%",),
        "dwell_checks": 1,
        "require_transition": True,
        "interval_seconds": 0.5,
        "shutdown_delay_seconds": 15,
        "dry_run": False,
    }
    options.update(overrides)
    settings = Settings(**options)
    factory = lambda **kwargs: PowerController(runner=recorder, **kwargs)  # noqa: E731
    app = CheskiApp(root, source=source, settings=settings, power_factory=factory)
    if choose:
        choose_window(app)
    return app


def choose_window(app, prefix: str = "Steam"):
    """Pick a window the way a user does: set the dropdown, raise its event."""
    label = next(label for label in app._windows if prefix in label)
    app.window_box.set(label)
    app.window_box.event_generate("<<ComboboxSelected>>")
    return app.target


def test_app_constructs_without_preselecting_a_window(tk_root, fake_source, recorder):
    """Launching must not silently adopt whatever window happens to exist."""
    app = build_app(tk_root, fake_source, recorder, choose=False)
    try:
        assert app.state == STATE_IDLE
        assert app.window_box.get() == ""
        assert app.target is None
        assert "No window selected" in app.target_info_var.get()
        assert app.dry_run_var.get() is False
        assert "shutdown /s /t 15 /c" in app.preview_var.get()
    finally:
        app._on_close()


def test_refresh_keeps_the_window_the_user_chose(tk_root, recorder):
    """Pressing Refresh must never move the target to another window."""
    source = FakeWindowSource(
        [WindowInfo(4242, "Steam - Downloading 12%", pid=1234, process="steam.exe")]
    )
    app = build_app(tk_root, source, recorder, choose=False)
    try:
        choose_window(app)
        assert app.target is not None and app.target.handle == 4242

        # A new window appears whose title sorts before "Steam ...".
        source.windows.insert(0, WindowInfo(7, "AAA Launcher", pid=99, process="aaa.exe"))
        app.refresh_windows()

        assert app.window_box.get().startswith("Steam - Downloading 12%")
        assert app.target is not None and app.target.handle == 4242
    finally:
        app._on_close()


def test_remembered_target_is_restored_on_launch(tk_root, recorder):
    source = FakeWindowSource(
        [
            WindowInfo(7, "AAA Launcher", pid=99, process="aaa.exe"),
            WindowInfo(4242, "Steam - Downloading 12%", pid=1234, process="steam.exe"),
        ]
    )
    app = build_app(
        tk_root, source, recorder, choose=False, last_process="steam.exe"
    )
    try:
        assert app.window_box.get().startswith("Steam")
        assert app.target is not None and app.target.handle == 4242
    finally:
        app._on_close()


def test_picker_is_locked_while_monitoring(tk_root, fake_source, recorder):
    """The panel and the monitor can never advertise different windows."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        assert str(app.window_box.cget("state")) == "disabled"
        assert app.refresh_button.instate(["disabled"])
        assert "Steam - Downloading 12%" in app.watched_var.get()

        # Even a forced selection change cannot move the target off the
        # window that the monitor is actually reading.
        app.window_box.event_generate("<<ComboboxSelected>>")
        assert app.worker is not None
        assert app.worker.target.handle == 4242
        assert app.window_box.get().startswith("Steam - Downloading 12%")

        app.stop_monitoring()
        assert str(app.window_box.cget("state")) == "readonly"
        assert app.watched_var.get() == ""
    finally:
        app._on_close()


def test_typed_numbers_settle_to_the_values_actually_used(tk_root, fake_source, recorder):
    """A field must never keep advertising a value the app will not use."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.delay_var.set("99999")
        app.interval_var.set("abc")
        app.dwell_var.set("0")

        assert app.start_monitoring() is True

        # 99999 -> 600, "abc" -> back to the configured 0.5s, 0 -> the 1-check floor
        assert app.delay_var.get() == "600"
        assert app.interval_var.get() == "0.5"
        assert app.dwell_var.get() == "1"
        assert "/t 600" in app.preview_var.get()
        assert app.state == STATE_WARMING
    finally:
        app._on_close()


def test_number_fields_are_wired_to_the_preview_and_settle(tk_root, fake_source, recorder):
    """Keyboard events cannot be routed to an unmapped test window, so this
    checks the wiring plus the handler those bindings call.  The end-to-end
    version (real typing into a real window) is in tests/manual/live_probe.py
    and the playtest transcript."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        for box in (app.interval_box, app.dwell_box, app.delay_box):
            assert box.bind("<KeyRelease>")
            assert box.bind("<FocusOut>")

        app.delay_var.set("300")
        app._settle_fields()  # what <FocusOut> invokes
        assert app.delay_var.get() == "300"
        assert "/t 300" in app.preview_var.get()

        app.delay_var.set("99999")
        app._settle_fields()
        assert app.delay_var.get() == "600"
        assert "/t 600" in app.preview_var.get()
    finally:
        app._on_close()


def test_dry_run_preview_uses_the_current_delay(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.delay_var.set("120")
        app._update_preview()
        assert "/t 120" in app.preview_var.get()

        app.delay_var.set("not a number")  # never trust a text field
        app._update_preview()
        assert "/t 15" in app.preview_var.get()
    finally:
        app._on_close()


def test_start_requires_a_trigger_word(tk_root, fake_source, recorder, patched_app_environment):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.trigger_var.set("   ,  ")
        assert app.start_monitoring() is False
        assert app.worker is None
        assert patched_app_environment.kinds() == ["error"]
    finally:
        app._on_close()


def test_start_requires_a_selected_window(tk_root, recorder, patched_app_environment):
    app = build_app(tk_root, FakeWindowSource([]), recorder, choose=False)
    try:
        assert app.start_monitoring() is False
        assert app.worker is None
        assert "error" in patched_app_environment.kinds()
        assert "Refresh list" in app.target_info_var.get()
    finally:
        app._on_close()


def test_arming_then_firing_then_aborting(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        assert app.state == STATE_WARMING

        # The title has no trigger, so the first poll arms the engine.
        assert spin(tk_root, lambda: app.state == STATE_MONITORING)

        fake_source.set_title(4242, "Steam - Downloading 100%")
        assert spin(tk_root, lambda: app.state == STATE_TRIGGERED)

        # 1. the shutdown was scheduled with the right countdown
        assert recorder.calls[0][:4] == ["shutdown", "/s", "/t", "15"]
        assert app.countdown is not None

        # 2. the emergency abort reaches shutdown /a
        app.abort_shutdown()
        assert recorder.calls[1] == ["shutdown", "/a"]
        assert app.state == STATE_ABORTED
        assert app.countdown is None
    finally:
        app._on_close()


def test_already_finished_download_never_fires(tk_root, fake_source, recorder):
    """The whole point of the transition guard, end to end."""
    fake_source.set_title(4242, "Steam - Downloading 100%")
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        app.dwell_var.set("1")
        assert not spin(tk_root, lambda: app.state == STATE_TRIGGERED, timeout=3.0)
        assert app.state == STATE_WARMING
        assert recorder.calls == []

        # Once the title clears, the engine arms; a later 100% then fires.
        fake_source.set_title(4242, "Steam - Downloading 40%")
        assert spin(tk_root, lambda: app.state == STATE_MONITORING)
        fake_source.set_title(4242, "Steam - Downloading 100%")
        assert spin(tk_root, lambda: app.state == STATE_TRIGGERED)
        assert recorder.calls[0][:4] == ["shutdown", "/s", "/t", "15"]
    finally:
        app._on_close()


def test_windows_sharing_a_title_and_process_stay_distinct(tk_root, recorder):
    """Two identical-looking windows must not collapse into one dropdown entry."""
    source = FakeWindowSource(
        [
            WindowInfo(11, "Settings", pid=1, process="SystemSettings.exe"),
            WindowInfo(22, "Settings", pid=1, process="SystemSettings.exe"),
        ]
    )
    app = build_app(tk_root, source, recorder, choose=False)
    try:
        labels = list(app.window_box.cget("values"))
        assert len(labels) == 2
        assert len(set(labels)) == 2
        handles = {app._windows[label].handle for label in labels}
        assert handles == {11, 22}
    finally:
        app._on_close()


def test_own_window_is_never_offered_as_a_target(tk_root, recorder):
    """Watching Cheski's own window could only ever be a silent no-op."""
    source = FakeWindowSource(
        [
            WindowInfo(1, "Cheski Auto Shutdown 1.0.0", pid=os.getpid(), process="python3.12.exe"),
            WindowInfo(2, "Steam - Downloading 12%", pid=1234, process="steam.exe"),
        ]
    )
    app = build_app(tk_root, source, recorder, choose=False)
    try:
        labels = list(app.window_box.cget("values"))
        assert len(labels) == 1
        assert labels[0].startswith("Steam - Downloading 12%")
    finally:
        app._on_close()


def test_re_attached_window_is_named_in_the_status_box(tk_root, fake_source, recorder):
    """After an automatic re-attach the status box must name the new window."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        app.handle_event(
            MonitorEvent(
                kind=KIND_TARGET_REATTACHED,
                title="Steam - Downloading 44%",
                handle=7777,
                detail="Re-attached to steam.exe (new handle 7777): Steam - Downloading 44%",
            )
        )
        assert "Steam - Downloading 44%" in app.watched_var.get()
        assert app.target is not None and app.target.handle == 7777
        assert "Re-attached" in app.log_text.get("1.0", "end")
    finally:
        app._on_close()


def test_stop_monitoring_returns_to_idle(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.start_monitoring()
        assert spin(tk_root, lambda: app.state == STATE_MONITORING)
        app.stop_monitoring()
        assert app.state == STATE_IDLE
        assert app.start_button.instate(["!disabled"])
        assert app.stop_button.instate(["disabled"])
    finally:
        app._on_close()


def test_closing_while_triggered_cancels_the_pending_shutdown(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    app.start_monitoring()
    assert spin(tk_root, lambda: app.state == STATE_MONITORING)
    fake_source.set_title(4242, "Steam - Downloading 100%")
    assert spin(tk_root, lambda: app.state == STATE_TRIGGERED)

    app._on_close()

    assert recorder.calls[-1] == ["shutdown", "/a"]


class _FakeTk:
    """Enough of a Tk root for ``main()`` to run without a window."""

    def mainloop(self) -> None:
        return None


@pytest.mark.skipif(sys.platform != "win32", reason="the app is Windows-only")
def test_first_launch_starts_in_dry_run(tmp_path, monkeypatch):
    """A fresh install rehearses instead of really powering off."""
    captured: dict = {}

    monkeypatch.setattr(app_module, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(app_module, "load_settings", lambda: Settings(dry_run=False))
    monkeypatch.setattr(app_module, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(app_module.tk, "Tk", _FakeTk)

    class FakeApp:
        def __init__(self, root, *, settings, force_dry_run=False):
            captured["settings"] = settings
            captured["force_dry_run"] = force_dry_run

    monkeypatch.setattr(app_module, "CheskiApp", FakeApp)

    assert app_module.main([]) == 0
    assert captured["settings"].dry_run is True
    assert captured["force_dry_run"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="the app is Windows-only")
def test_later_launches_do_not_force_dry_run(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    captured: dict = {}

    monkeypatch.setattr(app_module, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(app_module, "load_settings", lambda: Settings(dry_run=False))
    monkeypatch.setattr(app_module, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(app_module.tk, "Tk", _FakeTk)
    monkeypatch.setattr(
        app_module,
        "CheskiApp",
        lambda root, *, settings, force_dry_run=False: captured.update(settings=settings),
    )

    assert app_module.main([]) == 0
    assert captured["settings"].dry_run is False


def test_close_stops_the_worker_thread(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    app.start_monitoring()
    assert spin(tk_root, lambda: app.state == STATE_MONITORING)
    worker = app.worker

    app._on_close()

    assert worker is not None
    assert not worker.is_alive()
