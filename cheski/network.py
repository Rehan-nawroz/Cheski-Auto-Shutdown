"""Idle detection for apps that never publish progress in a title bar.

qBittorrent's main window title never changes and its progress lives in list
cells, not title bars — no trigger word can ever match.  This module watches
the target *process* instead: a sampler reads one process's cumulative
activity and a pure state machine decides when the job has gone quiet.

``psutil`` note: per-process *network* counters are not portably available,
but every downloader lands received data on disk (torrent pieces, game files,
browser cache), so cumulative disk I/O (read + write) is a reliable activity
signal: it climbs while the app works and flatlines when the job is done.
Seeding/verify phases keep reading, which correctly delays the shutdown.

Everything here is plain data: :class:`IdleDetector` takes its clock as an
argument so the firing rules are unit-testable without sleeps or psutil.
"""

from __future__ import annotations

try:
    import psutil
except ImportError:  # pragma: no cover - exercised only without psutil
    psutil = None  # type: ignore[assignment]

#: Detector states, in the order they occur.
WAITING = "waiting"  # armed, no activity seen yet (never fires in this state)
ACTIVE = "active"  # the app is transferring right now
IDLE = "idle"  # quiet, still inside the idle limit
FIRED = "fired"  # quiet for the whole idle limit after a transfer


class ProcessActivitySampler:
    """Cumulative disk bytes (read + write) of one process id.

    ``sample()`` returns ``None`` when the process is gone, access is denied,
    or psutil is unavailable — the caller then falls back to title watching.
    """

    name = "psutil-io"

    def __init__(self, pid: int) -> None:
        try:
            self._proc = psutil.Process(int(pid)) if psutil is not None else None
        except Exception:  # NoSuchProcess etc. — a dead pid samples as None
            self._proc = None

    def sample(self) -> int | None:
        if self._proc is None:
            return None
        try:
            io = self._proc.io_counters()
        except Exception:  # NoSuchProcess / AccessDenied / ZombieProcess
            return None
        return int(io.read_bytes + io.write_bytes)


class IdleDetector:
    """Fire only after activity was seen and then stopped for ``idle_seconds``.

    The two-part rule is the safety story: an app that never transfers (wrong
    target picked, or a chatty-but-idle app) can never cause a shutdown, and
    an app that pauses briefly gets the whole idle limit to resume.
    """

    def __init__(self, idle_seconds: float) -> None:
        self.idle_seconds = max(1.0, float(idle_seconds))
        self._last_total: int | None = None
        self._last_active_at: float | None = None
        self.saw_activity = False

    def feed(self, total: int, now: float) -> str:
        """Feed one cumulative sample taken at ``now``; return the new state."""
        previous = self._last_total
        self._last_total = total
        if previous is None:
            return WAITING  # first sample: nothing to compare against yet
        if total != previous:
            self.saw_activity = True
            self._last_active_at = now
            return ACTIVE
        if not self.saw_activity:
            return WAITING  # quiet so far; a never-active app never fires
        quiet_for = now - self._last_active_at
        if quiet_for >= self.idle_seconds:
            return FIRED
        return IDLE

    def seconds_left(self, now: float) -> float:
        """Seconds of quiet remaining before a fire (0 once the limit is hit)."""
        if not self.saw_activity or self._last_active_at is None:
            return self.idle_seconds
        return max(0.0, self.idle_seconds - (now - self._last_active_at))
