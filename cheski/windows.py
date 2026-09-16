"""Window discovery and target identity.

Why this module exists in this shape
------------------------------------
The obvious design -- "let the user pick a window by its title, then watch that
title" -- is self-defeating, because the title is exactly the thing that
changes.  A target is therefore bound to its **window handle (HWND)**, which
stays stable for the lifetime of the window, and is only re-resolved by
process name when the handle dies (apps like Steam recreate windows, and a
recreated window means a new handle).

Enumeration uses ``pygetwindow``.  Two extra things are needed that it does not
expose, and both are done with ``ctypes`` against ``user32``/``kernel32`` so no
``pywin32``/``psutil`` dependency is required:

* reading the title of *one* known handle (cheap per poll, versus enumerating
  every window on the desktop every 2 seconds);
* ``HWND -> PID -> process name``, used to re-attach to a lost window and to
  tell the user precisely which program they are watching.

Everything here is read-only: it never closes, moves or focuses a window.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import sys
from dataclasses import dataclass, replace
from typing import Iterable, Protocol, Sequence

log = logging.getLogger("cheski.windows")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_GETWINDOWTEXT_MAX = 1024

#: Windows that are never useful targets: shell furniture and placeholder
#: titles.  Matched case-insensitively against the stripped title.
IGNORED_TITLES = frozenset(
    {
        "program manager",
        "microsoft text input application",
        "windows input experience",
        "default ime",
        "n/a",
    }
)


@dataclass(frozen=True)
class WindowInfo:
    """A snapshot of one top-level window."""

    handle: int
    title: str
    pid: int | None = None
    process: str | None = None

    @property
    def label(self) -> str:
        """Human-readable dropdown entry: ``Title -- process.exe``."""
        base = " ".join((self.title or "").split()) or "(untitled window)"
        if self.process:
            return f"{base} -- {self.process}"
        if self.pid:
            return f"{base} -- pid {self.pid}"
        return base


# ---------------------------------------------------------------------------
# ctypes helpers (all best-effort: they return None/False instead of raising)
# ---------------------------------------------------------------------------

_user32: ctypes.WinDLL | None = None
_kernel32: ctypes.WinDLL | None = None


def _load_win32() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    """Load and prototype the two DLLs once, lazily."""
    global _user32, _kernel32
    if _user32 is None or _kernel32 is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL

        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int

        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int

        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE

        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        _user32, _kernel32 = user32, kernel32
    return _user32, _kernel32


def is_window_alive(handle: int) -> bool:
    """True if ``handle`` still refers to an existing window."""
    if sys.platform != "win32" or not handle:
        return False
    try:
        user32, _ = _load_win32()
        return bool(user32.IsWindow(wintypes.HWND(int(handle))))
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("IsWindow(%s) failed: %s", handle, exc)
        return False


def window_title(handle: int) -> str | None:
    """Return the current title of one handle.

    ``None`` means "the window is gone" -- which callers must treat as
    *not a finished download*, never as a trigger.  An empty string means the
    window exists but has no caption.
    """
    if sys.platform != "win32" or not handle:
        return None
    try:
        user32, _ = _load_win32()
        hwnd = wintypes.HWND(int(handle))
        if not user32.IsWindow(hwnd):
            return None
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(min(length + 1, _GETWINDOWTEXT_MAX))
        copied = int(user32.GetWindowTextW(hwnd, buffer, len(buffer)))
        if copied <= 0:
            return ""
        return buffer.value
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("GetWindowTextW(%s) failed: %s", handle, exc)
        return None


def process_name_for_window(handle: int) -> tuple[int | None, str | None]:
    """Return ``(pid, exe_name)`` for a window handle, best-effort.

    UWP apps report their host process (``ApplicationFrameHost.exe``) rather
    than the real app; that is a known Windows limitation, not a bug here.
    """
    if sys.platform != "win32" or not handle:
        return (None, None)
    try:
        user32, kernel32 = _load_win32()
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(int(handle)), ctypes.byref(pid))
        if not pid.value:
            return (None, None)
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not process:
            return (pid.value, None)
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buffer))
            ok = kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size))
            if not ok or not buffer.value:
                return (pid.value, None)
            return (pid.value, buffer.value.rsplit("\\", 1)[-1])
        finally:
            kernel32.CloseHandle(process)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("process lookup for %s failed: %s", handle, exc)
        return (None, None)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class WindowSource(Protocol):
    """Anything that can list windows and read one title."""

    def list_windows(self) -> list[WindowInfo]:
        ...

    def get_title(self, handle: int) -> str | None:
        ...


def filter_windows(infos: Iterable[WindowInfo]) -> list[WindowInfo]:
    """Drop junk titles, de-duplicate handles, sort by title.

    Shared by the real and fake sources so the filtering rules themselves are
    covered by tests without needing a desktop full of windows.
    """
    seen: set[int] = set()
    out: list[WindowInfo] = []
    for info in infos:
        handle = int(getattr(info, "handle", 0) or 0)
        title = " ".join((getattr(info, "title", "") or "").split())
        if not handle or handle in seen:
            continue
        if not title or title.casefold() in IGNORED_TITLES:
            continue
        seen.add(handle)
        out.append(replace(info, handle=handle, title=title))
    out.sort(key=lambda item: (item.title.casefold(), item.handle))
    return out


class PyGetWindowSource:
    """Real desktop source backed by ``pygetwindow`` + ``ctypes``."""

    name = "pygetwindow"

    def list_windows(self) -> list[WindowInfo]:
        try:
            import pygetwindow as gw
        except Exception as exc:  # pragma: no cover - import guard
            log.error("pygetwindow is unavailable: %s", exc)
            return []
        infos: list[WindowInfo] = []
        for window in gw.getAllWindows():
            handle = int(getattr(window, "_hWnd", 0) or 0)
            if not handle:
                continue
            pid, process = process_name_for_window(handle)
            infos.append(
                WindowInfo(
                    handle=handle,
                    title=getattr(window, "title", "") or "",
                    pid=pid,
                    process=process,
                )
            )
        return filter_windows(infos)

    def get_title(self, handle: int) -> str | None:
        return window_title(handle)


class FakeWindowSource:
    """In-memory source for tests and for the title simulator's own tests."""

    name = "fake"

    def __init__(self, windows: Sequence[WindowInfo] = (), *, default_title: str | None = None) -> None:
        self.windows: list[WindowInfo] = list(windows)
        self.default_title = default_title
        self.list_calls = 0
        self.title_calls = 0

    def list_windows(self) -> list[WindowInfo]:
        self.list_calls += 1
        return filter_windows(self.windows)

    def get_title(self, handle: int) -> str | None:
        self.title_calls += 1
        for info in self.windows:
            if info.handle == handle:
                return info.title
        return self.default_title

    # Test helpers -------------------------------------------------------
    def set_title(self, handle: int, title: str) -> None:
        self.windows = [
            replace(info, title=title) if info.handle == handle else info for info in self.windows
        ]

    def remove(self, handle: int) -> None:
        self.windows = [info for info in self.windows if info.handle != handle]

    def add(self, info: WindowInfo) -> None:
        self.windows.append(info)


def resolve_target(source: WindowSource, target: WindowInfo) -> WindowInfo | None:
    """Return the live window for ``target``.

    If the recorded handle still exists its title is re-read (titles change
    constantly).  If it is gone, look for a replacement belonging to the same
    process -- and failing that, the same PID -- preferring the window with the
    most informative title.  ``None`` means the target is genuinely lost, and
    the caller must not treat that as a completed download.
    """
    if target.handle and is_window_alive(target.handle):
        title = source.get_title(target.handle)
        if title is not None:
            return replace(target, title=title)

    own_pid = os.getpid()
    candidates: list[tuple[int, WindowInfo]] = []
    for info in source.list_windows():
        if info.pid and info.pid == own_pid:
            # Never re-attach to one of our own windows.  Matching on process
            # name alone would happily pick the application's own window (a
            # downloader can share an interpreter with this tool) and then sit
            # all night watching the wrong thing.
            continue
        if target.pid and info.pid and info.pid == target.pid:
            candidates.append((0, info))
        elif (
            target.process
            and info.process
            and info.process.casefold() == target.process.casefold()
        ):
            candidates.append((1, info))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], -len(pair[1].title)))
    return candidates[0][1]
