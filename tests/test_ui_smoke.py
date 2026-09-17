"""GUI integration tests — Terminal Precision surface.

These drive the real :class:`CheskiApp` against a fake window source and an
injected command runner, so the entire arm -> fire -> abort path is exercised
without a desktop, a download, or a real ``shutdown.exe``.

The new surface's seams: ``app.picker`` (process rows), ``app.chips`` (trigger
tags), ``app.segmented``/``app.case_toggle``/``app.transition_toggle``,
``app.interval_card``/``app.dwell_card``/``app.delay_card``,
``app.dry_banner``, ``app.ring``, ``app.log_panel``, ``app.abort_pill``.
Modal dialogs are still replaced (a real ``messagebox`` would block), and the
Tk root is withdrawn so nothing pops up on screen.
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


def rows_of(app) -> list:
    return list(app.picker._rows)


def choose_window(app, prefix: str = "Steam"):
    """Pick a window the way a user does: select a picker row by name."""
    row = next(row for row in app.picker._rows if prefix in row.display_name)
    app.picker.select_row(row)
    return app.target


def test_app_constructs_without_preselecting_a_window(tk_root, fake_source, recorder):
    """Launching must not silently adopt whatever window happens to exist."""
    app = build_app(tk_root, fake_source, recorder, choose=False)
    try:
        assert app.state == STATE_IDLE
        assert app.picker.selected_row() is None
        assert app.target is None
        assert app.dry_banner.value is False
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

        assert app.picker.selected_row() is not None
        assert app.picker.selected_row().info.handle == 4242
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
        assert app.target is not None and app.target.handle == 4242
        assert app.picker.selected_row() is not None
        assert app.picker.selected_row().info.handle == 4242
    finally:
        app._on_close()


def test_picker_is_locked_while_monitoring(tk_root, fake_source, recorder):
    """The panel and the monitor can never advertise different windows."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        assert str(app.picker.search_entry.cget("state")) == "disabled"
        assert "Steam - Downloading 12%" in app.watched_label.cget("text")

        app.stop_monitoring()
        assert str(app.picker.search_entry.cget("state")) == "normal"
    finally:
        app._on_close()


def test_metric_cards_clamp_out_of_range_values(tk_root, fake_source, recorder):
    """Steppers and stored settings must stay inside the documented ranges."""
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.delay_card.set_value(99999, animate=False)
        assert app.delay_card.get() == 600
        app.delay_card.set_value(1, animate=False)
        assert app.delay_card.get() == 15
        app.dwell_card.set_value(0, animate=False)
        assert app.dwell_card.get() == 1
        app.interval_card.set_value(999, animate=False)
        assert app.interval_card.get() == 60
        assert app.delay_card.warn_below == 30
    finally:
        app._on_close()


def test_trigger_chips_add_and_remove(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.chips.entry.delete(0, "end")
        app.chips.entry.insert(0, "done")
        app.chips._commit()
        assert "done" in app.chips.tags()
        assert "100%" in app.chips.tags()
        # duplicates collapse case-insensitively
        app.chips.entry.delete(0, "end")
        app.chips.entry.insert(0, "DONE")
        app.chips._commit()
        assert app.chips.tags().count("done") == 1
        app.chips._remove("done")
        assert "done" not in app.chips.tags()
    finally:
        app._on_close()


def test_segmented_and_toggles_follow_the_settings(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.segmented.set_value("all", notify=True)
        assert app.segmented.value == "all"
        app.case_toggle.set_value(True, notify=True)
        assert app.case_toggle.value is True
        app.transition_toggle.set_value(False, notify=True)
        assert app.transition_toggle.value is False
        assert app.settings.match_mode == "all"
        assert app.settings.case_sensitive is True
        assert app.settings.require_transition is False
    finally:
        app._on_close()


def test_dry_run_banner_toggles_and_shows_the_badge(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.dry_banner.value is False
        assert app.dry_banner.badge.cget("text") == "LIVE"
        app.dry_banner.set_value(True, notify=True)
        assert app.dry_banner.badge.cget("text") == "SAFE"
        assert app.dry_run is True
    finally:
        app._on_close()


def test_start_requires_a_trigger_word(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.chips.set_tags([])
        assert app.start_monitoring() is False
        assert app.worker is None
        assert app.state == STATE_IDLE
    finally:
        app._on_close()


def test_start_requires_a_selected_window(tk_root, recorder):
    app = build_app(tk_root, FakeWindowSource([]), recorder, choose=False)
    try:
        assert app.start_monitoring() is False
        assert app.worker is None
        assert app.state == STATE_IDLE
    finally:
        app._on_close()


def test_arming_then_firing_then_aborting(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
        assert app.state == STATE_WARMING
        assert app.ring.state == "active"

        # The title has no trigger, so the first poll arms the engine.
        assert spin(tk_root, lambda: app.state == STATE_MONITORING)

        fake_source.set_title(4242, "Steam - Downloading 100%")
        assert spin(tk_root, lambda: app.state == STATE_TRIGGERED)
        assert app.ring.state == "executing"

        # 1. the shutdown was scheduled with the right countdown
        assert recorder.calls[0][:4] == ["shutdown", "/s", "/t", "15"]
        assert app._seconds_left >= 1
        assert app.countdown_label.winfo_ismapped() or app.countdown_label.cget("text")

        # 2. the emergency abort reaches shutdown /a
        app.abort_shutdown()
        assert recorder.calls[1] == ["shutdown", "/a"]
        assert app.state == STATE_ABORTED
        assert app.ring.state == "idle"
    finally:
        app._on_close()


def test_already_finished_download_never_fires(tk_root, fake_source, recorder):
    """The whole point of the transition guard, end to end."""
    fake_source.set_title(4242, "Steam - Downloading 100%")
    app = build_app(tk_root, fake_source, recorder)
    try:
        assert app.start_monitoring() is True
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


def test_similar_windows_stay_distinct_rows(tk_root, recorder):
    """Two identical-looking windows must appear as two distinct rows."""
    source = FakeWindowSource(
        [
            WindowInfo(11, "Settings", pid=1, process="SystemSettings.exe"),
            WindowInfo(22, "Settings", pid=1, process="SystemSettings.exe"),
        ]
    )
    app = build_app(tk_root, source, recorder, choose=False)
    try:
        rows = rows_of(app)
        assert len(rows) == 2
        assert {row.info.handle for row in rows} == {11, 22}
        # rows keep distinct identities even with equal display names
        assert len({row.display_name for row in rows}) <= 2
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
        rows = rows_of(app)
        assert len(rows) == 1
        assert rows[0].info.handle == 2
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
        assert "Steam - Downloading 44%" in app.watched_label.cget("text")
        assert app.target is not None and app.target.handle == 7777
        assert "Re-attached" in app.log_panel.text.get("1.0", "end")
    finally:
        app._on_close()


def test_stop_monitoring_returns_to_idle(tk_root, fake_source, recorder):
    app = build_app(tk_root, fake_source, recorder)
    try:
        app.start_monitoring()
        assert spin(tk_root, lambda: app.state == STATE_MONITORING)
        app.stop_monitoring()
        assert app.state == STATE_IDLE
        assert str(app.start_button.cget("state")) == "normal"
        assert str(app.stop_button.cget("state")) == "disabled"
        assert app.ring.state == "idle"
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
