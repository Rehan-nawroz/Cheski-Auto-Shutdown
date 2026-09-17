"""Per-process window icons: real where Windows provides them, fallbacks where not.

``extract_icon(pid)`` walks ``HWND → WM_GETICON/GetClassLongPtr → module path
→ SHGetFileInfoW`` and decodes the resulting HICON with Pillow into a
``PIL.Image``.  Everything is best-effort: any failure returns ``None`` and the
caller falls back to :func:`fallback_avatar` (a deterministic geometric avatar
hued by process-name hash, cached and LRU-bounded like the real ones).

Results are cached by process name (icons do not vary per window) with a small
LRU cap so an all-night run cannot accumulate them.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import logging
import math
import os
import sys
from collections import OrderedDict

log = logging.getLogger("cheski.icons")

_ICON_SIZE = 32
_CACHE_LIMIT = 128

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_GETICON = 0x007F
ICON_SMALL = 0
ICON_BIG = 1
GCLP_HICON = -14
GCLP_HICONSM = -34
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001

kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL

_IMAGE_CACHE: "OrderedDict[tuple, object]" = OrderedDict()


def _cache_put(key, value):
    _IMAGE_CACHE[key] = value
    _IMAGE_CACHE.move_to_end(key)
    while len(_IMAGE_CACHE) > _CACHE_LIMIT:
        _IMAGE_CACHE.popitem(last=False)


def module_path_for_pid(pid: int) -> str | None:
    """Best-effort executable path for a PID (needs no elevation)."""
    if not pid:
        return None
    try:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value or None
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("module path for pid %s failed: %s", pid, exc)
    return None


def _hicon_to_image(hicon: int):
    """Decode an HICON to a PIL RGB image (best-effort, None on failure)."""
    from PIL import Image, ImageDraw

    try:
        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", wt.BOOL),
                ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP),
            ]

        info = ICONINFO()
        user32.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
        user32.GetIconInfo.restype = wt.BOOL
        if not user32.GetIconInfo(wt.HICON(hicon), ctypes.byref(info)):
            return None
        try:
            bmp = info.hbmColor
            if not bmp:
                return None

            class BITMAP(ctypes.Structure):
                _fields_ = [
                    ("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                    ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                    ("bmBits", ctypes.c_void_p),
                ]

            bmp_struct = BITMAP()
            gdi32 = ctypes.WinDLL("gdi32")
            gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
            gdi32.GetObjectW.restype = ctypes.c_int
            if not gdi32.GetObjectW(wt.HANDLE(bmp), ctypes.sizeof(bmp_struct), ctypes.byref(bmp_struct)):
                return None
            width, height = bmp_struct.bmWidth, abs(bmp_struct.bmHeight)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

            bmi = BITMAPINFO()
            bmi.bmiHeader = BITMAPINFOHEADER(
                ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0
            )
            gdi32.GetDIBits.restype = ctypes.c_int
            gdi32.GetDIBits.argtypes = [wt.HANDLE, wt.HANDLE, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
            buf = ctypes.create_string_buffer(width * height * 4)
            screen = user32.GetDC(0)
            try:
                if not gdi32.GetDIBits(wt.HANDLE(screen), wt.HANDLE(bmp), 0, height, buf, ctypes.byref(bmi), 0):
                    return None
            finally:
                user32.ReleaseDC(0, screen)
            img = Image.frombuffer("RGBA", (width, height), buf.raw, "raw", "BGRA", 0, 1)
            # composite over an opaque checker-free dark base so PNG-ish alpha shows
            base = Image.new("RGB", img.size, (12, 14, 18))
            base.paste(img, mask=img.split()[3])
            return base.resize((_ICON_SIZE, _ICON_SIZE))
        finally:
            gdi32 = ctypes.WinDLL("gdi32")
            if info.hbmMask:
                gdi32.DeleteObject(wt.HANDLE(info.hbmMask))
            if info.hbmColor:
                gdi32.DeleteObject(wt.HANDLE(info.hbmColor))
    except Exception as exc:  # pragma: no cover - best effort only
        log.debug("hicon decode failed: %s", exc)
        return None


def _window_handle_for_pid(pid: int) -> int:
    """Any visible top-level window of ``pid`` (cheapEnumWindows walk)."""
    if not pid:
        return 0
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, _lparam):
        pid_out = wt.DWORD(0)
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowThreadProcessId.restype = wt.DWORD
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
        if pid_out.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    try:
        user32.EnumWindows(callback, 0)
    except Exception:  # pragma: no cover - defensive
        return 0
    return found[0] if found else 0


def extract_icon(pid: int):
    """PIL image of the process's window icon, or ``None`` (best-effort)."""
    if sys.platform != "win32" or not pid:
        return None
    key = ("icon", pid)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    try:
        image = _extract_icon_uncached(pid)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("icon extraction for pid %s failed: %s", pid, exc)
        image = None
    _cache_put(key, image)
    return image


def _extract_icon_uncached(pid: int):
    hwnd = _window_handle_for_pid(pid)
    hicon = 0
    if hwnd:
        user32.SendMessageTimeoutW.restype = ctypes.c_void_p  # LRESULT
        for wParam in (ICON_SMALL, ICON_BIG):
            result = user32.SendMessageTimeoutW(
                wt.HWND(hwnd), WM_GETICON, wt.WPARAM(wParam), wt.LPARAM(0),
                wt.UINT(0x0002), wt.UINT(120), ctypes.byref(ctypes.c_void_p(0)),
            ) if False else user32.SendMessageW(wt.HWND(hwnd), WM_GETICON, wt.WPARAM(wParam), wt.LPARAM(0))
            hicon = int(result & 0xFFFFFFFF) if result else 0
            if hicon:
                break
        if not hicon:
            gcl = user32.GetClassLongPtrW(hwnd, GCLP_HICONSM)
            hicon = int(gcl or 0)
    if not hicon:
        exe = module_path_for_pid(pid)
        if exe:
            class SHFILEINFO(ctypes.Structure):
                _fields_ = [
                    ("hIcon", wt.HICON), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wt.DWORD), ("szDisplayName", wt.WCHAR * 260),
                    ("szTypeName", wt.WCHAR * 80),
                ]

            sfi = SHFILEINFO()
            ok = shell32.SHGetFileInfoW(
                ctypes.c_wchar_p(exe), 0, ctypes.byref(sfi), ctypes.sizeof(SHFILEINFO),
                SHGFI_ICON | SHGFI_LARGEICON,
            )
            if ok and sfi.hIcon:
                hicon = int(sfi.hIcon)
    if not hicon:
        return None
    image = _hicon_to_image(hicon)
    try:
        user32.DestroyIcon(wt.HICON(hicon))
    except Exception:  # pragma: no cover - defensive
        pass
    return image


def fallback_avatar(process: str | None, size: int = _ICON_SIZE):
    """Deterministic geometric avatar for processes without an icon."""
    from PIL import Image, ImageDraw

    key = ("avatar", process, size)
    if key in _IMAGE_CACHE:
        return _IMAGE_CACHE[key]
    seed = int(hashlib.sha256((process or "?").encode("utf-8")).hexdigest()[:8], 16)
    hue = seed % 360
    img = Image.new("RGB", (size, size), (18, 20, 24))
    draw = ImageDraw.Draw(img)
    base = _hsv_to_rgb(hue, 0.45, 0.55)
    dim = _hsv_to_rgb(hue, 0.45, 0.35)
    draw.ellipse([1, 1, size - 2, size - 2], outline=base, width=2)
    sides = 3 + (seed >> 8) % 4  # triangle..hexagon
    cx = cy = size / 2
    r = size * 0.32
    pts = []
    for i in range(sides):
        angle = math.pi * 2 * i / sides - math.pi / 2
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, outline=dim, width=2)
    _cache_put(key, img)
    return img


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h / 60) % 6
    f = h / 60 - int(h / 60)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return (int(r * 255), int(g * 255), int(b * 255))


def clear_cache() -> None:
    """Test hook: drop every cached image."""
    _IMAGE_CACHE.clear()
