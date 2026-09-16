"""Monitor thread tests.

Timing is made deterministic by waiting for observable events instead of
sleeping for a guessed duration.
"""

from __future__ import annotations

import queue
import time

from cheski.monitor import (
    KIND_ERROR,
    KIND_MATCH,
    KIND_STOPPED,
    KIND_TARGET_LOST,
    KIND_TARGET_REATTACHED,
    KIND_TICK,
    MonitorThread,
)
from cheski.windows import FakeWindowSource, WindowInfo


def wait_for(events: "queue.Queue", kind: str, timeout: float = 5.0):
    """Return (event, everything_seen) once ``kind`` shows up."""
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = events.get(timeout=0.1)
        except queue.Empty:
            continue
        seen.append(event)
        if event.kind == kind:
            return event, seen
    raise AssertionError(f"never saw {kind}; saw {[item.kind for item in seen]}")


def start_monitor(source, engine, *, interval=0.2, handle=4242):
    events: "queue.Queue" = queue.Queue()
    target = next(info for info in source.list_windows() if info.handle == handle)
    thread = MonitorThread(source, target, engine, interval=interval, events=events)
    thread.start()
    return thread, events


def test_matching_title_produces_a_match_event(fake_source, fake_engine):
    thread, events = start_monitor(fake_source, fake_engine)
    try:
        # Wait until the engine is armed before the title flips to 100%.
        _, seen = wait_for(events, KIND_TICK)
        assert seen[0].armed is True

        fake_source.set_title(4242, "Steam - Downloading 100%")
        event, _ = wait_for(events, KIND_MATCH)

        assert event.title == "Steam - Downloading 100%"
        assert event.armed is True
        assert event.streak == 1
    finally:
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_worker_reports_stopped_and_exits(fake_source, fake_engine):
    thread, events = start_monitor(fake_source, fake_engine)
    wait_for(events, KIND_TICK)
    thread.request_stop()
    wait_for(events, KIND_STOPPED)
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_stop_is_prompt_even_with_a_long_interval(fake_source, fake_engine):
    """Cancellation must not wait for the next poll."""
    thread, events = start_monitor(fake_source, fake_engine, interval=30.0)
    wait_for(events, KIND_TICK)
    started = time.monotonic()
    thread.request_stop()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert time.monotonic() - started < 1.0


def test_lost_window_is_reported_and_never_treated_as_a_trigger(
    fake_source, fake_engine, windows_never_alive
):
    thread, events = start_monitor(fake_source, fake_engine)
    try:
        wait_for(events, KIND_TICK)
        fake_source.remove(4242)
        event, seen = wait_for(events, KIND_TARGET_LOST)

        assert "disappeared" in event.detail
        assert KIND_MATCH not in [item.kind for item in seen]
    finally:
        thread.request_stop()
        thread.join(timeout=3)


def test_lost_window_is_re_attached_by_process_name(
    fake_source, fake_engine, windows_never_alive
):
    thread, events = start_monitor(fake_source, fake_engine)
    try:
        wait_for(events, KIND_TICK)
        fake_source.remove(4242)
        wait_for(events, KIND_TARGET_LOST)

        fake_source.add(
            WindowInfo(7777, "Steam - Downloading 30%", pid=1234, process="steam.exe")
        )
        event, seen = wait_for(events, KIND_TARGET_REATTACHED)
        assert event.title == "Steam - Downloading 30%"
        assert "steam.exe" in event.detail

        # ... and the new handle is the one being watched now.
        fake_source.set_title(7777, "Steam - Downloading 100%")
        match, _ = wait_for(events, KIND_MATCH)
        assert match.title == "Steam - Downloading 100%"
    finally:
        thread.request_stop()
        thread.join(timeout=3)


def test_lost_window_is_reported_only_once(fake_source, fake_engine, windows_never_alive):
    """The GUI log pane must not get one 'lost' line per poll."""
    thread, events = start_monitor(fake_source, fake_engine, interval=0.2)
    wait_for(events, KIND_TICK)
    fake_source.remove(4242)
    wait_for(events, KIND_TARGET_LOST)
    time.sleep(0.6)  # several more polls with the window still gone
    thread.request_stop()
    thread.join(timeout=3)
    losses = [item for item in drain(events) if item.kind == KIND_TARGET_LOST]
    assert losses == []


def drain(events: "queue.Queue") -> list:
    out = []
    while True:
        try:
            out.append(events.get_nowait())
        except queue.Empty:
            return out


class ExplodingSource:
    """Fails the first poll, then behaves."""

    def __init__(self, windows: list[WindowInfo]) -> None:
        self._inner = FakeWindowSource(windows)
        self.failures = 1

    def list_windows(self):
        return self._inner.list_windows()

    def get_title(self, handle):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("simulated GDI hiccup")
        return self._inner.get_title(handle)


def test_a_poll_error_does_not_kill_the_loop(fake_engine):
    source = ExplodingSource(
        [WindowInfo(4242, "Steam - Downloading 12%", pid=1234, process="steam.exe")]
    )
    events: "queue.Queue" = queue.Queue()
    thread = MonitorThread(
        source, source.list_windows()[0], fake_engine, interval=0.2, events=events
    )
    thread.start()
    try:
        error, _ = wait_for(events, KIND_ERROR)
        assert "simulated GDI hiccup" in error.detail

        # Wait for a successful poll to arm the engine before flipping the
        # title; flipping it first would (correctly) leave us in WARMING.
        tick, _ = wait_for(events, KIND_TICK)
        assert tick.armed is True

        source._inner.set_title(4242, "Steam - Downloading 100%")
        match, _ = wait_for(events, KIND_MATCH)
        assert match.title == "Steam - Downloading 100%"
    finally:
        thread.request_stop()
        thread.join(timeout=3)


def test_unchanged_titles_are_not_re_logged(fake_source, fake_engine):
    """The GUI log pane must not grow one line per poll."""
    thread, events = start_monitor(fake_source, fake_engine, interval=0.2)
    wait_for(events, KIND_TICK)
    time.sleep(0.6)  # at least a couple more polls, same title
    thread.request_stop()
    thread.join(timeout=3)
    ticks = [item for item in drain(events) if item.kind == KIND_TICK]
    assert ticks == []


def test_interval_floor_is_enforced(fake_source, fake_engine):
    thread = MonitorThread(fake_source, fake_source.list_windows()[0], fake_engine, interval=0.0)
    assert thread.interval == 0.2
