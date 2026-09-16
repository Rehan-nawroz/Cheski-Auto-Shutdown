"""Unit tests for the extracted CountdownDialog (needs a display, not the app)."""

from __future__ import annotations

import pytest

from cheski.ui.countdown import CountdownDialog

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root():
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
        except tk.TclError:  # pragma: no cover
            pass


def _body_texts(dialog) -> list[str]:
    frame = dialog.toplevel.winfo_children()[0]
    return [w.cget("text") for w in frame.winfo_children() if hasattr(w, "cget")]


def test_dialog_shows_warning_and_dry_run_banner(root):
    aborted = []
    dialog = CountdownDialog(root, seconds=15, dry_run=True, on_abort=lambda: aborted.append(1))
    try:
        texts = _body_texts(dialog)
        assert any("force-closed" in t for t in texts)
        assert any("DRY RUN" in t for t in texts)
        assert any("15 seconds" in t for t in texts)
        # The initial tick has already run, so the first decrement happened:
        # a dialog built for N seconds shows N but waits N-1 full ticks.
        assert dialog.seconds_left == 14
    finally:
        dialog.destroy()
    assert aborted == []  # building and destroying never aborts by itself


def test_tick_counts_down_and_stops_at_zero(root):
    dialog = CountdownDialog(root, seconds=2, dry_run=False, on_abort=lambda: None)
    try:
        dialog._tick()
        root.update()
        dialog._tick()
        root.update()
        dialog._tick()
        root.update()
        assert dialog.seconds_left <= 0
        assert "Shutting down now" in dialog._label.cget("text")
    finally:
        dialog.destroy()


def test_abort_goes_through_the_callback(root):
    aborted = []
    dialog = CountdownDialog(root, seconds=15, dry_run=False, on_abort=lambda: aborted.append(1))
    try:
        dialog.abort()
        assert aborted == [1]
    finally:
        dialog.destroy()


def test_destroy_is_idempotent(root):
    dialog = CountdownDialog(root, seconds=15, dry_run=False, on_abort=lambda: None)
    dialog.destroy()
    dialog.destroy()
    assert dialog.alive is False
