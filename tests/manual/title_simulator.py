"""A fake downloader: a window whose title bar you can drive.

Waiting hours for a real 80GB download to finish is not a test loop.  This
little window pretends to be Steam, so the whole arm -> fire -> abort path can
be exercised in seconds.

Usage::

    python tests/manual/title_simulator.py

Then in Cheski Auto Shutdown:

    1. Press "Refresh list" and pick "Simulated Downloader - Downloading 0%".
    2. Put the trigger in Dry-run mode the first time.
    3. Press "Start monitoring", then press "Run scenario" here.

"Start already finished" deliberately opens at 100% so you can watch the
safety guard refuse to fire until the title clears once.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

APP_NAME = "Simulated Downloader"
SCENARIO = [
    "Downloading 0%",
    "Downloading 12%",
    "Downloading 47%",
    "Downloading 88%",
    "Downloading 99%",
    "Downloading 100%",
    "Complete",
]


class TitleSimulator:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.step = 0
        self.job: str | None = None

        root.title(f"{APP_NAME} - Downloading 0%")
        root.minsize(420, 240)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="This window only exists to change its own title bar.",
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.status_var = tk.StringVar(value=f"Current title: {APP_NAME} - Downloading 0%")
        ttk.Label(frame, textvariable=self.status_var, wraplength=380, justify="left").pack(
            anchor="w", pady=(0, 8)
        )

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="Step delay (s)").pack(side="left")
        self.delay_var = tk.StringVar(value="1")
        ttk.Spinbox(row, from_=0.2, to=30, increment=0.2, width=6, textvariable=self.delay_var).pack(
            side="left", padx=6
        )

        ttk.Button(frame, text="Run scenario", command=self.run_scenario).pack(fill="x")
        ttk.Button(
            frame,
            text="Start already finished (100% - tests the arming guard)",
            command=self.start_finished,
        ).pack(fill="x", pady=4)
        ttk.Button(frame, text="Jump straight to 100%", command=lambda: self.set_status("Downloading 100%")).pack(
            fill="x"
        )
        ttk.Button(
            frame, text="Reset to Downloading 10%", command=lambda: self.set_status("Downloading 10%")
        ).pack(fill="x", pady=4)
        ttk.Button(frame, text="Close", command=self.root.destroy).pack(fill="x", pady=(8, 0))

    # -- mechanics ---------------------------------------------------

    def set_status(self, status: str) -> None:
        """This single call is the entire 'download'."""
        self.root.title(f"{APP_NAME} - {status}")
        self.status_var.set(f"Current title: {APP_NAME} - {status}")

    def _delay_ms(self) -> int:
        try:
            return max(200, int(float(self.delay_var.get()) * 1000))
        except ValueError:
            return 1000

    def run_scenario(self) -> None:
        self.cancel()
        self.step = 0
        self._advance()

    def _advance(self) -> None:
        if self.step >= len(SCENARIO):
            self.job = None
            return
        self.set_status(SCENARIO[self.step])
        self.step += 1
        self.job = self.root.after(self._delay_ms(), self._advance)

    def start_finished(self) -> None:
        self.cancel()
        self.set_status("Downloading 100%")

    def cancel(self) -> None:
        if self.job is not None:
            self.root.after_cancel(self.job)
            self.job = None


def main() -> None:
    root = tk.Tk()
    TitleSimulator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
