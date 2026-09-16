"""Run states and the widget policy that follows from them.

Everything here is plain data and functions: no tkinter, no threads.  The GUI
applies the returned policy to its widgets, so the *decisions* ("can the user
press Start in this state?") are unit-testable without a display, and only the
*application* of a decision needs Tk.

The state machine itself lives in the engine's arming rules (cheski.triggers)
and in how CheskiApp moves between these states on events; this module holds
the vocabulary and the rules for what each state makes visible.
"""

from __future__ import annotations

STATE_IDLE = "idle"
STATE_WARMING = "warming"
STATE_MONITORING = "monitoring"
STATE_TRIGGERED = "triggered"
STATE_ABORTED = "aborted"

STATES = (
    STATE_IDLE,
    STATE_WARMING,
    STATE_MONITORING,
    STATE_TRIGGERED,
    STATE_ABORTED,
)

STATE_TEXT = {
    STATE_IDLE: "Idle - not monitoring.",
    STATE_WARMING: "Arming - waiting for the trigger to clear once (safety).",
    STATE_MONITORING: "Monitoring - armed and watching the title bar.",
    STATE_TRIGGERED: "TRIGGERED - a shutdown has been scheduled.",
    STATE_ABORTED: "Shutdown aborted. Nothing is scheduled.",
}

#: Colour shown for each state in the status line; anything unlisted is plain.
STATE_COLOURS = {
    STATE_TRIGGERED: "#c62828",
    STATE_ABORTED: "#c77700",
    STATE_MONITORING: "#1b5e20",
    STATE_WARMING: "#b26a00",
}

RUNNING_STATES = frozenset({STATE_WARMING, STATE_MONITORING})
LIVE_STATES = RUNNING_STATES | {STATE_TRIGGERED}


def is_running(state: str) -> bool:
    """A worker is polling: Start must not run a second one, Stop is useful."""
    return state in RUNNING_STATES


def is_live(state: str) -> bool:
    """A run is in flight: the target and Start are frozen until it ends."""
    return state in LIVE_STATES


def status_text(state: str) -> str:
    return STATE_TEXT.get(state, state)


def status_colour(state: str) -> str:
    return STATE_COLOURS.get(state, "#222")


def countdown_body(seconds: int, *, dry_run: bool) -> tuple[str, ...]:
    """The lines shown above the countdown, force-close warning included.

    ``shutdown /s /t <n>`` implies ``/f`` whenever ``n`` is greater than zero,
    so running apps are force-closed at the deadline.  That must be said in
    plain language, in the window the user is looking at.
    """
    lines = []
    if dry_run:
        lines.append("DRY RUN: nothing will actually shut down. This is a rehearsal.")
    lines.append("The trigger word appeared in the watched title bar.")
    lines.append(f"Windows will shut down in {seconds} seconds.")
    lines.append(
        "At zero, running apps are force-closed - save your work now if you are here."
    )
    return tuple(lines)
