"""Live end-to-end probe: real window, real title reads, real trigger engine.

The pytest suite uses fakes for the desktop, so this script closes the last
gap: it opens a genuine Tk window, finds it through ``pygetwindow``, reads its
title through ``user32!GetWindowTextW`` and lets a real
:class:`~cheski.monitor.MonitorThread` fire a real
:class:`~cheski.triggers.TriggerEngine`.

It never touches :mod:`cheski.power`, so no shutdown can happen.  A window will
appear on screen for a few seconds.

Usage::

    python tests/manual/live_probe.py
"""

from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from pathlib import Path

# Running this file directly puts tests/manual/ on sys.path, not the project
# root, so add the root explicitly before importing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cheski.monitor import KIND_MATCH, KIND_TICK, MonitorThread  # noqa: E402
from cheski.triggers import TriggerConfig, TriggerEngine
from cheski.windows import PyGetWindowSource, WindowInfo

PROBE_TITLE = "Cheski E2E Probe"


def find_probe_window(source: PyGetWindowSource) -> WindowInfo | None:
    for info in source.list_windows():
        if info.title.startswith(PROBE_TITLE):
            return info
    return None


def pump_for_match(root: tk.Tk, thread: MonitorThread, events: "queue.Queue", seconds: float):
    """Keep the GUI alive while the worker polls; return the first match event."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        try:
            event = events.get_nowait()
        except queue.Empty:
            time.sleep(0.03)
            continue
        print(f"    event: {event.kind:<17} streak={event.streak} armed={event.armed} "
              f"title={event.title!r}")
        if event.kind == KIND_MATCH:
            return event
    return None


def wait_until_armed(root: tk.Tk, events: "queue.Queue", seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        try:
            event = events.get_nowait()
        except queue.Empty:
            time.sleep(0.03)
            continue
        if event.kind == KIND_TICK and event.armed:
            return True
    return False


def run(interval: float = 0.25) -> int:
    root = tk.Tk()
    root.title(f"{PROBE_TITLE} - Downloading 10%")
    root.geometry("360x120+40+40")
    root.update()  # map the window so pygetwindow can see it

    source = PyGetWindowSource()
    target = find_probe_window(source)
    if target is None:
        print("FAIL: the probe window did not show up in the window list")
        root.destroy()
        return 1
    print(f"PASS: found the probe window (handle={target.handle}, pid={target.pid}, "
          f"process={target.process})")

    # ---- Phase 1: a normal download finishes -------------------------
    print("\nPhase 1: arm at 10%, then finish at 100%")
    engine = TriggerEngine(
        TriggerConfig(triggers=("100%",), dwell_checks=1, require_transition=True)
    )
    engine.reset()
    events: "queue.Queue" = queue.Queue()
    thread = MonitorThread(source, target, engine, interval=interval, events=events)
    thread.start()

    armed = wait_until_armed(root, events, 5.0)
    print(f"{'PASS' if armed else 'FAIL'}: engine armed while the title had no trigger")
    if not armed:
        thread.request_stop()
        root.destroy()
        return 1

    root.title(f"{PROBE_TITLE} - Downloading 100%")
    match = pump_for_match(root, thread, events, 5.0)
    thread.request_stop()
    thread.join(timeout=2)
    print(f"{'PASS' if match else 'FAIL'}: real title change produced a real match event")
    if match is None:
        root.destroy()
        return 1

    # ---- Phase 2: the safety guard, for real -------------------------
    print("\nPhase 2: start with the title already at 100% (must NOT fire)")
    engine2 = TriggerEngine(
        TriggerConfig(triggers=("100%",), dwell_checks=1, require_transition=True)
    )
    engine2.reset()
    events2: "queue.Queue" = queue.Queue()
    root.title(f"{PROBE_TITLE} - Downloading 100%")
    root.update()
    thread2 = MonitorThread(source, target, engine2, interval=interval, events=events2)
    thread2.start()
    leaked = pump_for_match(root, thread2, events2, 2.0)
    thread2.request_stop()
    thread2.join(timeout=2)
    print(f"{'FAIL' if leaked else 'PASS'}: already-finished title did not fire")

    root.destroy()
    return 0 if leaked is None else 1


def main() -> int:
    if sys.platform != "win32":
        print("This probe needs Windows.")
        return 2
    code = run()
    print("\n" + ("LIVE PROBE PASSED" if code == 0 else "LIVE PROBE FAILED"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
