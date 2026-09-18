"""tkinter user interface for Cheski Auto Shutdown — Terminal Precision design.

Threading contract is unchanged: this module owns the Tk root and is the only
place that touches widgets.  :class:`~cheski.monitor.MonitorThread` posts
:class:`~cheski.monitor.MonitorEvent` objects onto a queue, and
:meth:`CheskiApp._pump` drains that queue from the Tk event loop via
``root.after``.  Nothing else may call into Tk.

Layout (from the design system): custom chrome header; left column — process
picker, trigger chips, timing metric cards, dry-run banner; right strip —
status ring card, watched title, countdown, terminal log, abort pill.
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
    SUGGESTED_TRIGGERS,
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
from . import theme
from .chrome import WindowChrome, add_resize_grip
from .processes import ProcessRow, build_rows
from .state import (
    STATE_ABORTED,
    STATE_IDLE,
    STATE_MONITORING,
    STATE_TRIGGERED,
    STATE_WARMING,
    countdown_body,
    is_live,
    is_running,
    status_text,
)
from .widgets import (
    AbortPill,
    DryRunBanner,
    GlyphToggle,
    MetricCard,
    ProcessPicker,
    Segmented,
    StatusRing,
    TagChipField,
    TerminalLog,
    TriggerSuggestions,
    toast,
)

log = logging.getLogger("cheski.ui")

_POLL_MS = 150

# Ring state per app state (design: Idle / Active / Confirmed / Executing).
RING_STATE = {
    STATE_IDLE: "idle",
    STATE_ABORTED: "idle",
    STATE_WARMING: "active",
    STATE_MONITORING: "active",
    STATE_TRIGGERED: "executing",
}

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
        self._closing = False
        self._pump_job: str | None = None
        self._countdown_job: str | None = None
        self._seconds_left = 0

        theme.init_theme(root)
        self.chrome = WindowChrome(root, "Cheski Auto Shutdown", __version__)
        self.grip = add_resize_grip(root)

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_windows()
        self._pump_job = self.root.after(_POLL_MS, self._pump)
        self._apply_state(STATE_IDLE)
        self.log_panel.append(f"Cheski Auto Shutdown {__version__} ready.")
        log.info("GUI ready (dry_run=%s)", self.dry_run)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        body = tk.Frame(self.root, bg=theme.SURFACE)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=theme.SURFACE)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=12)
        right = tk.Frame(body, bg=theme.SURFACE)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=12)

        # -- left: target picker ----------------------------------------
        self._section_label(left, "Window to watch")
        self.picker = ProcessPicker(left, on_select=self._on_row_selected,
                                    on_refresh=self.refresh_windows)
        self.picker.pack(fill="x")

        # -- left: trigger words ----------------------------------------
        self._section_label(left, "Trigger words")
        chips_row = tk.Frame(left, bg=theme.SURFACE)
        chips_row.pack(fill="x")
        self.chips = TagChipField(chips_row, on_change=self._on_triggers_changed)
        self.chips.pack(side="left", fill="x", expand=True)
        self.segmented = Segmented(chips_row, ["any", "all"], value=self.settings.match_mode,
                                   on_change=self._on_match_mode_changed, width=110)
        self.segmented.pack(side="left", padx=(8, 0))
        self.case_toggle = GlyphToggle(chips_row, "Aa", value=self.settings.case_sensitive,
                                       on_change=self._on_case_changed, tooltip="Match exact case")
        self.case_toggle.pack(side="left", padx=(8, 0))
        self.transition_toggle = GlyphToggle(chips_row, "⟳", value=self.settings.require_transition,
                                             on_change=self._on_transition_changed,
                                             tooltip="Only fire after the trigger clears once (recommended)")
        self.transition_toggle.pack(side="left", padx=(8, 0))
        self.suggestions = TriggerSuggestions(
            left, suggestions=SUGGESTED_TRIGGERS, on_add=self._add_suggested_trigger
        )
        self.suggestions.pack(fill="x")
        self.chips.set_tags(self.settings.triggers)

        # -- left: timing cards ------------------------------------------
        self._section_label(left, "Timing & safety")
        cards = tk.Frame(left, bg=theme.SURFACE)
        cards.pack(fill="x")
        for col in range(3):
            cards.columnconfigure(col, weight=1, uniform="metric")
        self.interval_card = MetricCard(
            cards, label="Check interval", unit="seconds",
            value=self.settings.interval_seconds, limits=INTERVAL_RANGE, step=0.5,
            fmt=lambda v: f"{v:g}",
            on_committed=lambda _v: self._save_preferences_only(),
        )
        self.interval_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.dwell_card = MetricCard(
            cards, label="Confirm passes", unit="checks",
            value=self.settings.dwell_checks, limits=DWELL_RANGE, step=1,
            fmt=lambda v: f"{v:.0f}",
            on_committed=lambda _v: self._save_preferences_only(),
        )
        self.dwell_card.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self.delay_card = MetricCard(
            cards, label="Grace period", unit="seconds",
            value=self.settings.shutdown_delay_seconds, limits=DELAY_RANGE, step=15,
            fmt=lambda v: f"{v:.0f}", warn_below=30,
            on_committed=lambda _v: self._save_preferences_only(),
        )
        self.delay_card.grid(row=0, column=2, sticky="nsew")

        # -- left: dry-run banner ----------------------------------------
        # -- left: execution mode -----------------------------------------
        self._section_label(left, "Execution mode")
        scope_row = tk.Frame(left, bg=theme.SURFACE)
        scope_row.pack(fill="x", pady=(0, 4))
        self.scope_toggle = GlyphToggle(
            scope_row, "⤢", value=bool(self.settings.watch_process_windows),
            on_change=self._on_scope_changed,
            tooltip="Watch every window of the app, so a completion popup on its own fires the trigger",
        )
        self.scope_toggle.pack(side="left")
        tk.Label(
            scope_row, text="watch all windows of the app",
            bg=theme.SURFACE, fg=theme.TEXT_SECONDARY, font=theme.font("body_small"),
        ).pack(side="left", padx=(8, 0))

        self.dry_banner = DryRunBanner(left, value=self._initial_dry_run(),
                                       on_change=self._on_dry_run_changed)
        self.dry_banner.pack(fill="x")
        if self.force_dry_run:
            self.dry_banner.set_value(True)
            self.dry_banner.set_enabled(False)

        # -- left: run controls -------------------------------------------
        controls = tk.Frame(left, bg=theme.SURFACE)
        controls.pack(fill="x", pady=(12, 0))
        self.start_button = tk.Button(
            controls, text="▶  Start monitoring", command=self.start_monitoring,
            bg=theme.PRIMARY_CONTAINER, fg=theme.ON_PRIMARY,
            activebackground="#0891b2", activeforeground=theme.ON_PRIMARY,
            relief="flat", bd=0, font=theme.font("title"), padx=14, pady=7,
            cursor="hand2",
        )
        self.start_button.pack(side="left")
        self.start_button.bind("<Button-1>", self._start_press)
        self.stop_button = tk.Button(
            controls, text="Stop", command=self.stop_monitoring,
            bg=theme.SURFACE_CONTAINER_HIGH, fg=theme.TEXT_HIGH,
            activebackground=theme.SURFACE_CONTAINER_HIGHEST,
            relief="flat", bd=0, font=theme.font("body"), padx=14, pady=7,
            state="disabled", cursor="hand2",
        )
        self.stop_button.pack(side="left", padx=8)
        self.progress_bar = ttk.Progressbar(
            controls, orient="horizontal", mode="determinate", length=140,
            style="Horizontal.TProgressbar",
        )
        self.progress_bar.pack(side="left", padx=(8, 0))
        self.progress_bar["value"] = 0

        # -- right: status strip ------------------------------------------
        self._section_label(right, "Status")
        ring_card = tk.Frame(right, bg=theme.SURFACE_CONTAINER_LOW,
                             highlightthickness=1, highlightbackground=theme.BORDER)
        ring_card.pack(fill="x")
        self.ring = StatusRing(ring_card)
        self.ring.pack(pady=(10, 2))

        self.watched_label = tk.Label(
            ring_card, text="Nothing watched yet", bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_SECONDARY, font=theme.font("body_small"), wraplength=240,
        )
        self.watched_label.pack(padx=10, anchor="w")
        self.title_label = tk.Label(
            ring_card, text="last title: —", bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_MUTED, font=(theme.mono_family(), 8),
            wraplength=240, anchor="w", justify="left",
        )
        self.title_label.pack(padx=10, pady=(2, 8), anchor="w")

        self.countdown_label = tk.Label(
            ring_card, text="", bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.CRIMSON, font=theme.font("timer"),
        )
        self.countdown_label.pack(pady=(0, 8))
        self.countdown_label.pack_forget()

        self.log_panel = TerminalLog(right, height=6)
        self.log_panel.pack(fill="both", expand=True, pady=(10, 0))

        self.abort_pill = AbortPill(right, on_abort=self.abort_shutdown)
        self.abort_pill.pack(fill="x", pady=(10, 0))

    def _section_label(self, parent, text: str) -> None:
        tk.Label(
            parent, text=text.upper(), bg=theme.SURFACE, fg=theme.TEXT_MUTED,
            font=theme.font("label_caps"),
        ).pack(anchor="w", pady=(12, 4))

    # ------------------------------------------------------------------
    # Derived settings from the new widgets
    # ------------------------------------------------------------------

    def _initial_dry_run(self) -> bool:
        return bool(self.settings.dry_run or self.force_dry_run)

    @property
    def dry_run(self) -> bool:
        return bool(self.dry_banner.value)

    def _on_triggers_changed(self) -> None:
        if hasattr(self, "suggestions"):
            self.suggestions.refresh(self.chips.tags())
        self._save_preferences_only()

    def _add_suggested_trigger(self, word: str) -> None:
        """One click on a suggested word arms it like typed input."""
        self.chips.set_tags([*self.chips.tags(), word])

    def _on_match_mode_changed(self, value: str) -> None:
        self.settings.match_mode = value
        self._save_preferences_only()

    def _on_case_changed(self, value: bool) -> None:
        self.settings.case_sensitive = bool(value)
        self._save_preferences_only()

    def _on_transition_changed(self, value: bool) -> None:
        self.settings.require_transition = bool(value)
        self._save_preferences_only()

    def _on_scope_changed(self, value: bool) -> None:
        self.settings.watch_process_windows = bool(value)
        self._save_preferences_only()

    def _on_dry_run_changed(self, value: bool) -> None:
        if self.force_dry_run:
            self.dry_banner.set_value(True)
            return
        self._save_preferences_only()
        self.log_panel.append(
            "DRY RUN is on: the shutdown command will be logged, not executed."
            if value else
            "LIVE MODE: the shutdown command will really execute. Be sure before arming.",
            level="warn" if not value else "info",
        )

    # ------------------------------------------------------------------
    # Window list
    # ------------------------------------------------------------------

    def refresh_windows(self) -> None:
        """Re-scan windows and reload the picker rows.

        The user's choice is never overwritten; a row is preselected only when
        a previous session's target is recognised.
        """
        self.picker.set_scanning(True)
        windows = self.source.list_windows()
        rows = build_rows(windows, own_pid=os.getpid())
        self.picker.set_rows(rows)
        self.log_panel.append(f"Found {len(rows)} window(s).")
        selected = self.picker.selected_row()
        if selected is not None:
            self.target = selected.info
        else:
            self._restore_remembered(rows)

    def _restore_remembered(self, rows: list[ProcessRow]) -> None:
        wanted = (self.settings.last_process or "").casefold()
        if not wanted:
            return
        matches = [row for row in rows if wanted in (row.process or "").casefold()]
        if not matches:
            return
        hint = (self.settings.last_title_hint or "").casefold()
        if hint:
            matches.sort(key=lambda row: hint not in row.display_name.casefold())
        self.picker.select_row(matches[0])

    def _on_row_selected(self, row: ProcessRow) -> None:
        self.target = row.info
        self.log_panel.append(f"Selected: {row.display_name} ({row.process}, pid {row.pid})")

    def _selected_window(self) -> WindowInfo | None:
        if self.target is not None:
            return self.target
        row = self.picker.selected_row()
        return row.info if row is not None else None

    # ------------------------------------------------------------------
    # Monitoring lifecycle
    # ------------------------------------------------------------------

    def _worker_running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _run_is_live(self) -> bool:
        """True while a run is in flight (arming, watching, or counting down)."""
        return is_live(self.state) or self._worker_running()

    def start_monitoring(self) -> bool:
        """Validate the form and start the worker thread."""
        if self._worker_running():
            return False

        triggers = self.chips.tags()
        if not triggers:
            self.log_panel.append("Enter at least one trigger word first.", level="warn")
            toast(self.root, "Add a trigger word first", kind="warn")
            return False

        target = self._selected_window()
        if target is None:
            self.log_panel.append("No window selected — pick one from the list.", level="warn")
            toast(self.root, "Pick a window to watch", kind="warn")
            return False

        config = TriggerConfig(
            triggers=triggers,
            match_mode=self.segmented.value,
            case_sensitive=self.case_toggle.value,
            dwell_checks=int(self.dwell_card.get()),
            require_transition=self.transition_toggle.value,
        )
        self.engine = TriggerEngine(config)
        self.engine.reset()
        self.stop_event = threading.Event()
        self.events = queue.Queue()

        self.worker = MonitorThread(
            self.source,
            target,
            self.engine,
            interval=max(0.2, float(self.interval_card.get())),
            events=self.events,
            stop_event=self.stop_event,
            watch_process=bool(self.settings.watch_process_windows),
        )
        self.worker.start()
        self._show_watched(f"{target.title or '(untitled)'}  [handle {target.handle}]")

        self._persist()
        self.log_panel.append(
            "Monitoring '{title}' for {words} (mode={mode}, every {interval}s, "
            "confirm after {dwell} check(s)).".format(
                title=target.title or "(untitled)",
                words=", ".join(triggers),
                mode=config.match_mode,
                interval=self.interval_card.get(),
                dwell=config.dwell_checks,
            )
        )
        if self.dry_run:
            self.log_panel.append("DRY RUN is on: the shutdown command will be logged, not executed.")
        self._apply_state(STATE_WARMING)
        return True

    def _start_press(self, _event=None) -> None:
        """Fill the arming progress bar; it drains once armed or stopped."""
        if self._worker_running():
            return
        self.progress_bar.configure(maximum=100, value=0)
        self._arm_fill(0)

    def _arm_fill(self, value: float) -> None:
        if self._closing:
            return
        if self.state in (STATE_WARMING, STATE_MONITORING):
            self.progress_bar["value"] = min(100, value + 8)
            self._pump_job = self.root.after(60, lambda: self._arm_fill(value + 8))
        elif self.state == STATE_IDLE:
            self.progress_bar["value"] = 0

    def stop_monitoring(self) -> None:
        """Stop the worker thread.  Does not touch any pending shutdown."""
        self._stop_worker()
        self.log_panel.append("Monitoring stopped.")
        if self.state != STATE_TRIGGERED:
            # A pending countdown outranks Stop: stay TRIGGERED until the user
            # aborts (or the timer runs out), otherwise the panel would claim
            # "Idle" while a shutdown is still scheduled.
            self._apply_state(STATE_IDLE)
        self.progress_bar["value"] = 0

    def _stop_worker(self) -> None:
        if self.worker is not None:
            self.worker.request_stop()
            self.stop_event.set()
            self.worker = None

    def abort_shutdown(self) -> None:
        """Run ``shutdown /a`` -- cancels a pending shutdown."""
        result = self._make_power().abort_shutdown()
        self.log_panel.append(f"$ {result.command_line}")
        self.log_panel.append(f"  -> rc={result.returncode} {result.message}")
        self._cancel_countdown()
        if result.ok:
            self._apply_state(STATE_ABORTED)
            toast(self.root, "Shutdown cancelled", kind="info")
        else:
            if result.returncode == 1116:
                # Nothing was pending, so nothing is going to happen either.
                self._apply_state(STATE_IDLE)
            toast(self.root, "Nothing to abort", kind="warn")

    def _make_power(self) -> PowerController:
        return self.power_factory(
            seconds=int(self.delay_card.get()), dry_run=self.dry_run
        )

    # ------------------------------------------------------------------
    # Triggered: schedule the shutdown and run the countdown
    # ------------------------------------------------------------------

    def _on_trigger(self, event: MonitorEvent) -> None:
        """Schedule the shutdown and start the in-panel countdown.

        The countdown lives in the status strip and the abort pill is the only
        way to cancel, so the closing handler aborts for the user rather than
        leaving a scheduled shutdown with no UI.
        """
        self._apply_state(STATE_TRIGGERED)
        self.log_panel.append(f"TRIGGER MATCHED: {event.title!r} ({event.detail})", level="warn")
        self._beep()

        seconds = int(self.delay_card.get())
        result = self._make_power().schedule_shutdown()
        self.log_panel.append(f"$ {result.command_line}")
        self.log_panel.append(f"  -> rc={result.returncode} {result.message}")

        if not result.ok:
            # Nothing was scheduled, so there is nothing to count down to.
            self._apply_state(STATE_IDLE)
            messagebox.showerror(
                "Could not schedule the shutdown",
                f"{result.message}\n\nThe command was:\n{result.command_line}",
            )
            return

        self._stop_worker()
        self._seconds_left = seconds
        self.countdown_label.configure(
            text=self._mmss(self._seconds_left), fg=theme.CRIMSON
        )
        self.countdown_label.pack(pady=(0, 8))
        self._tick_countdown()

    @staticmethod
    def _mmss(total: int) -> str:
        return f"{total // 60:02d}:{total % 60:02d}"

    def _tick_countdown(self) -> None:
        if self._closing or self.state != STATE_TRIGGERED:
            return
        if self._seconds_left <= 0:
            self.countdown_label.configure(text="Shutting down now…")
            return
        self.countdown_label.configure(text=self._mmss(self._seconds_left))
        self._seconds_left -= 1
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    def _cancel_countdown(self) -> None:
        if self._countdown_job is not None:
            try:
                self.root.after_cancel(self._countdown_job)
            except tk.TclError:  # pragma: no cover
                pass
            self._countdown_job = None
        self.countdown_label.pack_forget()

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
            self.title_label.configure(text=f"last title: {event.title}")
            if self._run_is_live() and self.worker is not None:
                self._show_watched(
                    f"{event.title or '(untitled)'}  [handle {self.worker.target.handle}]"
                )
            if event.streak:
                self.watched_label.configure(
                    text=f"matching {event.streak}/{int(self.dwell_card.get())} — {event.detail}"
                )
            elif event.detail:
                self.watched_label.configure(text=event.detail)
            if self.state == STATE_WARMING and event.armed:
                self._apply_state(STATE_MONITORING)
            elif self.state == STATE_MONITORING and not event.armed:
                self._apply_state(STATE_WARMING)
        elif event.kind == KIND_MATCH:
            self._on_trigger(event)
        elif event.kind == KIND_TARGET_LOST:
            self.watched_label.configure(text=event.detail)
            self.log_panel.append(f"WARNING: {event.detail}", level="warn")
        elif event.kind == KIND_TARGET_REATTACHED:
            self.log_panel.append(event.detail)
            self.watched_label.configure(text=event.detail)
            if event.handle:
                moved = WindowInfo(handle=event.handle, title=event.title)
                self.target = moved
                self._show_watched(f"{event.title or '(untitled)'}  [handle {event.handle}]")
        elif event.kind == KIND_ERROR:
            self.watched_label.configure(text=f"Poll error: {event.detail}")
            self.log_panel.append(f"ERROR: {event.detail}", level="error")
        elif event.kind == KIND_STOPPED:
            if self.state in (STATE_WARMING, STATE_MONITORING):
                self._apply_state(STATE_IDLE)
                self.log_panel.append("Monitor thread finished.")

    def _show_watched(self, text: str) -> None:
        self.watched_label.configure(text=text)

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------

    def _apply_state(self, state: str) -> None:
        """Apply one run state to the widgets it governs."""
        self.state = state
        self.ring.set_state(RING_STATE.get(state, "idle"))
        running = is_running(state)
        live = is_live(state)
        self.start_button.configure(state="disabled" if live else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        # Lock the picker whenever a run is in flight, so the panel and the
        # monitor can never advertise different windows.
        picker_state = "disabled" if live else "normal"
        self.picker.search_entry.configure(state=picker_state)
        for child in self.picker.inner.winfo_children():
            try:
                child.configure(state=picker_state)
            except tk.TclError:
                pass
        if live and self.worker is not None:
            self._show_watched(
                f"{self.worker.target.title or '(untitled)'}  [handle {self.worker.target.handle}]"
            )
        elif state != STATE_TRIGGERED and state != STATE_ABORTED:
            self._show_watched("Nothing watched yet" if state == STATE_IDLE else self.watched_label.cget("text"))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        target = self._selected_window()
        self.settings.triggers = self.chips.tags()
        self.settings.match_mode = self.segmented.value
        self.settings.case_sensitive = bool(self.case_toggle.value)
        self.settings.require_transition = bool(self.transition_toggle.value)
        self.settings.interval_seconds = float(self.interval_card.get())
        self.settings.dwell_checks = int(self.dwell_card.get())
        self.settings.shutdown_delay_seconds = int(self.delay_card.get())
        self.settings.dry_run = self.dry_run
        if target is not None:
            self.settings.last_process = target.process
            self.settings.last_title_hint = target.title
        save_settings(self.settings)

    def _save_preferences_only(self) -> None:
        """Persist form values without claiming a target when one is absent."""
        if not hasattr(self, "interval_card"):
            return  # widgets still under construction; nothing to persist yet
        self.settings.triggers = self.chips.tags() or self.settings.triggers
        self.settings.match_mode = self.segmented.value
        self.settings.case_sensitive = bool(self.case_toggle.value)
        self.settings.require_transition = bool(self.transition_toggle.value)
        self.settings.interval_seconds = float(self.interval_card.get())
        self.settings.dwell_checks = int(self.dwell_card.get())
        self.settings.shutdown_delay_seconds = int(self.delay_card.get())
        self.settings.dry_run = self.dry_run
        save_settings(self.settings)

    def _on_close(self) -> None:
        """Shut down cleanly -- and never leave an unattended pending shutdown."""
        self._closing = True
        if self.state == STATE_TRIGGERED:
            # The abort pill is the only way to cancel, so cancel for the user
            # rather than leaving a scheduled shutdown with no UI.
            result = self._make_power().abort_shutdown()
            self.log_panel.append(f"$ {result.command_line}")
            self.log_panel.append(f"  -> rc={result.returncode} {result.message}")
        self._cancel_countdown()
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
