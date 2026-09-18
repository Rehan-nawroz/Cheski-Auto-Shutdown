"""The monitoring loop.

The worker runs on its own thread so the GUI never blocks.  It **never**
touches a widget: tkinter is not thread-safe, so results are posted to a
``queue.Queue`` and the GUI drains that queue from its own event loop.

Cancellation goes through :class:`threading.Event` with
``stop_event.wait(interval)`` rather than ``time.sleep``, which makes Stop
instantaneous instead of "up to one interval late".  Poll failures are caught
and reported as events instead of killing the thread -- a monitor thread that
dies silently at 3am is worse than one that logs a warning every 2 seconds.

By default the loop watches **every window of the target's process**, not just
the one window that was picked.  Downloaders like IDM never change their main
window's title; they pop a separate "Download complete" dialog that did not
exist when monitoring started.  A single-window watcher can never see it; a
process-scoped one reads its title the moment it appears.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from .network import ACTIVE, FIRED, WAITING, IdleDetector, ProcessActivitySampler
from .triggers import TriggerEngine
from .windows import WindowInfo, WindowSource, resolve_target

log = logging.getLogger("cheski.monitor")

KIND_TICK = "tick"
KIND_MATCH = "match"
KIND_TARGET_LOST = "target_lost"
KIND_TARGET_REATTACHED = "target_reattached"
KIND_ERROR = "error"
KIND_STOPPED = "stopped"

ALL_KINDS = (
    KIND_TICK,
    KIND_MATCH,
    KIND_TARGET_LOST,
    KIND_TARGET_REATTACHED,
    KIND_ERROR,
    KIND_STOPPED,
)


@dataclass(frozen=True)
class MonitorEvent:
    """One message from the worker thread to the GUI thread."""

    kind: str
    title: str = ""
    detail: str = ""
    streak: int = 0
    armed: bool = False
    poll: int = 0
    #: Handle of the window now being watched, set on a re-attach so the GUI can
    #: keep naming the window it is really reading.
    handle: int = 0


class MonitorThread(threading.Thread):
    """Polls one window's title and evaluates the trigger engine."""

    def __init__(
        self,
        source: WindowSource,
        target: WindowInfo,
        engine: TriggerEngine,
        *,
        interval: float = 2.0,
        events: "queue.Queue[MonitorEvent] | None" = None,
        stop_event: threading.Event | None = None,
        name: str = "cheski-monitor",
        watch_process: bool = True,
        sampler=None,
        idle_seconds: float = 300.0,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.source = source
        self.target = target
        self.engine = engine
        self.interval = max(0.2, float(interval))
        self.events: queue.Queue[MonitorEvent] = events if events is not None else queue.Queue()
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.watch_process = bool(watch_process)
        self.polls = 0
        # Optional second trigger: process activity instead of title words.
        # ``sampler`` is anything with ``.sample() -> int | None`` (cumulative
        # bytes); ``None`` disables the idle path entirely.
        self._sampler = sampler
        self._idle = IdleDetector(idle_seconds) if sampler is not None else None
        self._last_idle_state = None

    # -- control ---------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the loop to finish.  Returns immediately."""
        self.stop_event.set()

    def _emit(self, kind: str, **fields) -> None:
        try:
            self.events.put(MonitorEvent(kind=kind, poll=self.polls, **fields))
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Could not post %s event: %s", kind, exc)

    def _process_titles(self) -> str | None:
        """Union of the titles of every window owned by the target's process.

        ``None`` when process scoping is off or the target has no process name;
        an empty string when the process is on but currently owns no titled
        window (the caller then falls back to the single-window read).
        """
        if not self.watch_process or not self.target.process:
            return None
        wanted = self.target.process.lower()
        parts = [
            info.title
            for info in self.source.list_windows()
            if info.title and info.process and info.process.lower() == wanted
        ]
        return "\n".join(parts) if parts else ""

    # -- loop ------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 - the loop is deliberately explicit
        current = self.target
        last_title: str | None = None
        last_armed = self.engine.armed
        lost_reported = False
        try:
            while not self.stop_event.is_set():
                try:
                    self.polls += 1
                    title = self.source.get_title(current.handle)

                    scoped = self._process_titles()
                    if scoped:
                        # Evaluate the union of the process's window titles.
                        # This already contains the picked window's own title,
                        # so it replaces the single-window read outright.
                        title = scoped
                        lost_reported = False

                    if title is None:
                        # The window is gone.  Never treat this as a trigger;
                        # try to find the app's new window first.
                        replacement = resolve_target(self.source, current)
                        if replacement is None:
                            if not lost_reported:
                                lost_reported = True
                                self._emit(
                                    KIND_TARGET_LOST,
                                    detail=(
                                        "Target window disappeared. Still watching for it to "
                                        "come back; no shutdown will happen while it is gone."
                                    ),
                                    armed=self.engine.armed,
                                )
                        else:
                            current = replacement
                            lost_reported = False
                            title = replacement.title
                            self._emit(
                                KIND_TARGET_REATTACHED,
                                title=title,
                                handle=replacement.handle,
                                detail=(
                                    "Re-attached to "
                                    f"{replacement.process or 'the target window'} "
                                    f"(new handle {replacement.handle}): {title}"
                                ),
                                armed=self.engine.armed,
                            )
                    else:
                        lost_reported = False

                    if title is not None:
                        result = self.engine.evaluate(title)
                        armed = self.engine.armed
                        if (
                            title != last_title
                            or result.streak
                            or result.matched
                            or armed != last_armed
                        ):
                            last_title = title
                            self._emit(
                                KIND_TICK,
                                title=title,
                                detail=result.reason,
                                streak=result.streak,
                                armed=armed,
                            )
                        last_armed = armed

                        if result.matched:
                            self._emit(
                                KIND_MATCH,
                                title=title,
                                detail=result.reason,
                                streak=result.streak,
                                armed=True,
                            )
                            return

                    if self._idle is not None and self.target.pid:
                        # Second trigger path: the process transferred data and
                        # has now been quiet for the idle limit.  Only this
                        # detector's own two-part rule (activity seen, then a
                        # full quiet period) can fire it; a gone process yields
                        # ``None`` samples which are ignored, so closing the app
                        # can never cause a shutdown.
                        total = self._sampler.sample()
                        if total is not None:
                            import time

                            state = self._idle.feed(total, time.monotonic())
                            if state != self._last_idle_state:
                                self._last_idle_state = state
                                self._emit(
                                    KIND_TICK,
                                    title=title or "",
                                    detail={
                                        WAITING: "network idle: waiting for the app to transfer data",
                                        ACTIVE: "network idle: app is transferring",
                                        FIRED: "network idle: transfer finished",
                                    }.get(state, f"network idle: quiet, fires in {max(0, self._idle.seconds_left(time.monotonic())):.0f}s"),
                                    armed=True,
                                )
                            if state == FIRED:
                                self._emit(
                                    KIND_MATCH,
                                    title=title or "",
                                    detail=(
                                        "network idle: {name} transferred data and has been quiet "
                                        "for {secs:.0f}s"
                                    ).format(
                                        name=self.target.process or "the app",
                                        secs=self._idle.idle_seconds,
                                    ),
                                    armed=True,
                                )
                                return
                except Exception as exc:
                    self._emit(
                        KIND_ERROR,
                        detail=f"{type(exc).__name__}: {exc}",
                        armed=self.engine.armed,
                    )
                    log.warning("Poll failed: %s", exc, exc_info=log.isEnabledFor(logging.DEBUG))

                if self.stop_event.wait(self.interval):
                    break
        finally:
            self._emit(KIND_STOPPED, title=last_title or "", armed=self.engine.armed)
            log.info("Monitor stopped after %d poll(s).", self.polls)
