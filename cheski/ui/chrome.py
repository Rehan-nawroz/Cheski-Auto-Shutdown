"""Custom window chrome: borderless frame with an in-app header.

Tk's native title bar cannot be styled, so the app draws its own: a minimal
header (teal ⬡ logomark, app name, version badge, window controls) over a
borderless root window.  Edge-dragging moves the window; every window edge
and corner resizes like a native border (Tk-level bindings — the tiled
``TkChild`` widget windows swallow Win32-level hit-tests, so the resize lives
where the input actually flows, in the bindtag chain).  ``fallback`` keeps
the native title bar when overriding fails (some window managers / remote
sessions refuse ``overrideredirect``).
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

from . import theme

_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_SW_MINIMIZE = 6

#: Thickness in pixels of the draggable resize band along each edge; corners
#: share both bands so a corner drag resizes two edges at once.
_RESIZE_BORDER = 6

#: Hard minimum window size for resizing (matches the old grip's floor).
_MIN_W = 720
_MIN_H = 560

if sys.platform == "win32":
    # Prototyped once: pointer-sized values do not fit ctypes' default ints.
    _user32 = ctypes.WinDLL("user32")
    _user32.GetParent.restype = ctypes.c_void_p
    _user32.GetParent.argtypes = (ctypes.c_void_p,)
    _user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    if hasattr(_user32, "GetWindowLongPtrW"):
        _get_window_long = _user32.GetWindowLongPtrW
        _set_window_long = _user32.SetWindowLongPtrW
    else:  # 32-bit Python: the PtrW exports do not exist there
        _get_window_long = _user32.GetWindowLongW
        _set_window_long = _user32.SetWindowLongW
    _get_window_long.restype = ctypes.c_ssize_t
    _get_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int)
    _set_window_long.restype = ctypes.c_ssize_t
    _set_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
else:  # pragma: no cover - non-Windows development
    _user32 = None

    def _get_window_long(*_a):
        return 0

    def _set_window_long(*_a):
        return 0


class WindowChrome:
    """Attaches a custom header to a root window and hides the native one."""

    def __init__(self, root: tk.Tk, title: str, version: str):
        self.root = root
        self._drag_offset = None
        self._restore = None
        self.overridden = self._try_override()
        self._hwnd = self._resolve_hwnd()
        # Tk-level resize state; see _install_resize_edges.
        self._resize: tuple | None = None
        self._cursor_zone: str | None = None
        if self.overridden:
            # A borderless window has no taskbar button, so an iconified one
            # could never be brought back.  Give it one.
            self._enable_taskbar_entry()
            self._install_resize_edges()

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
        self.min_btn = self._control("–", self._minimize)
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

    def _resolve_hwnd(self) -> int | None:
        """The Win32 wrapper window around Tk's inner window (Windows only)."""
        if _user32 is None:
            return None
        try:
            self.root.update_idletasks()
            hwnd = _user32.GetParent(self.root.winfo_id())
            return int(hwnd) or None
        except Exception:  # pragma: no cover - defensive
            return None

    def _enable_taskbar_entry(self) -> None:
        """Add ``WS_EX_APPWINDOW`` so the borderless window gets a taskbar button."""
        if self._hwnd is None:
            return
        style = _get_window_long(self._hwnd, _GWL_EXSTYLE)
        _set_window_long(self._hwnd, _GWL_EXSTYLE, style | _WS_EX_APPWINDOW)
        # Refresh the mapping so the button shows up immediately.
        self.root.withdraw()
        self.root.deiconify()

    # -- Tk-level edge resize ------------------------------------------------
    #
    # Tk tiles its client area with per-widget child HWNDs, so a Win32
    # WM_NCHITTEST subclass on any single window never sees clicks near the
    # edges.  The bindtag chain does: every widget's event propagates to the
    # toplevel binding unless a widget breaks, so binding on the root window
    # sees presses at every edge exactly the way the header drag does.

    def _install_resize_edges(self) -> None:
        root = self.root
        root.bind("<ButtonPress-1>", self._resize_press, add="+")
        root.bind("<B1-Motion>", self._resize_motion, add="+")
        root.bind("<ButtonRelease-1>", self._resize_release, add="+")
        root.bind("<Motion>", self._resize_hover, add="+")
        root.bind("<Leave>", self._resize_leave, add="+")

    def _zone_at(self, x: int, y: int) -> str | None:
        """Edge zone (``n``/``s``/``e``/``w`` combined) at screen point, if any."""
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        b = _RESIZE_BORDER
        top = y - ry <= b
        bottom = ry + h - y <= b
        left = x - rx <= b
        right = rx + w - x <= b
        zone = ""
        if top:
            zone += "n"
        elif bottom:
            zone += "s"
        if left:
            zone += "w"
        elif right:
            zone += "e"
        return zone or None

    _ZONE_CURSORS = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "nw": "size_nw_se", "se": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
    }

    def _resize_press(self, event) -> None:
        zone = self._zone_at(event.x_root, event.y_root)
        if zone is None:
            return
        self._resize = (
            zone,
            self.root.winfo_width(), self.root.winfo_height(),
            self.root.winfo_rootx(), self.root.winfo_rooty(),
            event.x_root, event.y_root,
        )

    def _resize_motion(self, event) -> None:
        if self._resize is None:
            return
        zone, sw, sh, sx, sy, px, py = self._resize
        dx, dy = event.x_root - px, event.y_root - py
        w, h, x, y = sw, sh, sx, sy
        if "e" in zone:
            w = sw + dx
        if "w" in zone:
            w = sw - dx
            x = sx + dx
        if "s" in zone:
            h = sh + dy
        if "n" in zone:
            h = sh - dy
            y = sy + dy
        w = max(_MIN_W, w)
        h = max(_MIN_H, h)
        # When clamped, keep the opposite edge fixed instead of drifting.
        if "w" in zone and w == _MIN_W:
            x = sx
        if "n" in zone and h == _MIN_H:
            y = sy
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _resize_release(self, _event) -> None:
        self._resize = None
        self._set_zone_cursor(None)

    def _resize_hover(self, event) -> None:
        if self._resize is not None:
            return  # keep the active shape while dragging
        self._set_zone_cursor(self._zone_at(event.x_root, event.y_root))

    def _resize_leave(self, _event) -> None:
        if self._resize is None:
            self._set_zone_cursor(None)

    def _set_zone_cursor(self, zone: str | None) -> None:
        if zone == self._cursor_zone:
            return
        self._cursor_zone = zone
        self.root.configure(cursor=self._ZONE_CURSORS.get(zone, ""))

    # -- header controls -----------------------------------------------------

    def _minimize(self) -> None:
        if self._hwnd is not None:
            _user32.ShowWindow(self._hwnd, _SW_MINIMIZE)
        else:
            self.root.iconify()

    def _drag_start(self, event):
        # The top resize band owns the first few pixels; don't fight it.
        if event.y_root - self.root.winfo_rooty() <= _RESIZE_BORDER:
            self._drag_offset = None
            return
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
        # Run the same handler the native WM_DELETE_WINDOW protocol runs, so a
        # close through the header behaves exactly like a close through a
        # native title bar (CheskiApp._on_close aborts a pending shutdown).
        # event_generate cannot be used: WM_DELETE_WINDOW is a protocol, not
        # a synthetic-able event.
        handler = self.root.protocol("WM_DELETE_WINDOW")
        if handler:
            self.root.tk.call(handler)
        else:
            self.root.destroy()


def with_alpha_badge() -> str:
    return theme.with_alpha(theme.PRIMARY_CONTAINER, 0.14)
