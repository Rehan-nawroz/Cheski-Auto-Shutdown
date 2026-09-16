"""tkinter user interface for Cheski Auto Shutdown.

Threading contract: this module owns the Tk root and is the only place that
touches widgets.  :class:`~cheski.monitor.MonitorThread` posts
:class:`~cheski.monitor.MonitorEvent` objects onto a queue, and
:meth:`CheskiApp._pump` drains that queue from the Tk event loop via
``root.after``.  Nothing else may call into Tk.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

try:  # pragma: no cover - platform specific
    import winsound
except ImportError:  # pragma: no cover
    winsound = None  # type: ignore[assignment]

from .. import __version__
from ..config import (
    DELAY_RANGE,
    DWELL_RANGE,
    INTERVAL_RANGE,
    Settings,
    clamp,
    config_path,
    load_settings,
    save_settings,
)
from ..logging_setup import setup_logging
from ..monitor import (
    KIND_ERROR,
    KIND_MATCH,
    KIND_STOPPED,
    KIND_TARGET_LOST,
    KIND_TARGET_REATTACHED,
    KIND_TICK,
    MonitorEvent,
    MonitorThread,
)
from ..power import PowerController
from ..triggers import TriggerConfig, TriggerEngine, parse_triggers
from ..windows import PyGetWindowSource, WindowInfo, WindowSource
from .countdown import CountdownDialog
from .state import (
    STATE_ABORTED,
    STATE_IDLE,
    STATE_MONITORING,
    STATE_TRIGGERED,
    STATE_WARMING,
    STATE_TEXT,
    is_live,
    is_running,
    status_colour,
    status_text,
)

log = logging.getLogger("cheski.ui")

_POLL_MS = 150
_MAX_LOG_LINES = 600

PowerFactory = Callable[..., PowerController]


def _default_power_factory(**kwargs) -> PowerController:
    return PowerController(**kwargs)


class CheskiApp:
    """The whole application: widgets, state machine, timer plumbing."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        source: WindowSource | None = None,
        settings: Settings | None = None,
        power_factory: PowerFactory | None = None,
        force_dry_run: bool = False,
    ) -> None:
        self.root = root
        self.source: WindowSource = source if source is not None else PyGetWindowSource()
        self.settings = settings if settings is not None else load_settings()
        self.power_factory: PowerFactory = power_factory or _default_power_factory
        self.force_dry_run = force_dry_run

        self.events: "queue.Queue[MonitorEvent]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: MonitorThread | None = None
        self.engine: TriggerEngine | None = None
        self.target: WindowInfo | None = None
        self.state = STATE_IDLE
        self._windows: dict[str, WindowInfo] = {}
        self._closing = False
        self._pump_job: str | None = None
        self.countdown: CountdownDialog | None = None
        self._logged_titles: set[str] = set()

        self.root.title(f"Cheski Auto Shutdown {__version__}")
        self.root.minsize(640, 620)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_windows()
        self._update_preview()
        self._pump_job = self.root.after(_POLL_MS, self._pump)
        log.info("GUI ready (dry_run=%s)", self.dry_run_var.get())

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        style = ttk.Style()
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                except tk.TclError:  # pragma: no cover
                    continue
                break

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        # -- 1. target window -------------------------------------------
        target_box = ttk.LabelFrame(outer, text="1. Window to watch", padding=8)
        target_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        target_box.columnconfigure(0, weight=1)

        self.window_var = tk.StringVar()
        self.window_box = ttk.Combobox(
            target_box, textvariable=self.window_var, state="readonly", width=64
        )
        self.window_box.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.window_box.bind("<<ComboboxSelected>>", lambda _event: self._on_window_selected())

        self.refresh_button = ttk.Button(
            target_box, text="Refresh list", command=self.refresh_windows
        )
        self.refresh_button.grid(row=0, column=1, sticky="ew")

        self.target_info_var = tk.StringVar(value="No window selected.")
        ttk.Label(target_box, textvariable=self.target_info_var, foreground="#444").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        # -- 2. trigger --------------------------------------------------
        trigger_box = ttk.LabelFrame(outer, text="2. Trigger words", padding=8)
        trigger_box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        trigger_box.columnconfigure(0, weight=1)

        self.trigger_var = tk.StringVar(value=", ".join(self.settings.triggers))
        ttk.Entry(trigger_box, textvariable=self.trigger_var, width=40).grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        self.match_var = tk.StringVar(value=self.settings.match_mode)
        mode_box = ttk.Frame(trigger_box)
        mode_box.grid(row=0, column=1, sticky="e")
        ttk.Radiobutton(mode_box, text="any", value="any", variable=self.match_var).pack(side="left")
        ttk.Radiobutton(mode_box, text="all", value="all", variable=self.match_var).pack(side="left")

        options = ttk.Frame(trigger_box)
        options.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.case_var = tk.BooleanVar(value=self.settings.case_sensitive)
        ttk.Checkbutton(options, text="Match exact case", variable=self.case_var).pack(side="left")
        self.transition_var = tk.BooleanVar(value=self.settings.require_transition)
        ttk.Checkbutton(
            options,
            text="Only fire after the trigger clears once (recommended)",
            variable=self.transition_var,
        ).pack(side="left", padx=(12, 0))

        ttk.Label(
            trigger_box,
            text=(
                "Separate several words with commas. 'all' fires only when every word is "
                "present, which is safer than a bare '100%'."
            ),
            foreground="#444",
            wraplength=580,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # -- 3. timing ---------------------------------------------------
        timing_box = ttk.LabelFrame(outer, text="3. Timing and safety", padding=8)
        timing_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(timing_box, text="Check every").grid(row=0, column=0, sticky="w")
        self.interval_var = tk.StringVar(value=f"{self.settings.interval_seconds:g}")
        self.interval_box = ttk.Spinbox(
            timing_box,
            from_=INTERVAL_RANGE[0],
            to=INTERVAL_RANGE[1],
            increment=0.5,
            width=6,
            textvariable=self.interval_var,
            command=self._update_preview,
        )
        self.interval_box.grid(row=0, column=1, sticky="w", padx=(4, 4))
        ttk.Label(timing_box, text="seconds").grid(row=0, column=2, sticky="w", padx=(0, 16))

        ttk.Label(timing_box, text="Confirm after").grid(row=0, column=3, sticky="w")
        self.dwell_var = tk.StringVar(value=str(self.settings.dwell_checks))
        self.dwell_box = ttk.Spinbox(
            timing_box,
            from_=DWELL_RANGE[0],
            to=DWELL_RANGE[1],
            width=4,
            textvariable=self.dwell_var,
        )
        self.dwell_box.grid(row=0, column=4, sticky="w", padx=(4, 4))
        ttk.Label(timing_box, text="checks").grid(row=0, column=5, sticky="w", padx=(0, 16))

        ttk.Label(timing_box, text="Shut down after").grid(row=0, column=6, sticky="w")
        self.delay_var = tk.StringVar(value=str(self.settings.shutdown_delay_seconds))
        self.delay_box = ttk.Spinbox(
            timing_box,
            from_=DELAY_RANGE[0],
            to=DELAY_RANGE[1],
            increment=15,
            width=6,
            textvariable=self.delay_var,
            command=self._update_preview,
        )
        self.delay_box.grid(row=0, column=7, sticky="w", padx=(4, 4))
        ttk.Label(timing_box, text="seconds").grid(row=0, column=8, sticky="w")
        for box in (self.interval_box, self.dwell_box, self.delay_box):
            self._bind_field(box)

        self.dry_run_var = tk.BooleanVar(value=self.settings.dry_run or self.force_dry_run)
        dry = ttk.Checkbutton(
            timing_box,
            text="Dry run: log the command instead of running it",
            variable=self.dry_run_var,
            command=self._update_preview,
        )
        dry.grid(row=1, column=0, columnspan=9, sticky="w", pady=(6, 0))
        if self.force_dry_run:
            dry.state(["disabled"])

        self.preview_var = tk.StringVar()
        ttk.Label(
            timing_box,
            textvariable=self.preview_var,
            foreground="#0a4",
            wraplength=600,
            justify="left",
        ).grid(row=2, column=0, columnspan=9, sticky="w", pady=(6, 0))

        ttk.Label(
            timing_box,
            text=(
                "Windows force-closes running apps when the countdown hits zero "
                "(a non-zero timer implies the /f flag), so save your work if you are "
                "still at the PC."
            ),
            foreground="#a30",
            wraplength=600,
            justify="left",
        ).grid(row=3, column=0, columnspan=9, sticky="w", pady=(4, 0))

        # -- 4. controls -------------------------------------------------
        controls = ttk.Frame(outer)
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        self.start_button = ttk.Button(
            controls, text="Start monitoring", command=self.start_monitoring
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop_monitoring)
        self.stop_button.pack(side="left", padx=6)

        self.abort_button = tk.Button(
            controls,
            text="EMERGENCY ABORT  (shutdown /a)",
            command=self.abort_shutdown,
            bg="#c62828",
            fg="white",
            activebackground="#8e0000",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            relief="raised",
            padx=10,
            pady=4,
        )
        self.abort_button.pack(side="right")

        # -- 5. status ---------------------------------------------------
        status_box = ttk.LabelFrame(outer, text="Status", padding=8)
        status_box.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        status_box.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value=STATE_TEXT[STATE_IDLE])
        self.status_label = ttk.Label(
            status_box, textvariable=self.status_var, font=("Segoe UI", 10, "bold")
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.watched_var = tk.StringVar(value="")
        ttk.Label(
            status_box, textvariable=self.watched_var, foreground="#1b5e20", wraplength=600,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.progress_var = tk.StringVar(value="")
        ttk.Label(status_box, textvariable=self.progress_var).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )

        self.title_var = tk.StringVar(value="Last title: (nothing seen yet)")
        ttk.Label(
            status_box, textvariable=self.title_var, foreground="#333", wraplength=600, justify="left"
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

        ttk.Label(status_box, text="Log", foreground="#444").grid(
            row=4, column=0, sticky="w", pady=(8, 2)
        )
        log_frame = ttk.Frame(status_box)
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame, height=10, wrap="word", state="disabled", background="#fbfbfb"
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        outer.rowconfigure(4, weight=1)
        status_box.rowconfigure(5, weight=1)
        self._apply_state(STATE_IDLE)
        self._append_log(f"Cheski Auto Shutdown {__version__} ready.")

    # ------------------------------------------------------------------
    # Window list
    # ------------------------------------------------------------------

    @staticmethod
    def _build_label_map(windows: Sequence[WindowInfo]) -> dict[str, WindowInfo]:
        """Map dropdown labels to windows, keeping duplicates distinguishable.

        Two windows can share a title *and* a process (two identical dialogs
        from one app).  Without this, one would silently shadow the other and
        the user would watch the wrong window.
        """
        own_pid = os.getpid()
        labels: dict[str, WindowInfo] = {}
        for info in windows:
            if info.pid and info.pid == own_pid:
                # Watching our own window can never fire a trigger; offering it
                # only invites a silent no-op overnight.
                continue
            label = info.label
            if label in labels:
                label = f"{label} [{info.handle}]"
            labels[label] = info
        return labels

    def refresh_windows(self) -> None:
        """Re-enumerate top-level windows and rebuild the dropdown.

        The user's choice is never overwritten; a window is preselected only
        when a previous session's target is recognised.
        """
        windows = self.source.list_windows()
        self._windows = self._build_label_map(windows)
        labels = list(self._windows)
        self.window_box.configure(values=labels)
        self._append_log(f"Found {len(labels)} window(s).")

        current = self.window_box.get()
        if current and current in self._windows:
            chosen = current  # keep what the user picked
        else:
            chosen = self._remembered_label(labels)  # only if last session had one
        self.window_box.set(chosen or "")
        self._on_window_selected()

    def _remembered_label(self, labels: list[str]) -> str | None:
        """Best-effort restore of the last target from a previous session."""
        wanted = (self.settings.last_process or "").casefold()
        hint = (self.settings.last_title_hint or "").casefold()
        if not wanted:
            return None
        matches = [label for label in labels if wanted in label.casefold()]
        if not matches:
            return None
        if hint:
            scored = sorted(matches, key=lambda label: hint not in label.casefold())
            return scored[0]
        return matches[0]

    def _on_window_selected(self) -> None:
        # While a run is live the target is frozen, so the display is put back
        # rather than being allowed to diverge from what is watched.
        if self._run_is_live() and self.worker is not None:
            watched = self.worker.target
            label = next(
                (name for name, info in self._windows.items() if info.handle == watched.handle),
                None,
            )
            if label is not None:
                self.window_box.set(label)
            self._show_watched(watched)
            return

        label = self.window_var.get()
        info = self._windows.get(label)
        if info is None:
            self.target = None
            if self._windows:
                self.target_info_var.set(
                    "No window selected. Pick the downloading app from the list above."
                )
            else:
                self.target_info_var.set(
                    "No selectable windows found. Open the downloading app first, then "
                    "press Refresh list."
                )
            return
        self.target = info
        details = [f"handle {info.handle}"]
        if info.pid:
            details.append(f"pid {info.pid}")
        if info.process:
            details.append(info.process)
        self.target_info_var.set(
            f"Selected: {info.title or '(untitled)'}  [{', '.join(details)}]"
        )

    def _selected_window(self) -> WindowInfo | None:
        if self.target is not None:
            return self.target
        return self._windows.get(self.window_var.get())

    # ------------------------------------------------------------------
    # Spinbox value helpers (never trust a text field)
    # ------------------------------------------------------------------

    def _bind_field(self, widget) -> None:
        """Keep the preview honest while typing, and settle the field on exit."""
        widget.bind("<KeyRelease>", lambda _event: self._update_preview())
        widget.bind("<FocusOut>", lambda _event: self._settle_fields())

    def _settle_fields(self) -> None:
        """Write the accepted (clamped) values back into the fields."""
        self.interval_var.set(f"{self._interval_value():g}")
        self.dwell_var.set(str(self._dwell_value()))
        self.delay_var.set(str(self._delay_value()))
        self._update_preview()

    def _worker_running(self) -> bool:
        """True while a worker thread is alive, whatever the state says."""
        return self.worker is not None and self.worker.is_alive()

    def _run_is_live(self) -> bool:
        """True while a run is in flight (arming, watching, or counting down)."""
        return is_live(self.state) or self._worker_running()

    def _show_watched(self, target: WindowInfo | None) -> None:
        if target is None:
            self.watched_var.set("")
        else:
            self.watched_var.set(
                f"Watching: {target.title or '(untitled)'}  [handle {target.handle}]"
            )

    def _number(self, variable: tk.StringVar, default: float, limits: tuple[float, float], *, as_int: bool) -> float:
        try:
            value = float(variable.get())
        except (TypeError, ValueError):
            value = default
        value = clamp(value, limits[0], limits[1])
        return int(value) if as_int else round(value, 2)

    def _interval_value(self) -> float:
        return self._number(self.interval_var, self.settings.interval_seconds, INTERVAL_RANGE, as_int=False)

    def _dwell_value(self) -> int:
        return int(self._number(self.dwell_var, self.settings.dwell_checks, DWELL_RANGE, as_int=True))

    def _delay_value(self) -> int:
        return int(
            self._number(
                self.delay_var, self.settings.shutdown_delay_seconds, DELAY_RANGE, as_int=True
            )
        )

    def _make_power(self) -> PowerController:
        return self.power_factory(seconds=self._delay_value(), dry_run=self.dry_run_var.get())

    def _update_preview(self) -> None:
        try:
            preview = self._make_power().describe()
        except Exception as exc:  # pragma: no cover - defensive
            preview = f"(could not build the command: {exc})"
        self.preview_var.set(f"Command that will be run: {preview}")

    # ------------------------------------------------------------------
    # Monitoring lifecycle
    # ------------------------------------------------------------------

    def start_monitoring(self) -> bool:
        """Validate the form and start the worker thread."""
        if self.worker is not None and self.worker.is_alive():
            return False

        # Make the fields show what the run will actually use before reading
        # them, so the displayed value and the real value cannot disagree.
        self._settle_fields()

        triggers = parse_triggers(self.trigger_var.get())
        if not triggers:
            messagebox.showerror(
                "Trigger needed",
                "Enter at least one trigger word, for example 100% or Complete.",
            )
            return False

        target = self._selected_window()
        if target is None:
            messagebox.showerror(
                "No window selected",
                "Press 'Refresh list' and pick the window whose title bar shows the "
                "download progress.",
            )
            return False

        config = TriggerConfig(
            triggers=triggers,
            match_mode=self.match_var.get(),
            case_sensitive=self.case_var.get(),
            dwell_checks=self._dwell_value(),
            require_transition=self.transition_var.get(),
        )
        self.engine = TriggerEngine(config)
        self.engine.reset()
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self._logged_titles.clear()

        self.worker = MonitorThread(
            self.source,
            target,
            self.engine,
            interval=self._interval_value(),
            events=self.events,
            stop_event=self.stop_event,
        )
        self.worker.start()
        self._show_watched(target)

        self._persist()
        self._append_log(
            "Monitoring '{title}' for {words} (mode={mode}, every {interval}s, "
            "confirm after {dwell} check(s)).".format(
                title=target.title or "(untitled)",
                words=", ".join(triggers),
                mode=config.match_mode,
                interval=self._interval_value(),
                dwell=config.dwell_checks,
            )
        )
        if self.dry_run_var.get():
            self._append_log("DRY RUN is on: the shutdown command will be logged, not executed.")
        self._apply_state(STATE_WARMING)
        return True

    def stop_monitoring(self) -> None:
        """Stop the worker thread.  Does not touch any pending shutdown."""
        self._stop_worker()
        self._append_log("Monitoring stopped.")
        self._apply_state(STATE_IDLE)
        self._on_window_selected()

    def _stop_worker(self) -> None:
        """Ask the worker to finish and drop the reference to it."""
        if self.worker is not None:
            self.worker.request_stop()
            self.stop_event.set()
            self.worker = None

    def abort_shutdown(self) -> None:
        """Run ``shutdown /a`` -- cancels a pending shutdown."""
        result = self._make_power().abort_shutdown()
        self._append_log(f"$ {result.command_line}")
        self._append_log(f"  -> rc={result.returncode} {result.message}")
        self._close_countdown()
        if result.ok:
            self._apply_state(STATE_ABORTED)
            messagebox.showinfo("Shutdown cancelled", result.message)
        else:
            if result.returncode == 1116:
                # Nothing was pending, so nothing is going to happen either.
                self._apply_state(STATE_IDLE)
            messagebox.showwarning("Nothing to abort", result.message)

    # ------------------------------------------------------------------
    # Triggered: schedule the shutdown and run the countdown
    # ------------------------------------------------------------------

    def _on_trigger(self, event: MonitorEvent) -> None:
        """Schedule the shutdown and open the countdown window.

        The countdown dialog is the only way to cancel a pending shutdown, so
        the closing handler aborts for the user rather than leaving a scheduled
        shutdown with no UI.
        """
        self._apply_state(STATE_TRIGGERED)
        self._append_log(f"TRIGGER MATCHED: {event.title!r} ({event.detail})")
        self._beep()

        seconds = self._delay_value()
        result = self._make_power().schedule_shutdown()
        self._append_log(f"$ {result.command_line}")
        self._append_log(f"  -> rc={result.returncode} {result.message}")

        if not result.ok:
            # Nothing was scheduled, so there is nothing to count down to.
            self._apply_state(STATE_IDLE)
            messagebox.showerror(
                "Could not schedule the shutdown",
                f"{result.message}\n\nThe command was:\n{result.command_line}",
            )
            return

        self._stop_worker()
        self.countdown = CountdownDialog(
            self.root,
            seconds=seconds,
            dry_run=result.dry_run,
            on_abort=self.abort_shutdown,
        )

    def _close_countdown(self) -> None:
        if self.countdown is not None:
            self.countdown.destroy()
            self.countdown = None

    def _beep(self) -> None:
        try:
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                self.root.after(400, lambda: winsound.MessageBeep(winsound.MB_ICONHAND))
            else:  # pragma: no cover
                self.root.bell()
        except Exception:  # pragma: no cover - sound is never critical
            pass

    # ------------------------------------------------------------------
    # Event pump (worker -> GUI)
    # ------------------------------------------------------------------

    def _pump(self) -> None:
        drained = 0
        try:
            while drained < 200:
                event = self.events.get_nowait()
                drained += 1
                self.handle_event(event)
        except queue.Empty:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Event pump failed: %s", exc)
        finally:
            if not self._closing:
                self._pump_job = self.root.after(_POLL_MS, self._pump)

    def handle_event(self, event: MonitorEvent) -> None:
        """Apply one worker event to the UI.  Pure widget work, no I/O."""
        if event.kind == KIND_TICK:
            self.title_var.set(f"Last title: {event.title}")
            if self._run_is_live() and self.worker is not None:
                # Keep "Watching:" naming the window with the title it has right
                # now, so it never reads as a stale claim about the target.
                self._show_watched(
                    WindowInfo(
                        handle=self.worker.target.handle,
                        title=event.title,
                        process=self.worker.target.process,
                    )
                )
            if event.title not in self._logged_titles:
                self._logged_titles.add(event.title)
                self._append_log(f"title: {event.title}")
            if event.streak:
                self.progress_var.set(
                    f"Matching {event.streak}/{self._dwell_value()} - {event.detail}"
                )
            else:
                self.progress_var.set(event.detail)
            if self.state == STATE_WARMING and event.armed:
                self._apply_state(STATE_MONITORING)
            elif self.state == STATE_MONITORING and not event.armed:
                self._apply_state(STATE_WARMING)
        elif event.kind == KIND_MATCH:
            self._on_trigger(event)
        elif event.kind == KIND_TARGET_LOST:
            self.progress_var.set(event.detail)
            self._append_log(f"WARNING: {event.detail}")
        elif event.kind == KIND_TARGET_REATTACHED:
            self._append_log(event.detail)
            self.progress_var.set(event.detail)
            if event.handle:
                # The "Watching:" line is the user's only proof of what is being
                # read, so it has to follow a re-attach.
                moved = WindowInfo(handle=event.handle, title=event.title)
                self.target = moved
                self._show_watched(moved)
        elif event.kind == KIND_ERROR:
            self.progress_var.set(f"Poll error: {event.detail}")
            self._append_log(f"ERROR: {event.detail}")
        elif event.kind == KIND_STOPPED:
            if self.state in (STATE_WARMING, STATE_MONITORING):
                self._apply_state(STATE_IDLE)
                self._append_log("Monitor thread finished.")
        else:  # pragma: no cover - unknown kinds are logged, not fatal
            self._append_log(f"Unhandled event: {event.kind}")

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------

    def _apply_state(self, state: str) -> None:
        """Apply one run state to the widgets it governs."""
        self.state = state
        self.status_var.set(status_text(state))
        running = is_running(state)
        live = is_live(state)
        self.start_button.configure(state="disabled" if live else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        # Lock the target picker whenever a run is in flight, so the panel and
        # the monitor can never advertise different windows.
        self.window_box.configure(state="disabled" if live else "readonly")
        self.refresh_button.configure(state="disabled" if live else "normal")
        if live and self.worker is not None:
            self._show_watched(self.worker.target)
        elif state != STATE_TRIGGERED:
            self._show_watched(None)
        self.status_label.configure(foreground=status_colour(state))

    def _append_log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        # Keep the pane bounded so an all-night run cannot eat memory.
        try:
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > _MAX_LOG_LINES:
                self.log_text.delete("1.0", f"{lines - _MAX_LOG_LINES}.0")
        except (ValueError, tk.TclError):  # pragma: no cover
            pass
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _persist(self) -> None:
        target = self._selected_window()
        self.settings.triggers = parse_triggers(self.trigger_var.get())
        self.settings.match_mode = self.match_var.get()
        self.settings.case_sensitive = bool(self.case_var.get())
        self.settings.require_transition = bool(self.transition_var.get())
        self.settings.interval_seconds = self._interval_value()
        self.settings.dwell_checks = self._dwell_value()
        self.settings.shutdown_delay_seconds = self._delay_value()
        self.settings.dry_run = bool(self.dry_run_var.get())
        if target is not None:
            self.settings.last_process = target.process
            self.settings.last_title_hint = target.title
        save_settings(self.settings)

    def _save_preferences_only(self) -> None:
        """Persist form values without claiming a target when one is absent."""
        self.settings.triggers = parse_triggers(self.trigger_var.get()) or self.settings.triggers
        self.settings.match_mode = self.match_var.get()
        self.settings.case_sensitive = bool(self.case_var.get())
        self.settings.require_transition = bool(self.transition_var.get())
        self.settings.interval_seconds = self._interval_value()
        self.settings.dwell_checks = self._dwell_value()
        self.settings.shutdown_delay_seconds = self._delay_value()
        self.settings.dry_run = bool(self.dry_run_var.get())
        save_settings(self.settings)

    def _on_close(self) -> None:
        """Shut down cleanly -- and never leave an unattended pending shutdown."""
        self._closing = True
        if self.state == STATE_TRIGGERED:
            # The countdown dialog is the only way to cancel, so cancel for the
            # user rather than leaving a scheduled shutdown with no UI.
            result = self._make_power().abort_shutdown()
            self._append_log(f"$ {result.command_line}")
            self._append_log(f"  -> rc={result.returncode} {result.message}")
        self._close_countdown()
        if self.worker is not None:
            self.worker.request_stop()
            self.stop_event.set()
            self.worker.join(timeout=2.0)
            self.worker = None
        if self._pump_job is not None:
            try:
                self.root.after_cancel(self._pump_job)
            except tk.TclError:  # pragma: no cover
                pass
        self._save_preferences_only()
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cheski",
        description="Watch a window title bar and shut the PC down when the trigger appears.",
    )
    parser.add_argument("--version", action="version", version=f"Cheski Auto Shutdown {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log the shutdown command instead of running it (recommended for a first trial)",
    )
    parser.add_argument(
        "--trigger",
        action="append",
        metavar="WORD",
        help="trigger word; repeat the flag or pass a comma-separated list",
    )
    parser.add_argument("--seconds", type=int, help="countdown before shutdown (15-600)")
    parser.add_argument("--interval", type=float, help="seconds between title checks (0.5-60)")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging(level=args.log_level, console=sys.stderr is not None)

    if sys.platform != "win32":
        print(
            "Cheski Auto Shutdown uses the Windows shutdown command and window titles, "
            "so it only runs on Windows.",
            file=sys.stderr,
        )
        return 2

    # First launch: rehearse before reality.  The command is logged, not run,
    # so the whole arm -> fire -> abort path can be watched safely.
    first_launch = not config_path().exists()
    settings = load_settings()
    if first_launch:
        settings.dry_run = True
        log.info("First launch detected - starting in dry-run mode.")
    if args.dry_run:
        settings.dry_run = True
    if args.trigger:
        parsed = parse_triggers(args.trigger)
        if parsed:
            settings.triggers = parsed
    if args.interval is not None:
        settings.interval_seconds = round(clamp(args.interval, *INTERVAL_RANGE), 2)
    if args.seconds is not None:
        settings.shutdown_delay_seconds = int(clamp(args.seconds, *DELAY_RANGE))

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Could not start the GUI: {exc}", file=sys.stderr)
        return 3

    CheskiApp(root, settings=settings, force_dry_run=bool(args.dry_run))
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
