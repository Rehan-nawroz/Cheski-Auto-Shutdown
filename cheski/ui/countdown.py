"""The countdown window shown while a scheduled shutdown is pending.

Extracted from :mod:`cheski.ui.app` so the pending-shutdown UI has one home:
the app decides *that* a shutdown was scheduled; this class owns the window
that says so, ticks down, and hands the EMERGENCY CANCEL press back.

The dialog never talks to the power layer.  Aborting goes through the
``on_abort`` callback (the app's ``abort_shutdown``), which destroys the
dialog -- the dialog must not know whether the abort ran dry-run or for real.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .state import countdown_body


class CountdownDialog:
    """A topmost window counting down to a scheduled shutdown."""

    def __init__(
        self,
        root: tk.Misc,
        *,
        seconds: int,
        dry_run: bool,
        on_abort: "callable[[], None]",
    ) -> None:
        self.root = root
        self.seconds_left = int(seconds)
        self._on_abort = on_abort
        self._after_job: str | None = None
        self._destroyed = False

        self.toplevel = tk.Toplevel(root)
        self.toplevel.title("Download finished")
        self.toplevel.attributes("-topmost", True)
        self.toplevel.resizable(False, False)
        self.toplevel.protocol("WM_DELETE_WINDOW", self.abort)

        frame = ttk.Frame(self.toplevel, padding=14)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="Download complete!", font=("Segoe UI", 15, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame, text="\n".join(countdown_body(self.seconds_left, dry_run=dry_run)),
            wraplength=420, justify="left",
        ).pack(anchor="w", pady=(8, 8))

        self._label = tk.Label(
            frame,
            text=f"{self.seconds_left} seconds",
            font=("Segoe UI", 22, "bold"),
            fg="#c62828",
        )
        self._label.pack(anchor="w", pady=(0, 8))

        self._abort_button = tk.Button(
            frame,
            text="EMERGENCY CANCEL",
            command=self.abort,
            bg="#c62828",
            fg="white",
            activebackground="#8e0000",
            activeforeground="white",
            font=("Segoe UI", 12, "bold"),
            padx=16,
            pady=8,
        )
        self._abort_button.pack(fill="x")

        self.toplevel.update_idletasks()
        try:
            self.toplevel.lift()
            self.toplevel.focus_force()
        except tk.TclError:  # pragma: no cover - already gone
            pass
        self._tick()

    # -- properties ------------------------------------------------------

    @property
    def alive(self) -> bool:
        """False once destroyed -- the app checks this instead of holding a
        separate "is a countdown running" flag that could drift."""
        return not self._destroyed

    # -- actions ---------------------------------------------------------

    def abort(self) -> None:
        """User asked to cancel; the callback destroys this dialog."""
        self._on_abort()

    def cancel_timer(self) -> None:
        if self._after_job is not None:
            try:
                self.root.after_cancel(self._after_job)
            except tk.TclError:  # pragma: no cover - already cancelled
                pass
            self._after_job = None

    def destroy(self) -> None:
        self.cancel_timer()
        self._destroyed = True
        try:
            self.toplevel.destroy()
        except tk.TclError:  # pragma: no cover - already destroyed
            pass

    # -- loop ------------------------------------------------------------

    def _tick(self) -> None:
        if self._destroyed:
            return
        if self.seconds_left <= 0:
            self._label.configure(text="Shutting down now...")
            self._abort_button.configure(state="disabled")
            return
        self._label.configure(text=f"{self.seconds_left} seconds")
        self.seconds_left -= 1
        self._after_job = self.root.after(1000, self._tick)
