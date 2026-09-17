"""The Terminal Precision widget set.

Canvas-drawn components that stock tkinter cannot express, each owning one
visual concern and exposing a small, testable API:

- :class:`ProcessPicker` — searchable list with icons, pid and memory
- :class:`TagChipField` — removable keyword pills with an add-entry
- :class:`Segmented` — ANY/ALL switch (segmented control, not radios)
- :class:`GlyphToggle` — icon-button toggle (the ``Aa`` case switch)
- :class:`MetricCard` — large number + unit + ± steppers, count-up animation,
  amber pulse under a threshold (the grace-period card)
- :class:`DryRunBanner` — flask toggle banner with SAFE/LIVE badge
- :class:`StatusRing` — animated monitoring-state ring (idle/active speeds)
- :class:`TerminalLog` — timestamped coloured log, auto-scroll lock, copy-all
- :class:`AbortPill` — pinned abort button with shake + toast feedback
- :func:`toast` — transient confirmation pill

Every widget is theme-only: none of them read settings, touch the monitor
thread, or know about shutdown semantics.  ``app.py`` does the wiring.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import font as tkfont

from . import theme
from .icons import extract_icon, fallback_avatar
from .processes import ProcessRow
from .theme import blend, with_alpha


def _mono(size: int, weight: str = "normal") -> tuple:
    return (theme.mono_family(), size, weight)


# ---------------------------------------------------------------------------
# Process picker
# ---------------------------------------------------------------------------


class ProcessPicker(tk.Frame):
    """Searchable process list with icons, pid and memory.

    Callbacks: ``on_select(row)`` (double-click / Enter), ``on_refresh()``
    (refresh button).  ``set_rows()`` reloads the list with a short fade;
    ``set_scanning(True)`` shows the scanning pulse and empties the list.
    """

    ROW_H = 44
    FADE_MS = 140

    def __init__(self, master, *, on_select, on_refresh, height=4):
        super().__init__(master, bg=theme.SURFACE, bd=0, highlightthickness=0)
        self.on_select = on_select
        self.on_refresh = on_refresh
        self._rows: list[ProcessRow] = []
        self._selected: ProcessRow | None = None
        self._filter = ""
        self._icons: dict[int, object] = {}
        self._fade_job = None

        search_bar = tk.Frame(self, bg=theme.SURFACE)
        search_bar.pack(fill="x", pady=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_filter())
        self.search_entry = tk.Entry(
            search_bar,
            textvariable=self.search_var,
            bg="#0f1219", fg=theme.TEXT_HIGH,
            insertbackground=theme.PRIMARY,
            relief="flat",
            font=theme.font("body"),
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.PRIMARY_CONTAINER,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self._placeholder = "  Search processes…"
        self._placeholder_on = True
        self._show_placeholder()
        self.search_entry.bind("<FocusIn>", self._hide_placeholder)
        self.search_entry.bind("<FocusOut>", self._show_placeholder)

        self.refresh_button = tk.Canvas(
            search_bar, width=30, height=30, bg="#0f1219",
            highlightthickness=1, highlightbackground=theme.BORDER,
            cursor="hand2",
        )
        self.refresh_button.pack(side="left", padx=(6, 0))
        self._draw_refresh_icon()
        self.refresh_button.bind("<Button-1>", lambda _e: self._pulse_refresh())

        wrap = tk.Frame(self, bg=theme.SURFACE)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            wrap, bg=theme.SURFACE_LOWEST, highlightthickness=1,
            highlightbackground=theme.BORDER, height=height * self.ROW_H,
        )
        self.scrollbar = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=theme.SURFACE_LOWEST)
        self._inner_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.row_frames: list[tk.Frame] = []
        self._make_scanning_label()
        self._scanning = False

    # -- public API -------------------------------------------------------

    def set_rows(self, rows: list[ProcessRow]) -> None:
        """Replace the list contents (with a quick fade-in)."""
        self._rows = rows
        self.set_scanning(False)
        self._rebuild(fade=True)

    def set_scanning(self, scanning: bool) -> None:
        self._stop_scanning_pulse()
        self._scanning = scanning
        if scanning:
            for child in self.inner.winfo_children():
                if child is not self.scanning_label:
                    child.destroy()
            self.row_frames = []
            if not self.scanning_label.winfo_exists():
                self._make_scanning_label()
            self.scanning_label.configure(text="● scanning")
            self.scanning_label.pack(anchor="w", padx=10, pady=14)
            self._scan_job = self.after(320, self._pulse_scanning)
        else:
            self.scanning_label.pack_forget()

    def _make_scanning_label(self):
        self.scanning_label = tk.Label(
            self.inner, text="● scanning", bg=theme.SURFACE_LOWEST,
            fg=theme.PRIMARY, font=_mono(9, "bold"), anchor="w",
        )

    def _pulse_scanning(self):
        """Animate the scanning dots; stops itself when scanning ends."""
        if not self._scanning or not self.scanning_label.winfo_exists():
            self._scan_job = None
            return
        text = self.scanning_label.cget("text")
        dots = (text.count("·") + 1) % 4
        self.scanning_label.configure(text="● scanning" + " ·" * dots)
        self._scan_job = self.after(320, self._pulse_scanning)

    def selected_row(self) -> ProcessRow | None:
        return self._selected

    def select_row(self, row: ProcessRow) -> None:
        """Programmatic selection (same path a click takes, minus the event)."""
        if row not in self._rows:
            return
        self._selected = row
        self._highlight()
        self.on_select(row)

    # -- internals ---------------------------------------------------------

    def _apply_filter(self):
        if not hasattr(self, "inner"):
            return  # the placeholder insert fires this trace before __init__ finishes
        self._filter = self.search_var.get().strip().casefold()
        self._rebuild(fade=False)

    def _visible_rows(self):
        if not self._filter:
            return self._rows
        return [r for r in self._rows if self._filter in r.search_text]

    def _rebuild(self, *, fade: bool):
        for child in self.inner.winfo_children():
            if child is not self.scanning_label:
                child.destroy()
        self.row_frames = []
        for i, row in enumerate(self._visible_rows()):
            frame = self._build_row(row, i)
            self.row_frames.append(frame)
            if fade:
                frame.configure(bg=with_alpha(theme.PRIMARY_CONTAINER, 0.12))
                self._fade_in(frame, 0)
        self._highlight()
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fade_in(self, frame, step):
        if not frame.winfo_exists():
            return
        t = step / 6
        colour = blend(with_alpha(theme.PRIMARY_CONTAINER, 0.12), theme.SURFACE_LOWEST, t)
        try:
            frame.configure(bg=colour)
        except tk.TclError:
            return
        if step < 6:
            self._fade_job = self.after(self.FADE_MS // 6, lambda: self._fade_in(frame, step + 1))

    def _build_row(self, row: ProcessRow, index: int) -> tk.Frame:
        frame = tk.Frame(self.inner, bg=theme.SURFACE_LOWEST)
        frame.pack(fill="x")
        icon_canvas = tk.Canvas(
            frame, width=28, height=28, bg=theme.SURFACE_LOWEST,
            highlightthickness=0,
        )
        icon_canvas.pack(side="left", padx=(10, 8), pady=8)
        self._draw_icon(icon_canvas, row)
        text_col = tk.Frame(frame, bg=theme.SURFACE_LOWEST)
        text_col.pack(side="left", fill="x", expand=True, pady=6)
        name_label = tk.Label(
            text_col, text=row.display_name, bg=theme.SURFACE_LOWEST,
            fg=theme.TEXT_HIGH, font=theme.font("body"), anchor="w",
        )
        name_label.pack(anchor="w")
        meta = f"{row.process or 'unknown'} · pid {row.pid or '?'} · {row.memory_text}"
        meta_label = tk.Label(
            text_col, text=meta, bg=theme.SURFACE_LOWEST,
            fg=theme.TEXT_SECONDARY, font=_mono(8), anchor="w",
        )
        meta_label.pack(anchor="w")
        for widget in (frame, icon_canvas, text_col, name_label, meta_label):
            widget.bind("<Double-Button-1>", lambda _e, r=row: self._choose(r))
            widget.bind("<Button-1>", lambda _e, r=row, f=frame: self._preview(r, f))
        if index % 2 == 1:
            tk.Frame(frame, bg=theme.BORDER, height=1).pack(fill="x", side="bottom")
        return frame

    def _draw_icon(self, canvas: tk.Canvas, row: ProcessRow):
        image = None
        try:
            image = extract_icon(row.pid)
        except Exception:
            image = None
        if image is None:
            image = fallback_avatar(row.process)
        photo = tk.PhotoImage(getattr(image, "tk", image)) if False else None
        try:
            from PIL import ImageTk

            photo = ImageTk.PhotoImage(image)
        except Exception:  # pragma: no cover - Pillow absent
            photo = None
        if photo is not None:
            self._icons[row.info.handle] = photo  # keep a reference alive
            canvas.create_image(14, 14, image=photo)
        else:  # pragma: no cover - no Pillow: initial letter
            canvas.create_text(
                14, 14, text=(row.display_name[:1] or "?").upper(),
                fill=theme.PRIMARY, font=_mono(11, "bold"),
            )

    def _preview(self, row, frame):
        self._selected = row
        self._highlight()

    def _choose(self, row):
        self._selected = row
        self._highlight()
        self.on_select(row)

    def _highlight(self):
        for frame in self.row_frames:
            if frame.winfo_exists():
                frame.configure(
                    bg=with_alpha(theme.PRIMARY_CONTAINER, 0.16)
                    if self._selected is not None and frame in getattr(self, "_frame_of", {}).get(self._selected, [])
                    else theme.SURFACE_LOWEST
                )

    def _pulse_refresh(self):
        self.refresh_button.delete("all")
        self._draw_refresh_icon(spun=1)
        self.after(220, lambda: (self.refresh_button.delete("all"), self._draw_refresh_icon()))
        self.on_refresh()

    def _draw_refresh_icon(self, spun: int = 0):
        c = self.refresh_button
        colour = theme.PRIMARY if spun else theme.TEXT_SECONDARY
        c.create_arc(8, 8, 22, 22, start=20 + 120 * spun, extent=250,
                     style="arc", outline=colour, width=2)
        c.create_polygon(21, 6, 25, 12, 18, 12, fill=colour, outline=colour)

    def _pulse_scanning(self):
        if not self._scanning:
            return
        current = int(self.scanning_label.cget("text").strip("● ").count("…") * 0)  # no-op
        text = self.scanning_label.cget("text")
        dots = (text.count("·") + 1) % 4
        self.scanning_label.configure(text="● scanning" + " ·" * dots)
        self._scan_job = self.after(320, self._pulse_scanning)

    def _stop_scanning_pulse(self):
        job = getattr(self, "_scan_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            self._scan_job = None

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._inner_window, width=event.width)

    def _on_mousewheel(self, event):
        if self.winfo_containing(event.x_root, event.y_root) is self.canvas or True:
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _show_placeholder(self, _event=None):
        if not self.search_entry.get():
            self._placeholder_on = True
            self.search_entry.configure(fg=theme.TEXT_MUTED)
            self.search_entry.insert(0, self._placeholder)

    def _hide_placeholder(self, _event=None):
        if self._placeholder_on:
            self._placeholder_on = False
            self.search_entry.configure(fg=theme.TEXT_HIGH)
            self.search_entry.delete(0, "end")


# ---------------------------------------------------------------------------
# Tag chips
# ---------------------------------------------------------------------------


class TagChipField(tk.Frame):
    """Removable keyword pills with an inline add-entry."""

    def __init__(self, master, *, on_change, placeholder="add trigger…"):
        super().__init__(master, bg=theme.SURFACE, bd=0, highlightthickness=0)
        self.on_change = on_change
        self._tags: list[str] = []
        self._chip_canvases: list[tk.Canvas] = []
        self.flow = tk.Frame(self, bg=theme.SURFACE)
        self.flow.pack(fill="x")
        self.entry = tk.Entry(
            self, bg="#0f1219", fg=theme.TEXT_HIGH, relief="flat",
            insertbackground=theme.PRIMARY, font=theme.font("body"),
            highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.PRIMARY_CONTAINER,
        )
        self.entry.pack(fill="x", ipady=3, pady=(6, 0))
        self.entry.bind("<Return>", self._commit)
        self.entry.bind("<FocusOut>", self._commit)
        self.entry.bind("<FocusIn>", self._hide_placeholder)
        self.entry.bind("<space>", self._commit_on_space)
        self._placeholder = placeholder
        self._placeholder_on = False
        self._show_placeholder()

    # -- public API -------------------------------------------------------

    def tags(self) -> tuple[str, ...]:
        return tuple(self._tags)

    def set_tags(self, tags) -> None:
        self._tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
        self._rebuild()
        self.on_change()

    # -- internals ---------------------------------------------------------

    def _commit_on_space(self, _event):
        self._commit()
        return "break"

    def _commit(self, _event=None):
        raw = self.entry.get()
        if self._placeholder_on and raw.strip() == self._placeholder.strip():
            raw = ""
        for part in raw.replace(";", ",").replace("|", ",").split(","):
            part = part.strip()
            if part and part.casefold() not in [t.casefold() for t in self._tags]:
                self._tags.append(part)
        self.entry.delete(0, "end")
        self._show_placeholder()
        self._rebuild()
        self.on_change()
        return "break"

    def _remove(self, tag: str):
        self._tags = [t for t in self._tags if t != tag]
        self._rebuild()
        self.on_change()

    def _show_placeholder(self):
        if not self.entry.get() and not self._placeholder_on:
            self._placeholder_on = True
            self.entry.configure(fg=theme.TEXT_MUTED)
            self.entry.insert(0, self._placeholder)

    def _hide_placeholder(self):
        if self._placeholder_on:
            self._placeholder_on = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=theme.TEXT_HIGH)

    def _rebuild(self):
        for chip in self._chip_canvases:
            chip.destroy()
        self._chip_canvases = []
        for tag in self._tags:
            chip = self._make_chip(tag)
            self._chip_canvases.append(chip)
        if self._tags:
            self.entry.configure(fg=theme.TEXT_HIGH)

    def _make_chip(self, tag: str) -> tk.Canvas:
        font = theme.font("body_small")
        measure = tkfont.Font(font=font)
        width = measure.measure(tag) + 34
        chip = tk.Canvas(
            self.flow, width=width, height=24, bg=theme.SURFACE,
            highlightthickness=1, highlightbackground=with_alpha(theme.PRIMARY_CONTAINER, 0.3),
            cursor="hand2",
        )
        chip.pack(side="left", padx=(0, 6), pady=2)
        chip.create_rectangle(0, 0, width, 24, fill=with_alpha(theme.PRIMARY_CONTAINER, 0.12), width=0)
        chip.create_text(10, 12, text=tag, anchor="w", fill=theme.TEXT_HIGH, font=font)
        chip.create_text(width - 14, 12, text="✕", fill=theme.TEXT_SECONDARY, font=_mono(8, "bold"))
        chip.tag_bind("all", "<Enter>", lambda _e: chip.configure(highlightbackground=theme.PRIMARY_CONTAINER))
        chip.tag_bind("all", "<Leave>", lambda _e: chip.configure(highlightbackground=with_alpha(theme.PRIMARY_CONTAINER, 0.3)))
        chip.bind("<Button-1>", lambda _e: self._remove(tag))
        return chip


# ---------------------------------------------------------------------------
# Segmented control / glyph toggle
# ---------------------------------------------------------------------------


class Segmented(tk.Canvas):
    """Two-option segmented switch (ANY/ALL).  ``on_change(value)``."""

    def __init__(self, master, options, *, value, on_change, width=132):
        super().__init__(master, width=width, height=26, bg=theme.SURFACE,
                         highlightthickness=0)
        self.options = list(options)
        self.value = value
        self.on_change = on_change
        self.seg_w = width // len(self.options)
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_value(self, value, notify=False):
        self.value = value
        self._draw()
        if notify:
            self.on_change(value)

    def _seg_at(self, x):
        index = int(x / self.seg_w)
        return self.options[max(0, min(index, len(self.options) - 1))]

    def _click(self, event):
        chosen = self._seg_at(event.x)
        if chosen != self.value:
            self.set_value(chosen, notify=True)

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), 26
        self.create_rectangle(0, 0, w, h, fill="#0f1219", width=1, outline=theme.BORDER)
        for i, option in enumerate(self.options):
            x0, x1 = i * self.seg_w, (i + 1) * self.seg_w
            if option == self.value:
                self.create_rectangle(
                    x0 + 2, 2, x1 - 2, h - 2,
                    fill=with_alpha(theme.PRIMARY_CONTAINER, 0.22),
                    outline=theme.PRIMARY_CONTAINER,
                )
                fill = theme.PRIMARY
            else:
                fill = theme.TEXT_SECONDARY
            self.create_text(
                (x0 + x1) // 2, h // 2, text=option.upper(),
                fill=fill, font=_mono(9, "bold"),
            )


class GlyphToggle(tk.Canvas):
    """Small icon-button toggle (e.g. the ``Aa`` case switch)."""

    def __init__(self, master, glyph: str, *, value: bool, on_change, tooltip=""):
        super().__init__(master, width=34, height=26, bg=theme.SURFACE,
                         highlightthickness=1, highlightbackground=theme.BORDER,
                         cursor="hand2")
        self.glyph = glyph
        self.value = value
        self.on_change = on_change
        self.tooltip = tooltip
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self.configure(highlightbackground=theme.BORDER_FOCUS))
        self.bind("<Leave>", lambda _e: self.configure(highlightbackground=theme.BORDER))
        self._draw()

    def set_value(self, value, notify=False):
        self.value = value
        self._draw()
        if notify:
            self.on_change(value)

    def _click(self, _event):
        self.set_value(not self.value, notify=True)

    def _draw(self):
        self.delete("all")
        active = self.value
        bg = with_alpha(theme.PRIMARY_CONTAINER, 0.22) if active else "#0f1219"
        fg = theme.PRIMARY if active else theme.TEXT_SECONDARY
        self.create_rectangle(0, 0, 34, 26, fill=bg, width=0)
        self.create_text(17, 13, text=self.glyph, fill=fg, font=_mono(10, "bold"))


# ---------------------------------------------------------------------------
# Metric card
# ---------------------------------------------------------------------------


class MetricCard(tk.Frame):
    """Large number + unit + ± steppers; count animation; threshold pulse.

    ``on_committed(value)`` fires when the user releases a stepper or the
    animated value settles — the app persists from it.
    """

    STEPS = 8
    STEP_MS = 28

    def __init__(self, master, *, label, unit, value, limits, step,
                 fmt=lambda v: f"{v:g}", warn_below=None, on_committed=None):
        super().__init__(master, bg=theme.SURFACE_CONTAINER_LOW,
                         bd=0, highlightthickness=1,
                         highlightbackground=theme.BORDER)
        self.unit = unit
        self.limits = limits
        self.step = step
        self.fmt = fmt
        self.warn_below = warn_below
        self.on_committed = on_committed
        self._value = float(value)
        self._shown = float(value)
        self._anim_job = None
        self._pulse_job = None
        self._pulse_state = 0

        top = tk.Frame(self, bg=theme.SURFACE_CONTAINER_LOW)
        top.pack(fill="x", padx=8, pady=(10, 0))
        self.label_label = tk.Label(
            top, text=label.upper(), bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_SECONDARY, font=_mono(8, "bold"), anchor="w",
        )
        self.label_label.pack(anchor="w", fill="x")
        self.value_label = tk.Label(
            top, text=self.fmt(self._value), bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_HIGH, font=theme.font("timer_idle"),
        )
        self.value_label.pack(anchor="w")
        self.unit_label = tk.Label(
            top, text=unit, bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_MUTED, font=_mono(8),
        )
        self.unit_label.pack(anchor="w")

        btns = tk.Frame(self, bg=theme.SURFACE_CONTAINER_LOW)
        btns.pack(fill="x", padx=12, pady=(2, 10))
        self.minus_btn = self._stepper(btns, "−", self._dec)
        self.plus_btn = self._stepper(btns, "+", self._inc)
        self._apply_warning()

    # -- public API -------------------------------------------------------

    def get(self) -> float:
        return self._value

    def set_value(self, value, animate=True):
        self._value = max(self.limits[0], min(self.limits[1], float(value)))
        if animate:
            self._animate_to(self._value)
        else:
            self._shown = self._value
            self.value_label.configure(text=self.fmt(self._value))
        self._apply_warning()

    def set_warning_below(self, threshold):
        self.warn_below = threshold
        self._apply_warning()

    # -- internals ---------------------------------------------------------

    def _stepper(self, master, glyph, command):
        btn = tk.Label(
            master, text=glyph, bg=theme.SURFACE_CONTAINER, fg=theme.TEXT_HIGH,
            font=_mono(11, "bold"), width=4, cursor="hand2",
        )
        btn.pack(side="left", padx=(0, 6), ipady=2)
        btn.bind("<Button-1>", lambda _e: command())
        return btn

    def _inc(self):
        self.set_value(self._value + self.step)

    def _dec(self):
        self.set_value(self._value - self.step)

    def _animate_to(self, target):
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None
        start = self._shown

        def step_tick(step=0):
            t = (step + 1) / self.STEPS
            self._shown = start + (target - start) * t
            self.value_label.configure(text=self.fmt(self._shown))
            if step + 1 < self.STEPS:
                self._anim_job = self.after(self.STEP_MS, lambda: step_tick(step + 1))
            else:
                self._shown = target
                self.value_label.configure(text=self.fmt(target))
                self._anim_job = None
                if self.on_committed is not None:
                    self.on_committed(self._value)

        step_tick()

    def _apply_warning(self):
        if self._pulse_job is not None:
            try:
                self.after_cancel(self._pulse_job)
            except tk.TclError:
                pass
            self._pulse_job = None
        if self.warn_below is not None and self._value < self.warn_below:
            self._pulse_amber(0)
        else:
            self.configure(highlightbackground=theme.BORDER)
            self.value_label.configure(fg=theme.TEXT_HIGH)

    def _pulse_amber(self, state):
        if self.warn_below is None or self._value >= self.warn_below:
            return
        t = state / 8
        colour = blend(theme.BORDER, theme.TERTIARY_CONTAINER, t)
        self.configure(highlightbackground=colour)
        self.value_label.configure(fg=blend(theme.TEXT_HIGH, theme.TERTIARY, t))
        self._pulse_job = self.after(90, lambda: self._pulse_amber((state + 1) % 17))


# ---------------------------------------------------------------------------
# Dry-run banner
# ---------------------------------------------------------------------------


class DryRunBanner(tk.Frame):
    """Flask toggle banner with SAFE/LIVE badge; amber tint when LIVE."""

    def __init__(self, master, *, value: bool, on_change):
        super().__init__(master, bg=theme.SURFACE_CONTAINER_LOW, bd=0,
                         highlightthickness=1, highlightbackground=theme.BORDER)
        self.value = value
        self.on_change = on_change
        self.icon = tk.Canvas(self, width=34, height=34, bg=theme.SURFACE_CONTAINER_LOW,
                              highlightthickness=0)
        self.icon.pack(side="left", padx=(12, 8), pady=8)
        self._draw_flask(on=not value)
        text = tk.Frame(self, bg=theme.SURFACE_CONTAINER_LOW)
        text.pack(side="left", fill="x", expand=True, pady=6)
        self.title_label = tk.Label(
            text, text="Dry run mode", bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_HIGH, font=theme.font("title"), anchor="w",
        )
        self.title_label.pack(anchor="w")
        self.desc_label = tk.Label(
            text, text="Logs the shutdown command — nothing executes",
            bg=theme.SURFACE_CONTAINER_LOW, fg=theme.TEXT_SECONDARY,
            font=theme.font("body_small"), anchor="w",
        )
        self.desc_label.pack(anchor="w")
        right = tk.Frame(self, bg=theme.SURFACE_CONTAINER_LOW)
        right.pack(side="right", padx=12)
        self.badge = tk.Label(
            right, text="SAFE", bg=with_alpha(theme.SECONDARY_CONTAINER, 0.16),
            fg=theme.SECONDARY, font=_mono(9, "bold"), padx=8, pady=2,
        )
        self.badge.pack(side="left", padx=(0, 8), pady=12)
        self.toggle = tk.Canvas(
            right, width=44, height=22, bg=theme.SURFACE_CONTAINER_LOW,
            highlightthickness=0, cursor="hand2",
        )
        self.toggle.pack(side="left", pady=12)
        self.toggle.bind("<Button-1>", self._clicked)
        self.bind("<Button-1>", self._clicked)
        for child in (text, self.title_label, self.desc_label):
            child.bind("<Button-1>", self._clicked)
        self._redraw()

    def _clicked(self, _event=None):
        self.set_value(not self.value, notify=True)

    def set_value(self, value, notify=False):
        self.value = value
        self._redraw()
        if notify:
            self.on_change(value)

    def set_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.toggle.configure(cursor="arrow" if not enabled else "hand2")
        for widget in (self.title_label, self.desc_label, self.badge):
            widget.configure(state=state)

    def _redraw(self):
        self.icon.delete("all")
        self._draw_flask(on=not self.value)
        self._draw_toggle()
        if self.value:
            self.badge.configure(text="SAFE", bg=with_alpha(theme.SECONDARY_CONTAINER, 0.16),
                                 fg=theme.SECONDARY)
            self.configure(highlightbackground=theme.BORDER)
        else:
            self.badge.configure(text="LIVE", bg=with_alpha(theme.TERTIARY_CONTAINER, 0.2),
                                 fg=theme.TERTIARY)
            self.configure(highlightbackground=with_alpha(theme.TERTIARY_CONTAINER, 0.7))

    def _draw_flask(self, on: bool):
        colour = theme.SECONDARY if on else theme.TERTIARY
        c = self.icon
        c.create_polygon(13, 4, 21, 4, 21, 12, 27, 24, 7, 24, 13, 12,
                         fill="", outline=colour, width=2)
        c.create_line(10, 18, 24, 18, fill=colour, width=2)

    def _draw_toggle(self):
        c = self.toggle
        c.delete("all")
        on = self.value  # dry run ON == toggle ON (SAFE)
        track = theme.SECONDARY_CONTAINER if on else theme.SURFACE_CONTAINER_HIGHEST
        c.create_rectangle(2, 2, 42, 20, fill=track, width=0)
        x = 31 if on else 13
        c.create_oval(x - 8, 3, x + 8, 19, fill=theme.TEXT_HIGH, width=0)


# ---------------------------------------------------------------------------
# Status ring
# ---------------------------------------------------------------------------


class StatusRing(tk.Canvas):
    """Animated state ring: idle dim, active slow spin, confirmed fast spin.

    States: ``idle``, ``active``, ``confirmed``, ``executing``.
    """

    PERIODS = {"idle": 0, "active": 3000, "confirmed": 500, "executing": 500}

    def __init__(self, master, *, size=120):
        super().__init__(master, width=size, height=size, bg=theme.SURFACE_CONTAINER_LOW,
                         highlightthickness=0)
        self.size = size
        self.state = "idle"
        self._angle = 0
        self._job = None
        self._draw()

    def set_state(self, state: str):
        if state not in self.PERIODS:
            state = "idle"
        self.state = state
        self._schedule()

    def _schedule(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        period = self.PERIODS.get(self.state, 0)
        if period:
            steps = max(2, period // 33)
            self._job = self.after(period // steps, lambda: self._tick(period // steps))

    def _tick(self, delay_ms):
        if self.state in ("idle",):
            self._job = None
            self._draw()
            return
        self._angle = (self._angle + (360 * delay_ms / self.PERIODS[self.state])) % 360
        self._draw()
        self._job = self.after(delay_ms, lambda: self._tick(delay_ms))

    def _draw(self):
        self.delete("all")
        cx = cy = self.size / 2
        r = self.size / 2 - 10
        state = self.state
        base = theme.SURFACE_CONTAINER_HIGHEST
        if state == "idle":
            colour = theme.TEXT_MUTED
        elif state == "active":
            colour = theme.PRIMARY_CONTAINER
        else:
            colour = theme.CRIMSON if state == "executing" else theme.TERTIARY_CONTAINER
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=base, width=6)
        if state == "idle":
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=colour, width=2)
        else:
            extent = 120 if state == "active" else 300
            self.create_arc(
                cx - r, cy - r, cx + r, cy + r, start=-self._angle, extent=extent,
                style="arc", outline=colour, width=6,
            )
        label = {"idle": "IDLE", "active": "ACTIVE", "confirmed": "CONFIRMED",
                 "executing": "EXECUTING"}[state]
        self.create_text(cx, cy, text=label, fill=colour if state != "idle" else theme.TEXT_SECONDARY,
                         font=_mono(9, "bold"))


# ---------------------------------------------------------------------------
# Terminal log
# ---------------------------------------------------------------------------

_LEVEL_STYLES = {
    "info": (None, None),
    "dim": (theme.TEXT_MUTED, None),
    "warn": (theme.TERTIARY, None),
    "error": (theme.ERROR, None),
}


class TerminalLog(tk.Frame):
    """Terminal-style log: mono, coloured levels, auto-scroll lock, copy-all."""

    def __init__(self, master, *, height=10):
        super().__init__(master, bg=theme.SURFACE_LOWEST, bd=0,
                         highlightthickness=1, highlightbackground=theme.BORDER)
        bar = tk.Frame(self, bg=theme.SURFACE_LOWEST)
        bar.pack(fill="x")
        self.title = tk.Label(
            bar, text="EVENT LOG", bg=theme.SURFACE_LOWEST,
            fg=theme.TEXT_MUTED, font=theme.font("label_caps"),
        )
        self.title.pack(side="left", padx=10, pady=4)
        self.autoscroll = True
        self.autoscroll_btn = tk.Label(
            bar, text="🔒", bg=theme.SURFACE_LOWEST, fg=theme.PRIMARY,
            font=("Segoe UI Symbol", 9), cursor="hand2",
        )
        self.autoscroll_btn.pack(side="right", padx=4)
        self.autoscroll_btn.bind("<Button-1>", self._toggle_autoscroll)
        self.copy_btn = tk.Label(
            bar, text="⧉ copy", bg=theme.SURFACE_LOWEST, fg=theme.TEXT_SECONDARY,
            font=_mono(8), cursor="hand2",
        )
        self.copy_btn.pack(side="right", padx=4)
        self.copy_btn.bind("<Button-1>", self._copy_all)

        self.text = tk.Text(
            self, height=height, wrap="word", relief="flat", bd=0,
            bg=theme.SURFACE_LOWEST, fg=theme.TEXT_HIGH,
            insertbackground=theme.PRIMARY, font=_mono(9),
            state="disabled", takefocus=0,
        )
        self.text.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        for level, (fg, _bg) in _LEVEL_STYLES.items():
            if fg is not None:
                self.text.tag_configure(level, foreground=fg)
        self.text.tag_configure("timestamp", foreground=theme.TEXT_MUTED)
        self._fade_top()

    # -- public API -------------------------------------------------------

    def append(self, message: str, level: str = "info") -> None:
        """Add one entry (slides in conceptually; Tk's see() gives motion)."""
        stamp = time.strftime("%H:%M:%S")
        self.text.configure(state="normal")
        self.text.insert("end", f"[{stamp}] ", "timestamp")
        self.text.insert("end", message + "\n", () if level == "info" else level)
        # Bound the pane so an all-night run cannot eat memory.
        end_lines = int(self.text.index("end-1c").split(".")[0])
        if end_lines > 600:
            self.text.delete("1.0", f"{end_lines - 600}.0")
        self.text.configure(state="disabled")
        if self.autoscroll:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # -- internals ---------------------------------------------------------

    def _toggle_autoscroll(self, _event=None):
        self.autoscroll = not self.autoscroll
        self.autoscroll_btn.configure(
            text="🔒" if self.autoscroll else "🔓",
            fg=theme.PRIMARY if self.autoscroll else theme.TEXT_MUTED,
        )

    def _copy_all(self, _event=None):
        content = self.text.get("1.0", "end").rstrip()
        self.clipboard_clear()
        self.clipboard_append(content)
        self.copy_btn.configure(text="✓ copied", fg=theme.SECONDARY)
        self.after(1200, lambda: self.copy_btn.configure(text="⧉ copy", fg=theme.TEXT_SECONDARY))

    def _fade_top(self):
        """Paint a subtle gradient strip over the top edge (fade-to-black)."""
        strip = tk.Canvas(self, height=10, bg=theme.SURFACE_LOWEST,
                          highlightthickness=0)
        strip.place(x=1, y=24, relwidth=1.0)
        for i in range(10):
            shade = blend(theme.SURFACE_LOWEST, theme.SURFACE_CONTAINER_LOW, i / 9)
            strip.create_rectangle(0, i, 2000, i + 1, width=0, fill=shade)


# ---------------------------------------------------------------------------
# Abort pill + toast
# ---------------------------------------------------------------------------


class AbortPill(tk.Canvas):
    """Pinned ``⬡ Abort shutdown`` pill; shake + callback on press."""

    def __init__(self, master, *, on_abort):
        super().__init__(master, width=210, height=44, bg=theme.SURFACE,
                         highlightthickness=1,
                         highlightbackground=with_alpha(theme.CRIMSON, 0.5),
                         cursor="hand2")
        self.on_abort = on_abort
        self._job = None
        self._draw(base=True)
        self.bind("<Button-1>", self._press)
        self.bind("<Enter>", self._hover_on)
        self.bind("<Leave>", self._hover_off)

    def _draw(self, base: bool, offset: int = 0):
        self.delete("all")
        w, h = 210, 44
        fill = with_alpha(theme.CRIMSON, 0.12) if base else theme.CRIMSON
        text = theme.ERROR if base else "#ffffff"
        self.create_rectangle(2 + offset, 2, w - 2 + offset, h - 2, fill=fill, width=0)
        self.create_text(w // 2 + offset, h // 2, text="⬡  Abort shutdown",
                         fill=text, font=theme.font("title"))

    def _hover_on(self, _e):
        self._draw(base=False)
        self.configure(highlightbackground=theme.CRIMSON)

    def _hover_off(self, _e):
        self._draw(base=True)
        self.configure(highlightbackground=with_alpha(theme.CRIMSON, 0.5))

    def _press(self, _event=None):
        self._shake(0)
        self.on_abort()

    def _shake(self, step):
        offsets = [0, -3, 3, -2, 2, 0]
        if step >= len(offsets):
            self._draw(base=False)
            return
        self._draw(base=False, offset=offsets[step])
        self._job = self.after(40, lambda: self._shake(step + 1))


def toast(master, message: str, *, kind: str = "info", duration_ms=1800) -> None:
    """Transient confirmation pill at the bottom-center of ``master``."""
    colour = {
        "info": theme.PRIMARY_CONTAINER,
        "warn": theme.TERTIARY_CONTAINER,
        "error": theme.CRIMSON,
    }.get(kind, theme.PRIMARY_CONTAINER)
    pill = tk.Label(
        master, text=message, bg=with_alpha(colour, 0.9), fg=theme.SURFACE_LOWEST,
        font=_mono(9, "bold"), padx=14, pady=6,
    )
    pill.place(relx=0.5, rely=1.0, anchor="s", y=-18)
    pill.lift()
    master.after(duration_ms, pill.destroy)
