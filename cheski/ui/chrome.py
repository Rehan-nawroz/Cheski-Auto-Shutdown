"""Custom window chrome: borderless frame with an in-app header.

Tk's native title bar cannot be styled, so the app draws its own: a minimal
header (teal ⬡ logomark, app name, version badge, window controls) over a
borderless root window.  Edge-dragging moves the window; the bottom-right
grip resizes it.  ``fallback`` keeps the native title bar when overriding
fails (some window managers / remote sessions refuse ``overrideredirect``).
"""

from __future__ import annotations

import tkinter as tk

from . import theme


class WindowChrome:
    """Attaches a custom header to a root window and hides the native one."""

    def __init__(self, root: tk.Tk, title: str, version: str):
        self.root = root
        self._drag_offset = None
        self._restore = None
        self.overridden = self._try_override()

        self.header = tk.Frame(
            root, bg=theme.SURFACE_CONTAINER_LOW,
            highlightthickness=1, highlightbackground=theme.HEADER_BORDER,
        )
        if self.overridden:
            self.header.pack(fill="x", side="top")

        mark = tk.Canvas(self.header, width=22, height=22,
                         bg=theme.SURFACE_CONTAINER_LOW, highlightthickness=0)
        mark.pack(side="left", padx=(12, 8), pady=5)
        mark.create_polygon(11, 2, 19, 7, 19, 15, 11, 20, 3, 15, 3, 7,
                            outline=theme.PRIMARY, width=2, fill="")
        mark.create_polygon(11, 7, 15, 9.5, 15, 12.5, 11, 15, 7, 12.5, 7, 9.5,
                            outline=theme.SECONDARY, width=1, fill="")

        name = tk.Label(
            self.header, text=title, bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.TEXT_HIGH, font=theme.font("body"),
        )
        name.pack(side="left")
        badge = tk.Label(
            self.header, text=f"v{version}", bg=with_alpha_badge(),
            fg=theme.PRIMARY, font=(theme.mono_family(), 8, "bold"), padx=6, pady=1,
        )
        badge.pack(side="left", padx=(8, 0))

        self.controls = tk.Frame(self.header, bg=theme.SURFACE_CONTAINER_LOW)
        self.controls.pack(side="right")
        self.min_btn = self._control("–", root.iconify)
        self.max_btn = self._control("□", self._toggle_max)
        self.close_btn = self._control("✕", self._close, danger=True)

        if self.overridden:
            for widget in (self.header, mark, name, badge, self.controls):
                widget.bind("<ButtonPress-1>", self._drag_start, add="+")
                widget.bind("<B1-Motion>", self._drag_move, add="+")
            root.geometry("960x720+80+60")
            root.attributes("-topmost", False)
            root.configure(bg=theme.SURFACE)

    # -- setup helpers -----------------------------------------------------

    def _try_override(self) -> bool:
        try:
            root = self.root
            root.overrideredirect(True)
            root.update_idletasks()
            if not root.winfo_viewable():
                # Withdrawing + deiconifying forces the WM to honour the flag.
                root.withdraw()
                root.deiconify()
                root.update_idletasks()
            return True
        except tk.TclError:
            self.root.overrideredirect(False)
            return False

    def _control(self, glyph: str, command, danger: bool = False) -> tk.Label:
        btn = tk.Label(
            self.controls, text=glyph, width=3, pady=4,
            bg=theme.SURFACE_CONTAINER_LOW,
            fg=theme.CRIMSON if danger else theme.TEXT_SECONDARY,
            font=("Segoe UI Symbol", 9), cursor="hand2",
        )
        btn.pack(side="left")
        btn.bind("<Button-1>", lambda _e: command())
        if danger:
            btn.bind("<Enter>", lambda _e: btn.configure(bg=theme.ERROR_CONTAINER))
            btn.bind("<Leave>", lambda _e: btn.configure(bg=theme.SURFACE_CONTAINER_LOW))
        else:
            btn.bind("<Enter>", lambda _e: btn.configure(bg=theme.SURFACE_CONTAINER_HIGH))
            btn.bind("<Leave>", lambda _e: btn.configure(bg=theme.SURFACE_CONTAINER_LOW))
        return btn

    # -- behaviours ----------------------------------------------------------

    def _drag_start(self, event):
        self._drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        if self._drag_offset is None:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def _toggle_max(self):
        if self._restore is None:
            self._restore = self.root.geometry()
            self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
        else:
            self.root.geometry(self._restore)
            self._restore = None

    def _close(self):
        self.root.event_generate("<WM_DELETE_WINDOW>")
        try:
            self.root.protocol("WM_DELETE_WINDOW")()  # call the registered handler
        except TypeError:
            self.root.destroy()


def with_alpha_badge() -> str:
    return theme.with_alpha(theme.PRIMARY_CONTAINER, 0.14)


def add_resize_grip(root: tk.Tk) -> tk.Canvas | None:
    """Bottom-right drag grip for the borderless window (None if native)."""
    if not root.overrideredirect():
        return None
    grip = tk.Canvas(root, width=16, height=16, bg=theme.SURFACE,
                     highlightthickness=0, cursor="size_nw_se")
    grip.place(relx=1.0, rely=1.0, anchor="se")
    grip.create_polygon(16, 6, 16, 16, 6, 16, fill=theme.SURFACE_CONTAINER_HIGHEST)
    grip.create_polygon(16, 11, 16, 16, 11, 16, fill=theme.TEXT_MUTED)
    state = {"start": None}

    def press(event):
        state["start"] = (root.winfo_width(), root.winfo_height(), event.x_root, event.y_root)

    def move(event):
        if state["start"] is None:
            return
        w0, h0, x0, y0 = state["start"]
        root.geometry(f"{max(720, w0 + event.x_root - x0)}x{max(560, h0 + event.y_root - y0)}")

    grip.bind("<ButtonPress-1>", press)
    grip.bind("<B1-Motion>", move)
    return grip
