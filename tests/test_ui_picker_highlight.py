"""Picker selection highlight -- regression tests for the dead highlight bug.

The row tint used to be computed from a lookup table that was never built, so
no row ever showed the selection colour no matter what the user clicked.
These tests drive the real :class:`~cheski.ui.widgets.ProcessPicker` through
the same entry points a user hits (click-preview, double-click select,
programmatic restore) and assert on the actual painted background of the row
frames, so the highlight cannot silently die again.
"""

from __future__ import annotations

import time

import pytest

from cheski.config import Settings
from cheski.ui import app as app_module
from cheski.ui import widgets
from cheski.ui.app import CheskiApp
from cheski.windows import FakeWindowSource, WindowInfo


@pytest.fixture(autouse=True)
def quiet_app(monkeypatch):
    """No config writes, no beeps from constructing the app."""
    monkeypatch.setattr(app_module, "save_settings", lambda *args, **kwargs: True)
    monkeypatch.setattr(app_module.CheskiApp, "_beep", lambda self: None)


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
        try:
            root.update()
            if predicate():
                return True
        except Exception:  # pragma: no cover - widget torn down mid-spin
            return False
        time.sleep(0.02)
    return False


def make_app(root, source) -> CheskiApp:
    settings = Settings(
        triggers=("100%",),
        dwell_checks=1,
        require_transition=True,
        interval_seconds=0.5,
        shutdown_delay_seconds=15,
        dry_run=False,
    )
    return CheskiApp(root, source=source, settings=settings, power_factory=lambda **kwargs: None)


def test_selection_tint_is_actually_visible():
    """The highlight must be a different colour than an idle row's."""
    assert widgets.SELECTED_BG != widgets.ROW_BG


def test_selected_row_paints_the_highlight_tint(tk_root):
    """Selecting a row tints it and only it -- and the tint sticks."""
    source = FakeWindowSource(
        [
            WindowInfo(11, "AAA Launcher", pid=99, process="aaa.exe"),
            WindowInfo(4242, "Steam - Downloading 12%", pid=1234, process="steam.exe"),
        ]
    )
    app = make_app(tk_root, source)
    try:
        picker = app.picker
        steam = next(row for row in picker._rows if "Steam" in row.display_name)
        other = next(row for row in picker._rows if "AAA" in row.display_name)
        steam_frame = picker.row_frame(steam)
        other_frame = picker.row_frame(other)
        assert steam_frame is not None and other_frame is not None

        # Let the load-in fade settle so both rows sit at their idle colour.
        assert spin(tk_root, lambda: str(steam_frame.cget("bg")) == str(widgets.ROW_BG))

        picker.select_row(steam)
        assert str(steam_frame.cget("bg")) == str(widgets.SELECTED_BG)
        assert str(other_frame.cget("bg")) == str(widgets.ROW_BG)
        assert str(steam_frame.cget("bg")) != str(other_frame.cget("bg"))

        # The highlight must survive a rebuild (search filter, refresh, ...).
        picker.search_var.set("steam")
        rebuilt = picker.row_frame(steam)
        assert rebuilt is not None and rebuilt is not steam_frame
        assert str(rebuilt.cget("bg")) == str(widgets.SELECTED_BG)

        # Choosing another row hands the highlight over.
        picker.search_var.set("")
        assert spin(tk_root, lambda: picker.row_frame(other) is not None)
        picker.select_row(other)
        assert str(picker.row_frame(other).cget("bg")) == str(widgets.SELECTED_BG)
        assert str(picker.row_frame(steam).cget("bg")) == str(widgets.ROW_BG)
    finally:
        app._on_close()
